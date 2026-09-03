# Updated: 2026-09-03 12:45
"""Larnitech `speaker` (media point) as a `media_player`.

Everything below is confirmed live on the demo case (`5:30`), 2026-09-02/03.

What the API2 side of this type can and cannot do:
- Transport, volume, mute and "play this URL" all work.
- `next`/`previous` switch the SOURCE, not the track — the source list is
  controller-side and invisible through the API, so there is no
  `source_list`/`select_source` to offer, only the two step buttons.
- No track metadata of any kind (title, artist, artwork) — `url` is the only
  indication of what is playing.
- No power on/off: the type has no such command, `stop` is the closest thing.
- Seek is NOT offered: writing `position` (as `"30.000"` and as `60000`) was
  acknowledged and ignored, position simply kept advancing. Both tries were
  against a live radio stream, where seeking is meaningless anyway — so this
  is "unconfirmed", not "known broken". Revisit with a file-backed source.

Two live findings shape the code more than the docs do:
- `muted` is present in the status only WHILE muted; unmuting removes the key
  rather than setting it `false`. Reading it as `status.get("muted") is True`
  is therefore correct, and the post-write `status-get` (which REPLACES the
  stored status, see `coordinator.async_refresh_addr`) is what clears it.
  Note the controller pushes an event when muting but none at all when
  unmuting — an unmute made outside HA is only picked up by the next poll.
- While playing, the controller pushes a `position` event EVERY SECOND. See
  `_sync_position` for why that must not become a state write per second.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.media_player import (
    ENTITY_ID_FORMAT,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import LarnitechEntity

# Read vocabulary -> HA state. `error` has no HA equivalent (the domain has
# no error state) — it reads as idle, with the raw value kept in the
# attributes and a one-off warning, so a widget stuck in `error` is still
# diagnosable. See BUG-009.
_STATE_MAP = {
    "playing": MediaPlayerState.PLAYING,
    "pause": MediaPlayerState.PAUSED,
    "stopped": MediaPlayerState.IDLE,
    "error": MediaPlayerState.IDLE,
}
_STATE_ERROR = "error"

# Write vocabulary. NOT derived from the read vocabulary — the two differ per
# command (`play`->`playing`, `pause`->`pause`, `stop`->`stopped`), and an
# unrecognised value is not rejected but drives the widget into `error`
# (BUG-009). Only these six strings are ever written.
_CMD_PLAY = "play"
_CMD_PAUSE = "pause"
_CMD_STOP = "stop"
_CMD_NEXT = "next"
_CMD_PREVIOUS = "previous"

# How far (seconds) the reported position may drift from what the current
# anchor predicts before it is re-anchored — see `_sync_position`.
_POSITION_RESYNC = 5


def _parse_time(value) -> float | None:
    """`SS.mmm`, `M:SS.mmm` or `H:MM:SS.mmm` -> seconds.

    All three shapes came off one device within a minute (2026-09-03): the
    controller drops leading groups as they reach zero, so the format is not
    fixed and must not be parsed as plain seconds."""
    if not isinstance(value, str):
        return None
    total = 0.0
    for part in value.split(":"):
        try:
            total = total * 60 + float(part)
        except ValueError:
            return None
    return total


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if d.get("type") == "speaker"}
        new = [LarnitechMediaPlayer(coordinator, a) for a in current - known]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.add_discovery_listener(_add_new))
    _add_new()


class LarnitechMediaPlayer(LarnitechEntity, MediaPlayerEntity):
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.PLAY_MEDIA
    )

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())
        self._position: float | None = None
        self._position_updated: datetime | None = None
        self._sync_position()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_position()
        super()._handle_coordinator_update()

    @callback
    def _sync_position(self) -> None:
        """Re-anchor `media_position` only when playback actually jumps.

        The controller pushes a `position` event every second while playing,
        so publishing each one would mean a new HA state — and a recorder row
        — every second per media point. HA extrapolates elapsed time from the
        anchor by itself, so the anchor only has to move when the reported
        position leaves where it predicts: a source change, or a play/pause
        transition."""
        position = _parse_time(self.status.get("position"))
        if position is None:
            self._position = None
            self._position_updated = None
            return
        expected = self._expected_position()
        if expected is None or abs(position - expected) > _POSITION_RESYNC:
            self._position = position
            self._position_updated = dt_util.utcnow()

    @callback
    def _expected_position(self) -> float | None:
        if self._position is None or self._position_updated is None:
            return None
        if self.state is not MediaPlayerState.PLAYING:
            return self._position
        elapsed = (dt_util.utcnow() - self._position_updated).total_seconds()
        return self._position + elapsed

    @property
    def state(self) -> MediaPlayerState:
        raw = self.status.get("state")
        if raw == _STATE_ERROR:
            self._warn_once(
                "state_error",
                "Larnitech media point %s reports state 'error' — writing `play` "
                "recovers it (BUG-009)",
                self.entity_id,
            )
        return _STATE_MAP.get(raw, MediaPlayerState.IDLE)

    @property
    def volume_level(self) -> float | None:
        volume = self.status.get("volume")
        if not isinstance(volume, (int, float)):
            return None
        return max(0.0, min(1.0, volume / 100))

    @property
    def is_volume_muted(self) -> bool:
        # Absent key = not muted; the controller removes it on unmute rather
        # than writing `false` (see module docstring).
        return self.status.get("muted") is True

    @property
    def media_content_id(self) -> str | None:
        return self.status.get("url")

    @property
    def media_content_type(self) -> MediaType:
        return MediaType.MUSIC

    @property
    def media_position(self) -> float | None:
        return self._position

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._position_updated

    @property
    def media_duration(self) -> float | None:
        # Only present for a source that has one — absent on live streams.
        return _parse_time(self.status.get("duration"))

    @property
    def extra_state_attributes(self) -> dict:
        return {
            # The raw value, so `error` (which reads as idle) stays visible.
            "larnitech_state": self.status.get("state"),
            "priority": self.status.get("priority"),
        }

    async def async_media_play(self) -> None:
        await self.async_write_status({"state": _CMD_PLAY})

    async def async_media_pause(self) -> None:
        await self.async_write_status({"state": _CMD_PAUSE})

    async def async_media_stop(self) -> None:
        await self.async_write_status({"state": _CMD_STOP})

    async def async_media_next_track(self) -> None:
        await self.async_write_status({"state": _CMD_NEXT})

    async def async_media_previous_track(self) -> None:
        await self.async_write_status({"state": _CMD_PREVIOUS})

    async def async_set_volume_level(self, volume: float) -> None:
        # The controller quantises to 1/250 (0.4%), so the value read back is
        # the nearest step, not the one written — never verify for equality.
        await self.async_write_status({"volume": round(volume * 100, 1)})

    async def async_mute_volume(self, mute: bool) -> None:
        await self.async_write_status({"muted": mute})

    async def async_play_media(self, media_type, media_id: str, **kwargs) -> None:
        """Point the media point at a URL it can reach itself.

        Writing `url` switches the stream and keeps playing (confirmed live);
        position resets to zero. `media_type` is not checked — the controller
        takes any URL and there is nothing to validate it against."""
        await self.async_write_status({"url": media_id})
