# Updated: 2026-09-03 15:05
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
- Seek works, but only where there is something to seek: on a file-backed
  source it jumps as asked, on a live stream the write is acked and ignored.
  The unit is SECONDS in every accepted form — `"1:00.000"`, `"20.000"` and
  a bare number all landed on the same second. A target past the end of the
  track does not clamp: playback ends, `state` becomes `eof` and `position`
  resets to zero (that is how a bare `90000` — ninety thousand seconds, not
  milliseconds — was found to be seconds).

`priority` turned out to be the most capable part of this type — a real
interruption stack, confirmed live 2026-09-03:
- A source claims a level. A command carrying a HIGHER priority takes the
  point over; one carrying a LOWER priority is discarded in silence, `stop`
  included — a media point held at priority 8 ignores every plain write HA
  makes (HA's own writes carry no priority, i.e. the lowest level there is).
- `stop` at a priority >= the active one releases the ACTIVE level and the
  source underneath resumes where it would have been — the interrupted
  stream had kept running. With nothing underneath, playback stops.
- A source that simply plays to its end pops the same way, with no `eof`
  in between: the announcement ends and the music is back on its own.
- `priority` is only accepted written TOGETHER with `url` + `state` — on its
  own the controller does not even acknowledge it.

That is an announcement system, so it is wired to HA's own: `play_media`
with `announce: true` (what `tts.speak` sends) claims `_PRIORITY_ANNOUNCE`
and the previous source returns by itself afterwards; `extra: {priority: N}`
sets the level explicitly.

Because the level also decides whether a command runs at all, no control
here is ever sent bare: play, pause, stop, next/previous, volume, mute and
seek all re-assert the level already active (`_active_priority`), which
always passes the gate. Deliberately not the top of the scale — a command
CLAIMS the priority it arrives with (only `stop` releases instead), and a
pause sent at 250 parked the point there, where HA's own `play` at priority
0 could no longer reach it.

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

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_EXTRA,
    ENTITY_ID_FORMAT,
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
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
    # Reached the end of a track (also what a seek past the end produces).
    # Undocumented by the vendor, found live 2026-09-03.
    "eof": MediaPlayerState.IDLE,
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

# Priority levels (the controller's own scale is 0-250).
# Announcements sit well above the levels controller scripts use in practice
# (8 in the vendor's own example), while leaving room above for anything
# genuinely urgent.
_PRIORITY_ANNOUNCE = 100
# Ordinary playback claims the bottom level: HA is one source among several,
# and grabbing a high level here would make every HA-started stream
# un-interruptible by the controller's own announcements.
_PRIORITY_NORMAL = 0


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
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
        | MediaPlayerEntityFeature.BROWSE_MEDIA
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
    def _active_priority(self) -> int:
        """The level the media point is held at right now.

        Every command except `stop` goes out at this level. Sending below it
        is discarded in silence — a point a controller script grabbed at
        priority 8 ignored HA's pause and mute outright (confirmed live).
        Sending ABOVE it is not the answer either: unlike `stop`, those
        commands CLAIM the level they arrive with, and a pause sent at the
        top left the point parked at 250, where HA's own `play` could no
        longer reach it. Re-asserting the level that is already active always
        passes the gate and claims nothing new."""
        priority = self.status.get("priority")
        if isinstance(priority, (int, float)):
            return int(priority)
        return _PRIORITY_NORMAL

    async def _write_at_active_priority(self, status: dict) -> None:
        await self.async_write_status({**status, "priority": self._active_priority})

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
        await self._write_at_active_priority({"state": _CMD_PLAY})

    async def async_media_pause(self) -> None:
        await self._write_at_active_priority({"state": _CMD_PAUSE})

    async def async_media_stop(self) -> None:
        # Releases the active level rather than stopping everything: with a
        # source underneath (an announcement over music), that one resumes,
        # and a second stop stops it too.
        await self._write_at_active_priority({"state": _CMD_STOP})

    async def async_media_next_track(self) -> None:
        await self._write_at_active_priority({"state": _CMD_NEXT})

    async def async_media_previous_track(self) -> None:
        await self._write_at_active_priority({"state": _CMD_PREVIOUS})

    async def async_set_volume_level(self, volume: float) -> None:
        # The controller quantises to 1/250 (0.4%), so the value read back is
        # the nearest step, not the one written — never verify for equality.
        await self._write_at_active_priority({"volume": round(volume * 100, 1)})

    async def async_mute_volume(self, mute: bool) -> None:
        await self._write_at_active_priority({"muted": mute})

    async def async_media_seek(self, position: float) -> None:
        # Seconds, in the same shape the device reports them. Nothing to
        # clamp against on a live stream (no duration, and the write is
        # ignored there anyway); HA's own slider is bounded by
        # `media_duration` where there is one.
        await self._write_at_active_priority({"position": f"{position:.3f}"})

    async def async_play_media(self, media_type, media_id: str, **kwargs) -> None:
        """Play a URL, an HA media-library item, or a TTS announcement.

        A `media-source://` id (what the media browser and `tts.speak` hand
        over) is resolved to an HA-served URL and made absolute, because the
        controller fetches it itself rather than receiving a stream from HA —
        it has to be a URL that resolves from where the controller sits.

        `announce: true` claims `_PRIORITY_ANNOUNCE`, so the announcement
        interrupts whatever plays and the previous source comes back on its
        own when it ends; `extra: {priority: N}` sets the level by hand.
        `url`, `priority` and `state` go in ONE write — the controller
        ignores `priority` written on its own. `media_type` is not checked:
        the controller takes any URL and there is nothing to validate
        against."""
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = item.url
        media_id = async_process_play_media_url(self.hass, media_id)

        extra = kwargs.get(ATTR_MEDIA_EXTRA) or {}
        priority = extra.get("priority")
        if priority is None:
            priority = (
                _PRIORITY_ANNOUNCE
                if kwargs.get(ATTR_MEDIA_ANNOUNCE)
                else _PRIORITY_NORMAL
            )
        await self.async_write_status(
            {"url": media_id, "priority": int(priority), "state": _CMD_PLAY}
        )

    async def async_browse_media(
        self, media_content_type=None, media_content_id=None
    ) -> BrowseMedia:
        """Browse HA's own media library — the controller has no library of
        its own to expose (its source list is invisible through the API)."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )
