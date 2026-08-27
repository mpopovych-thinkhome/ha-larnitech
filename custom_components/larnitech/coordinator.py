# Updated: 2026-08-21 18:05
"""Data coordinator: decoded events applied directly; full get-devices as safety.

The full snapshot also drives reconciliation: add/remove devices, react to a
device changing type, and sync names / rooms per the entry's toggles."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import LarnitechClient, LarnitechError
from .const import (
    CONF_AUTO_REMOVE,
    CONF_ENTITY_ID_PATTERN,
    CONF_NAME_SUFFIX_ADDR,
    CONF_READ_ONLY,
    CONF_SCAN_INTERVAL,
    CONF_UPDATE_AREAS,
    CONF_UPDATE_NAMES,
    CONF_USE_AREAS,
    DEFAULT_ENTITY_ID_PATTERN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MASS_REMOVAL_RATIO,
    MISSING_SNAPSHOTS_BEFORE_REMOVE,
    device_display_name,
    device_slug,
    entity_object_id,
    hub_slug,
    toggle,
    unhandled_reason,
)

_LOGGER = logging.getLogger(__name__)

# Debounce for the rare full refresh (reconnect / unknown-addr fallback).
_REFRESH_COOLDOWN = 2.0

# Log (never act) once a run of failed polls has left the object stale this
# long. `async_get_devices` already times out well under this (15-20s), so
# reaching it means several polls failed in a row — not a single slow one.
# Deliberately log-only: an integration-side watchdog can't reliably act on
# the case that actually matters (the whole HA event loop wedged, as seen
# live 2026-08-20 on a stuck core restart) — if the loop is that stuck, this
# code doesn't get to run either. Auto-restarting HA core from here would
# also risk a restart loop on a merely-flaky connection. A human decides.
_STALE_THRESHOLD = 60

# Climate setpoints are a group that must be REPLACED, not merged. Larnitech
# only sends the setpoints the active automation actually uses (a
# cooling-only zone sends `setpoint-cool` and no `setpoint-heat` at all), so
# a plain merge keeps a setpoint that no longer exists — HA went on showing a
# heat setpoint for a zone switched to cooling only. Any status update
# carrying at least one of these keys defines the complete current set;
# the others are dropped.
_SETPOINT_KEYS = ("setpoint", "setpoint-heat", "setpoint-cool")


def dev_kind(dev: dict) -> str:
    """`type/sub-type` — the pair that decides which platform owns an addr."""
    sub = dev.get("sub-type")
    return f"{dev.get('type')}/{sub}" if sub else str(dev.get("type"))


def _model_type(model: str | None) -> str | None:
    """The type half of a device's `model`. Handles both the current format
    ("type" / "type (sub-type)") and the pre-2026-08-21 one ("type, addr") —
    every device already in the registry still carries the old format until
    its entities are next recreated, so parsing only the new one would read
    every untouched device as "type changed" on this deploy's first reconcile
    and mass-recreate the lot. Drop this once no live registry can still hold
    the old format (i.e. never — safe to leave)."""
    if not model:
        return None
    for sep in (" (", ", "):
        if sep in model:
            return model.split(sep, 1)[0]
    return model


def merge_status(old: dict, new: dict) -> dict:
    """Merge a status update, replacing the setpoint group wholesale."""
    merged = {**old, **new}
    if any(key in new for key in _SETPOINT_KEYS):
        for key in _SETPOINT_KEYS:
            if key not in new:
                merged.pop(key, None)
    return merged


class LarnitechCoordinator(DataUpdateCoordinator):
    """Holds the device registry keyed by addr; reconciles HA on full snapshots."""

    def __init__(self, hass, client: LarnitechClient, entry):
        scan_interval = entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=_REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.client = client
        self.entry = entry
        self._missing: dict[str, int] = {}
        # (type, sub-type) pairs already reported as unmapped — log each once.
        self._logged_unmapped: set = set()
        # Addrs whose status arrived in the push event currently being
        # delivered to listeners; empty for a poll-driven update. Momentary
        # entities (buttons) fire only when their addr is in here: HA runs
        # EVERY listener on EVERY coordinator update, so without this a
        # button re-reads its own last `hex` — which `merge_status` never
        # clears — and re-fires on each poll and on every other device's push.
        self.event_addrs: frozenset[str] = frozenset()
        self._last_success = time.monotonic()
        self._stale_logged = False
        # Mass-removal guard: latched state + the live set it would act on.
        self._mass_latched = False
        self._mass_missing: list[str] = []
        # False until the platforms finish their first pass, so entity
        # creation logs at DEBUG during setup (every entity would log) and at
        # INFO afterwards, where it means a device genuinely appeared.
        self.setup_complete = False

        # Resolved toggles (options override data, default ON).
        self.auto_remove = toggle(entry, CONF_AUTO_REMOVE)
        self.update_names = toggle(entry, CONF_UPDATE_NAMES)
        self.use_areas = toggle(entry, CONF_USE_AREAS)
        self.update_areas = toggle(entry, CONF_UPDATE_AREAS)
        self.read_only = toggle(entry, CONF_READ_ONLY)
        self.name_suffix_addr = toggle(entry, CONF_NAME_SUFFIX_ADDR)
        self.scan_interval = scan_interval
        # Frozen at setup — never read from options (see const.py).
        self.entity_id_pattern = entry.data.get(
            CONF_ENTITY_ID_PATTERN, DEFAULT_ENTITY_ID_PATTERN
        )

    async def _async_update_data(self) -> dict[str, dict]:
        # A poll is not a device event — see `event_addrs`.
        self.event_addrs = frozenset()
        try:
            devices = await self.client.async_get_devices()
        except LarnitechError as err:
            self._check_stale()
            raise UpdateFailed(str(err)) from err
        self._last_success = time.monotonic()
        self._stale_logged = False
        data = {d["addr"]: d for d in devices if "addr" in d}
        # An empty snapshot is never a real "the controller has no devices" —
        # `async_get_devices` returns [] for a malformed/`devices`-less reply
        # just as it would for a genuinely empty one. Treating it as truth
        # feeds `_reconcile` an all-missing snapshot, and two of those in a
        # row make `auto_remove` delete EVERY device from the HA registry
        # (taking their entities, and any dashboard/automation references to
        # them, with it). Fail the update instead: entities go unavailable
        # and recover on the next good poll.
        if not data and self.data:
            raise UpdateFailed("get-devices returned an empty snapshot — ignoring")
        self._log_unmapped(data)
        # Cause before effect: this logs what the controller changed, the
        # reconcile below logs what HA did about it.
        self._log_snapshot_changes(data)
        self._reconcile(data)
        return data

    def _check_stale(self) -> None:
        elapsed = time.monotonic() - self._last_success
        if elapsed >= _STALE_THRESHOLD and not self._stale_logged:
            self._stale_logged = True
            _LOGGER.error(
                "Larnitech %s: no successful update for %.0fs (last error: %s) — "
                "object may be unreachable, or HA itself may be under load",
                self.entry.title,
                elapsed,
                self.last_exception,
            )

    @callback
    def _log_snapshot_changes(self, data: dict[str, dict]) -> None:
        """Log what the CONTROLLER started or stopped reporting, and any addr
        whose type changed.

        Entities are never added or dropped for any other reason — the
        platforms act purely on this set — so these lines are the "why"
        behind a device appearing or vanishing in HA, and they pair with the
        removal warnings in `_reconcile` to give the full story without
        turning on debug logging."""
        old = self.data
        if old is None:
            return  # initial load: every addr would log as new

        for addr in sorted(data.keys() - old.keys()):
            dev = data[addr]
            _LOGGER.info(
                "%s: device appeared at %s (%s, %r) — entities are created for it now",
                self.entry.title, addr, dev_kind(dev), dev.get("name"),
            )
        for addr in sorted(old.keys() - data.keys()):
            dev = old[addr]
            _LOGGER.warning(
                "%s: device no longer reported at %s (%s, %r) — its entities go "
                "unavailable, and it is removed once absent from %s consecutive snapshots",
                self.entry.title, addr, dev_kind(dev), dev.get("name"),
                MISSING_SNAPSHOTS_BEFORE_REMOVE,
            )
        for addr in sorted(data.keys() & old.keys()):
            before, after = dev_kind(old[addr]), dev_kind(data[addr])
            if before == after:
                continue
            # Type or sub-type changed — `_reconcile` (below) drops the device
            # so it gets recreated on the right platform. This line is purely
            # the "why", logged before that action's own line.
            _LOGGER.warning(
                "%s: device at %s changed kind: %s -> %s (%r)",
                self.entry.title, addr, before, after, data[addr].get("name"),
            )

    @callback
    def _log_unmapped(self, data: dict[str, dict]) -> None:
        """Surface devices that map to no platform, with the raw payload, so an
        unknown type or sub-type can be added to the mapping later."""
        for addr, dev in data.items():
            reason = unhandled_reason(dev)
            if reason is None:
                continue
            key = (dev.get("type"), dev.get("sub-type"))
            if key in self._logged_unmapped:
                continue
            self._logged_unmapped.add(key)
            _LOGGER.warning(
                "Larnitech device not mapped to a platform (%s) at %s — raw: %s",
                reason,
                addr,
                dev,
            )

    @callback
    def apply_events(self, devices: list[dict]) -> None:
        """Merge decoded push events into state without a full re-read.

        Events are partial (only changed keys) and never used for add/remove —
        a missing addr in an event does not mean the device is gone. A single
        device in the batch with a hex status or unknown addr triggers a full
        refresh for itself, but must not discard the other devices in the
        same batch — those are applied immediately regardless."""
        if self.data is None:
            return

        updated = dict(self.data)
        pushed: set[str] = set()
        need_full = False
        verify: list[str] = []
        for dev in devices:
            addr = dev.get("addr")
            status = dev.get("status")
            if addr is None:
                continue
            if not isinstance(status, dict):
                _LOGGER.debug(
                    "apply_events: non-dict status for %s: %r — full refresh", addr, status
                )
                need_full = True
                continue
            if addr not in updated:
                _LOGGER.debug(
                    "apply_events: event for unknown addr %s: %r — full refresh", addr, dev
                )
                need_full = True
                continue
            merged = dict(updated[addr])
            merged["status"] = merge_status(merged.get("status", {}), status)
            updated[addr] = merged
            pushed.add(addr)
            # A preset switch can change which setpoints exist at all — and an
            # event only carries what changed, so "no setpoint key" here is
            # ambiguous (unchanged, or gone with the old automation?). Re-read
            # the device for the unambiguous full status.
            if "automation" in status:
                verify.append(addr)

        if pushed:
            # Listeners run synchronously inside async_set_updated_data, so
            # this window is exactly the push delivery and nothing else.
            self.event_addrs = frozenset(pushed)
            self.async_set_updated_data(updated)
            self.event_addrs = frozenset()
        if need_full:
            self.hass.async_create_task(self.async_request_refresh())
        for addr in verify:
            self.hass.async_create_task(self.async_refresh_addr(addr))

    async def async_refresh_addr(self, addr: str) -> None:
        """Point-verify one device after a write — the controller doesn't
        reliably push a `statuses` event for every write (observed live:
        sometimes silent, especially for masked-capability climate writes),
        so the write path calls this instead of trusting `status-set`'s
        `success` blindly. Cheaper than a full `get-devices` refresh and,
        called by the caller after a short delay, avoids racing the
        controller's own internal state settling (an immediate re-read can
        land the pre-write value back into HA).

        `status-get`'s response is a thin `{addr, type, status}` shape — no
        `name`/`area`/`modes`/`vane-hor`/`automations`/... (confirmed live
        2026-08-17), so only the `status` key is taken from it: replacing the
        whole device dict wiped those device-level keys on every write and
        silently broke anything that reads them (e.g. AC's
        `swing_horizontal_modes`, whose default is "none" — losing the mask
        made the whole feature disappear post-write).

        `status` itself IS complete here, unlike a push event — so it
        REPLACES the stored status rather than merging into it. A key absent
        from a full status means the device genuinely doesn't have it, and
        merging would resurrect it: a zone whose automation has no setpoint
        at all sends no setpoint key, and HA went on showing the setpoint
        from the previous automation."""
        if self.data is None or addr not in self.data:
            return
        try:
            device = await self.client.async_get_device_status(addr)
        except LarnitechError as err:
            _LOGGER.debug("Larnitech: post-write refresh of %s failed: %s", addr, err)
            return
        if device is None or not isinstance(device.get("status"), dict):
            return
        updated = dict(self.data)
        merged = dict(updated[addr])
        merged["status"] = device["status"]
        updated[addr] = merged
        self.async_set_updated_data(updated)

    # --- reconciliation (full snapshot only) -----------------------------

    @callback
    def _reconcile(self, data: dict[str, dict]) -> None:
        dev_reg = dr.async_get(self.hass)
        serial = self.client.serial
        snapshot = {device_slug(serial, addr): dev for addr, dev in data.items()}

        # The controller's own device is not a widget — it never appears in a
        # get-devices snapshot, so it must be excluded from both the
        # missing-device accounting and the mass-removal ratio.
        hub = hub_slug(serial)
        slugged = []
        for device in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id):
            slug = next((i[1] for i in device.identifiers if i[0] == DOMAIN), None)
            if slug is not None and slug != hub:
                slugged.append((device, slug))

        missing_slugs = [slug for _, slug in slugged if slug not in snapshot]
        mass_removal = self._check_mass_removal(len(slugged), missing_slugs)

        retype = False
        for device, slug in slugged:
            dev = snapshot.get(slug)

            if dev is None:
                if not mass_removal:
                    self._handle_missing(dev_reg, device, slug)
                continue
            self._missing.pop(slug, None)

            # Type OR sub-type changed (e.g. lamp -> thermostat, or
            # lamp/socket -> lamp/air-fan): drop so the right platform
            # recreates it. `model` is "<type>" or "<type> (<sub-type>)" (see
            # entity.py) — compare only the type half, or a sub-type-only
            # device (e.g. "lamp (socket)") never matches its own past value.
            # `sw_version` holds the sub-type the same way.
            #
            # `old_subtype is None` also means "never recorded" (e.g. right
            # after this check was added — every existing device's sw_version
            # is unset until its entities are next created). Requiring a prior
            # recorded value before comparing avoids mistaking that gap for a
            # sub-type change and mass-recreating everything on the first
            # reconcile after an upgrade — see TODO.md "Safety" for why a
            # false-positive mass action here is the exact failure mode this
            # whole file guards against.
            new_type = dev.get("type")
            new_subtype = dev.get("sub-type")
            old_type = _model_type(device.model)
            old_subtype = device.sw_version or None
            type_changed = bool(old_type and new_type and old_type != new_type)
            subtype_changed = (
                not type_changed
                and old_subtype is not None
                and old_subtype != (new_subtype or None)
            )
            if self.auto_remove and (type_changed or subtype_changed):
                _LOGGER.warning(
                    "Larnitech: removing device %s (%s) — %s changed %s -> %s; "
                    "it will be recreated on the platform the new %s maps to.",
                    slug,
                    device.name,
                    "type" if type_changed else "sub-type",
                    old_type if type_changed else old_subtype,
                    new_type if type_changed else new_subtype,
                    "type" if type_changed else "sub-type",
                )
                dev_reg.async_remove_device(device.id)
                retype = True
                continue

            self._sync_name(dev_reg, device, dev)
            self._sync_area(dev_reg, device, dev)

        if retype:
            self._schedule_reload("a device changed type")

    @callback
    def _check_mass_removal(self, total: int, missing_slugs: list[str]) -> bool:
        """Gate the normal per-device auto-remove behind a repair issue once
        more than `MASS_REMOVAL_RATIO` of the previously-known devices are
        absent from one snapshot. A drop that large is far more likely a
        controller/network hiccup, a misconfigured object, or the wrong
        server than a real mass removal on the Larnitech side — and unlike a
        single missing device, a hiccup this size can easily persist across
        both polls of the normal 2-consecutive-snapshot debounce. Returns
        True while the guard is latched (caller must not auto-remove).

        The guard LATCHES rather than re-testing the ratio each poll. The ratio
        is measured against the devices still in the registry, which shrinks as
        devices are removed — so an unlatched guard lets a mass removal launder
        itself into a series of "small" ones: confirm the first >50% batch, and
        whatever is still missing is now a minority of what is left and gets
        silently auto-removed by the normal path. Seen live 2026-08-21 on the
        Imerel stand: 93 devices removed on confirmation, then 20 more deleted
        90 seconds later with no prompt at all. It unlatches only when the
        object comes back whole, or when the user confirms."""
        issue_id = f"{self.entry.entry_id}_mass_removal"
        self._mass_missing = missing_slugs

        if not missing_slugs:
            # Everything is back — the only clean exit besides confirmation.
            self._mass_latched = False
        elif self.auto_remove and total and len(missing_slugs) / total > MASS_REMOVAL_RATIO:
            self._mass_latched = True

        if not self._mass_latched:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return False

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="mass_device_removal",
            translation_placeholders={
                "missing": str(len(missing_slugs)),
                "total": str(total),
                "title": self.entry.title,
            },
            data={"entry_id": self.entry.entry_id},
        )
        return True

    @callback
    def confirm_mass_removal(self) -> None:
        """Called from the repair flow once the user confirms. Acts on the
        CURRENT missing set, not on a list captured when the issue was first
        raised — more devices can go missing while the prompt sits unanswered,
        and a stale list leaves those to be deleted later with no prompt."""
        dev_reg = dr.async_get(self.hass)
        for slug in self._mass_missing:
            device = dev_reg.async_get_device(identifiers={(DOMAIN, slug)})
            if device is not None:
                _LOGGER.warning(
                    "Larnitech: removing device %s (%s) — confirmed by user "
                    "after a mass-removal repair issue.",
                    slug,
                    device.name,
                )
                dev_reg.async_remove_device(device.id)
            self._missing.pop(slug, None)
        self._mass_missing = []
        self._mass_latched = False
        # Platforms only add an addr they have not added before (their `known`
        # set), so anything removed here that IS still reported by the
        # controller would never come back on its own. Reload rebuilds that
        # bookkeeping from scratch.
        self._schedule_reload("a mass removal was confirmed")

    @callback
    def _schedule_reload(self, why: str) -> None:
        _LOGGER.info("%s: reloading entry — %s", self.entry.title, why)
        self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    @callback
    def _handle_missing(self, dev_reg, device, slug: str) -> None:
        if not self.auto_remove:
            return
        self._missing[slug] = self._missing.get(slug, 0) + 1
        if self._missing[slug] >= MISSING_SNAPSHOTS_BEFORE_REMOVE:
            # Removing a device takes its entities with it — and with them any
            # dashboard card or automation referencing them. Never do it
            # silently.
            _LOGGER.warning(
                "Larnitech: removing device %s (%s) — absent from %s consecutive "
                "snapshots. Disable the 'auto_remove' option to keep devices "
                "that the controller stops reporting.",
                slug,
                device.name,
                MISSING_SNAPSHOTS_BEFORE_REMOVE,
            )
            dev_reg.async_remove_device(device.id)
            self._missing.pop(slug, None)

    @callback
    def _sync_name(self, dev_reg, device, dev: dict) -> None:
        # Auto-update respects a manual rename; the resync button overrides it.
        if not self.update_names or device.name_by_user is not None:
            return
        name = device_display_name(dev)
        if name and device.name != name:
            dev_reg.async_update_device(device.id, name=name)

    @callback
    def _sync_area(self, dev_reg, device, dev: dict) -> None:
        if not (self.use_areas and self.update_areas):
            return
        lt_area = dev.get("area")
        if not lt_area:
            return
        area = ar.async_get(self.hass).async_get_or_create(lt_area)
        if device.area_id != area.id:
            dev_reg.async_update_device(device.id, area_id=area.id)

    # --- entity_id pattern change (Reconfigure) --------------------------

    @callback
    def apply_entity_id_pattern(self, pattern: str) -> str:
        """Rewrite every entity_id on this entry to the new naming pattern.

        Renaming the registry entries is what actually moves an entity —
        `entity_id` assigned in an entity's `__init__` is only a suggestion
        for one that is not registered yet, so without this a pattern change
        would apply to nothing but future devices. `unique_id` is untouched,
        so history follows the rename.

        HA does not rewrite references, so anything pointing at the old
        entity_ids (dashboard cards, automations, scripts, templates) must be
        updated by hand — hence the summary returned for the log."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        serial = self.client.serial
        renamed = skipped = 0

        for addr, dev in (self.data or {}).items():
            slug = device_slug(serial, addr)
            device = dev_reg.async_get_device(identifiers={(DOMAIN, slug)})
            if device is None:
                continue
            new_base = entity_object_id(pattern, serial, addr, dev)
            for ent in er.async_entries_for_device(
                ent_reg, device.id, include_disabled_entities=True
            ):
                # Companion entities carry a suffix on top of the device slug
                # (`_pid`, `_malfunction`, one per `json` field) — keep it.
                if not ent.unique_id.startswith(slug):
                    continue
                domain = ent.entity_id.split(".", 1)[0]
                new_entity_id = f"{domain}.{new_base}{ent.unique_id[len(slug):]}"
                if new_entity_id == ent.entity_id:
                    continue
                if ent_reg.async_get(new_entity_id) is not None:
                    # Two devices sharing a room and a name collide under the
                    # name-based patterns; leave the loser where it is rather
                    # than fail the whole rename.
                    _LOGGER.warning(
                        "%s: cannot rename %s -> %s, that entity_id is taken",
                        self.entry.title, ent.entity_id, new_entity_id,
                    )
                    skipped += 1
                    continue
                ent_reg.async_update_entity(ent.entity_id, new_entity_id=new_entity_id)
                renamed += 1

        self.entity_id_pattern = pattern
        summary = f"{renamed} entity_id(s) renamed to pattern {pattern!r}"
        if skipped:
            summary += f", {skipped} skipped (id already taken)"
        _LOGGER.warning(
            "%s: %s. HA does not update references — check dashboards, "
            "automations, scripts and templates that used the old entity_ids.",
            self.entry.title, summary,
        )
        return summary

    # --- on-demand resync from the button --------------------------------

    @callback
    def resync_names(self) -> None:
        """Force HA names back to Larnitech, overriding manual renames."""
        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)
        serial = self.client.serial
        for addr, dev in (self.data or {}).items():
            name = device_display_name(dev)
            if not name:
                continue
            device = dev_reg.async_get_device(
                identifiers={(DOMAIN, device_slug(serial, addr))}
            )
            if device is None:
                continue
            dev_reg.async_update_device(device.id, name=name, name_by_user=None)
            for ent in er.async_entries_for_device(
                ent_reg, device.id, include_disabled_entities=True
            ):
                if ent.name is not None:
                    ent_reg.async_update_entity(ent.entity_id, name=None)
