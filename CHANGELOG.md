# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Climate platform (`valve-heating`/`fancoil`/`climate-control`/`AC`/`conditioner`/
`virtual/ventilation`/`vent`) is complete — see `[0.6.0]` below. Next up:
remaining small `virtual` sub-types (`sensor`/`text`/`long-text`/`prf`/`lamp`/
`dimer-lamp`/`rgb-lamp`/`jalousie`/`gate`(+120)), tracked as `0.7.0` — see
`TODO.md` → "Phase 3 — remaining widget types".

## [0.6.1] - 2026-08-18

### Added
- `light-scheme` (all 4 `ls-type` variants) → `switch`. The API exposes the same `status.state` on/off shape regardless of variant, so one class handles all of them; `ls-type=2` ("activate-only") ignores `turn_off` on the controller side, so the switch is shown but off is a no-op for it. `ls-type=4` (master-slave) needs no separate code — it behaves exactly like its underlying lamp/dimmer-lamp/rgb-lamp type.
- `valve` (bare type, main shutoff valve) → `valve`. Read and write use different vocabularies, confirmed live 2026-08-17 by probing the API directly (bypassing HA): read reports `opened`/`closed` (participle), but `status-set` only accepts `open`/`close` (imperative) — `{"state":"opened"}`/`{"state":"closed"}` is rejected with `code 9: set-status has invalid parameter`. Displayed state labels overridden to "Opened"/"Closed" (Larnitech's own wording) via `translation_key`.
- Write-then-verify: after a `status-set`, the integration now re-reads the written device via a targeted `status-get` (not a full `get-devices`) ~1s later and merges the result into HA, instead of trusting `status-set`'s `success` blindly — the controller doesn't reliably push a `statuses` event back for every write.
- New `read_only` option (initial setup + Options flow): the integration only ever reads from Larnitech, never sends `status-set`. A control action from HA (light/switch/climate/etc.) is discarded — the entity is immediately re-read (targeted `status-get`) and snaps back to Larnitech's real status instead. Single choke point: `LarnitechEntity.async_write_status` — also fixed two writes in `light.py` (`LarnitechDimmer`/`LarnitechRgb` brightness/color) that were calling the client directly and would have bypassed the gate.

Write commands for the above are PROVISIONAL pending live verification.

### Fixed
- **Critical:** an empty `get-devices` snapshot (a malformed/`devices`-less reply, not a real "the controller has no devices") was trusted as truth by `_reconcile` — two in a row made `auto_remove` silently delete *every* device from the HA registry, taking their entities (and any dashboard/automation reference to them) with it. Now raises `UpdateFailed` instead: entities go `unavailable` and recover on the next good poll. Device removal, when it does legitimately happen, now logs a `WARNING` with the device name — it was silent before.
- `lamp/lock`: polarity was inverted — confirmed live that `state=off` means locked, not `state=on`.
- `dimmer-lamp` / `rgb-lamp`: `level`/`saturation` are integer percent (0-100), not a 0.0-1.0 fraction — brightness was showing values like 10000%. Fixed decode (`level/100*255`) and write (rounds to an integer percent, no fractions).
- `rgb-lamp`: real status is `hue`/`saturation`/`level`, not `r`/`g`/`b`. Switched from `ColorMode.RGB` to `ColorMode.HS` + `brightness`. `hue` is also 0-100 percent (position around the color wheel), not degrees — was showing green as orange and blue as acid-yellow until scaled ×3.6.
- Larnitech WS frames with raw control characters (long-text widgets) failed `json.loads()` for the *entire* frame, silently dropping every device batched into it, including unrelated toggles — control chars are now stripped before parsing (`client.py`).
- `coordinator.apply_events`: one device in a push-event batch with a hex status or unknown addr discarded the whole batch (including successfully-decoded devices) instead of just triggering its own fallback refresh — this was the main cause of HA state sometimes not reflecting a toggle made in the Larnitech app.
- `valve` (bare type) open/close from HA did nothing: writes sent `on`/`off` (by analogy with `lamp`) and later `opened`/`closed` (by analogy with the read side) — both silently rejected/ignored by the controller. Confirmed live: write vocabulary is `open`/`close`, distinct from the `opened`/`closed` the same device reports on read. Now writes the correct vocabulary; control confirmed live.

## [0.6.0] - 2026-08-17
Climate platform (`valve-heating`/`fancoil`/`climate-control`/`AC`/`conditioner`) rework — first pass shipped provisionally in 0.5.0, this is the confirmed-live follow-up. Write commands are PROVISIONAL pending live verification unless noted otherwise.

### Added
- `AC`/`conditioner`: capabilities are now derived from the vendor bitmasks (`modes`, `fans`/`funs`, `vane-ver`, `vane-hor`) with the documented per-type defaults when a mask is absent, instead of hardcoded lists — so an AC no longer offers horizontal vanes it doesn't have. Vertical/horizontal vanes are exposed as the climate entity's own `swing_mode`/`swing_horizontal_mode` (no separate helper entities).
- `fancoil` split into its own class (`LarnitechFancoil`, was sharing `LarnitechAC`): `hvac_modes` fixed `[off, heat, cool]`; presets share the `valve-heating` manual/always-off scheme (confirmed live — fancoil has the same `automation`/`automations` shape); fan speed is always a 0-100% float regardless of physical step count, exposed as 10%-step `fan_modes`, rounded to the nearest step on read.
- `AC`/`conditioner` capability masks (`modes`/`fans`/`funs`/`vane-hor`/`vane-ver`) are now re-read from the coordinator's live data on every update instead of being frozen at entity creation — a mask changed on the Larnitech side (app-side reconfiguration) is picked up within one poll interval, no HA restart needed.
- `LarnitechAC` split into `LarnitechAC` and `LarnitechConditioner` (was one class branching on `type` internally) — shared protocol logic lives in `_LarnitechACBase`, per-type mask handling lives in each subclass. New project rule: one Larnitech `type` = one entity class (`larnitech_integration_spec.md` → "Структура компонента").
- `swing_mode`/`swing_horizontal_mode` on AC/conditioner now have friendly names ("Vertical swing"/"Horizontal swing") and a per-position icon (`icons.json`); `fan_mode` (AC/conditioner) has icons for Auto/1st/2nd/3rd Speed.
- `climate-control`: `hvac_modes` simplified to `[off, heat_cool]` — this type has no real mode selection (no `modes` capability mask anywhere, device-level or in status), the zone's own algorithm picks heat vs cool on its own. `pid-temperature` (signed heat/cool demand) now drives `hvac_action` — confirmed live on `eaffc8c1_101_250`: `pid-temperature: -100` → `hvac_action: cooling`. The raw percentage is also exposed as a companion diagnostic sensor (`sensor.<slug>_pid`, icon `mdi:sine-wave`) since the `climate` domain has no slot for it. Which setpoint(s) are offered now tracks live status instead of being hardcoded to a range: an automation with only `setpoint-cool` offers a single `target_temperature`, not a phantom heat setpoint too — confirmed live (`supported_features` drops `TARGET_TEMPERATURE_RANGE` when only one setpoint is present).
- `valve-heating` (+`warm-floor`): while a named preset is assigned, `hvac_mode` now reflects that assignment (`heat`) regardless of the heating channel's moment-to-moment on/off — the channel instead drives `hvac_action` (`heating`/`idle`). Without a named preset (Manual/Always-off), `hvac_mode`/`hvac_action` follow the channel directly, same as before. Confirmed live on `eaffc8c1_110_1`. Cool-side detection for a dual heat/cool valve-heating widget is deferred — heat-only for now.
- `fancoil`/`AC`/`conditioner`: `hvac_action` added, mirroring the selected `mode` — neither type exposes a live demand/PID signal like `climate-control`'s, so this isn't new information (`hvac_mode` already carries it), but fills the attribute HA's climate cards look for. `AC`/`conditioner` untested live (no such device found on any accessible object this session).
- Icons (`icons.json`): Manual/Always-off preset icons for `fancoil`/`valve-heating`; 10%-step fan-speed icons for `fancoil`; a default preset icon for `climate-control` (its preset names are per-object custom automation names, not a fixed vocabulary like Manual/Always-off).
- `climate-control`: `hvac_modes` is now always the full `[off, heat_cool, heat, cool]` (per user decision 2026-08-18) — an earlier attempt to narrow it to what the device-level `modes` capability list allows was tried and dropped; picking an incompatible mode is the user's call, not something to hide from the UI.
- `virtual` sub-type `ventilation` (Komfovent-style) moved from `fan` to `climate` — a better fit once a widget has a linked `temperature-sensors` XML attribute (confirmed live 2026-08-18): `fan_mode` covers the speed preset (`auto`/`low`/`middle`/`high`, confirmed live by writing each and reading back — same 4 wire values `AC`/`fancoil` use, duplicated as a plain list rather than shared), and `current_temperature`/`target_temperature` have real native slots the `fan` domain never had.
- `vent` (bare type) moved from `fan` to `climate` — once a CO2 automation is linked, it carries `automation`/`automations` following the *exact* same manual/always-off/named-preset scheme as `valve-heating`/`fancoil` (confirmed live 2026-08-18: absent automation = manual, `"always-off"` = locked off + `state` forced off). Speed (`status.fan`, 0-100%) is exposed as `fan_mode` in 10%-step presets, same shape as `fancoil`'s. `hvac_mode` stays `fan_only` while a named automation is active regardless of the channel's on/off (same rule as `valve-heating`); `hvac_action` reduces to just `idle`/`fan` (moving air or not — no PID-style signal exists for this type, but none is needed, `status.fan` already says it). Turning off from HA while a named automation is active now also resets it to Manual (`automation: ""`) — otherwise `hvac_mode` stays locked to `fan_only` and the "Off" click looks like it did nothing. Same turn-off-resets-preset fix applied to `valve-heating`/`fancoil` for consistency.
- New `number` platform (`number.py`): `vent`'s CO2 setpoint (`status.target`, 400-2000 ppm, step 50 — range/step not documented anywhere, a reasonable guess pending live confirmation) — only created once a linked automation reports it. The live CO2 reading (`status.current`) is *not* duplicated here — it's the same physical sensor already exposed as its own `co2-sensor` entity.
- `LarnitechFan` (`lamp/air-fan`) was missing `FanEntityFeature.TURN_ON`/`TURN_OFF` in `supported_features` — HA rejected `fan.turn_on`/`fan.turn_off` as unsupported even though the methods exist (confirmed live 2026-08-18; same class of bug as the `ClimateEntityFeature.TURN_ON`/`TURN_OFF` omissions fixed earlier in this file, just missed in `fan.py`).

### Fixed
- **`climate-control`: `hvac_mode` writes from HA never reached Larnitech.** Root cause found and confirmed live 2026-08-18: `LarnitechClimateControl` had two `async_set_hvac_mode` definitions — the correct one (writes `state`+`mode`) was silently shadowed by a leftover no-op copy further down the file that only wrote `state`, inherited unnoticed from an earlier refactor. `climate.set_hvac_mode` returned success with no error either way, masking it completely; only a direct Larnitech-side read (bypassing HA) exposed the mismatch. A full-file AST scan confirmed no other duplicate method definitions exist anywhere in the package. Verified live for all three writable values (`heat`/`cool`/`heat_cool` → `mode: heat`/`cool`/`auto`, each producing the correct single/dual setpoint on the controller).
- `vent`: `hvac_action` could show `fan` on a stopped unit — `status.fan` keeps reporting the last speed even after the controller sets `state: "off"`, so speed alone isn't "moving air". Now requires both the channel on *and* speed > 0; simplified to two states only (`fan`/`idle`), matching the type's actual signal (no third "off vs idle" distinction exists here). Confirmed live.
- `vent`'s CO2 setpoint (`number.py`) now exists as an entity for every `vent` widget, instead of appearing/disappearing as its CO2 automation is toggled — it goes `unavailable` when no automation is linked (`status.target` absent) rather than not existing at all. Per user decision 2026-08-18: a stable entity_id beats one whose id changes each time it's re-created.
- `fancoil`: switching `hvac_mode` between `heat`/`cool` now writes `mode` first, waits 1s (`_MODE_THEN_STATE_SETTLE`), then writes `state: "on"` separately, instead of both in one `status-set` (per user instruction 2026-08-18). Confirmed live via `watch_start`/`watch_read`: the controller reacts to a `mode` change by briefly dropping the channel to `off` on its own, and the deliberate `state: "on"` a second later is exactly what corrects it back — sending both together left the channel silently off after a mode switch.
- `vent`/`valve-heating`/`fancoil`: turning off from HA while a named preset/automation is active now correctly resets it to Manual (`automation: ""`) **and** turns the channel off. The two writes must be sequential with a **1s gap**, not combined in one `status-set` or sent back-to-back — confirmed live 2026-08-18 that clearing `automation` makes the controller re-evaluate the channel and drive it back to its own "on" state, and that re-evaluation was landing *after* an immediate `state: off`, leaving the channel silently on. New `_PRESET_RESET_SETTLE = 1` constant in `climate.py`.
- `coordinator.merge_status`: climate setpoints (`setpoint`/`setpoint-heat`/`setpoint-cool`) are a group that must be replaced wholesale, not merged key-by-key — Larnitech only ever sends the setpoint(s) the *active* automation actually uses, so a plain merge kept a setpoint from a previous automation forever (a zone switched to cooling-only kept showing a stale heat setpoint in HA). Any status update carrying at least one setpoint key now replaces the whole group.
- `coordinator.async_refresh_addr` (the point-verify read after a write, and now also after any push event carrying an `automation` change): `status-get`'s response is a *complete* status, unlike a push event — it now **replaces** the stored status instead of merging into it, so a key genuinely absent under the new automation (e.g. no setpoint at all) is actually removed instead of surviving from the previous status forever. A preset switch is now detected live (`automation` key in a push event) and triggers this point-verify immediately, instead of waiting for the up-to-120s safety poll.
- `conditioner`: `modes`/`funs` masks are no longer read from the API at all — confirmed live that `modes` arrives with the wrong value (`funs` never arrives, same as AC's `fans`) — always shows the full default mode/fan-speed set now instead of an incorrectly narrowed one. `AC` is unaffected; its `modes`/`vane-hor` are confirmed correct live.
- Write-then-verify (`coordinator.async_refresh_addr`) was replacing the *entire* device dict with `status-get`'s response, which — unlike `get-devices` — omits `name`/`area`/`modes`/`vane-hor`/`automations`/... Every write through HA was silently wiping those fields (most visibly: AC's horizontal-swing feature disappearing after any write at all, since its default mask is "no horizontal vane"). Now merges only the `status` sub-key, same as the push-event path (`apply_events`).
- `AC`/`conditioner` fan speed could not be set from HA at all: the integration wrote a numeric index, which the controller rejects with `set-status has invalid parameter`. The wire vocabulary is the fixed set `auto`/`low`/`middle`/`high` (probed exhaustively against both live devices), displayed in HA as `Auto`/`1st Speed`/`2nd Speed`/`3rd Speed`. Reading was also broken for the middle speed — the raw value is `middle`, not `medium`.
- `climate.py` `fan_mode` (AC/conditioner/fancoil): raw `status.fan` was passed straight through without mapping to `_attr_fan_modes` — a numeric value like `0` isn't a valid fan mode string. Now: a string is checked against the list, a number is used as an index into it, anything else returns `None`.
- `valve-heating` `preset_mode`: the device always has two reserved modes beyond any user-defined presets — "manual" (`status.automation` absent, reported as `"Manual"` — a synthetic label, Larnitech itself has no name for this state) and "always-off" (`status.automation="always-off"` on the wire, shown as `"Always-off"`). Neither was in the XML-defined `automations` preset list, so a device in either mode reported a `preset_mode` HA considered invalid. Both are now always injected into `preset_modes` (shared with `fancoil` via the `_ManualAlwaysOffPresets` mixin).

## [0.5.0] - 2026-06-26
### Added
- Climate: `valve-heating` (+ `warm-floor`) → heat/off with `target` setpoint;
  `climate-control` → off/heat/cool/auto with `setpoint-heat`/`setpoint-cool`
  range; `AC` (+ `conditioner`/`fancoil`) → off/heat/cool/dry/fan/auto with
  `target` and fan mode. Larnitech `automations` exposed as HA presets. Write
  commands are provisional pending live verification.

## [0.4.0] - 2026-06-26
### Added
- Covers: `blinds` → cover (shade), `jalousie` → cover (blind), `gate` → cover
  (gate). Position is inverted (Larnitech 0.0 = open). The `target` write key
  and `stop` command are provisional; jalousie tilt is not implemented yet.

## [0.3.0] - 2026-06-26
### Added
- Lighting widgets: `dimmer-lamp` → light with brightness (`level` 0.0–1.0),
  `rgb-lamp` → light with rgb. Brightness/color status & write keys are
  provisional pending confirmation against a live device.
- `lamp` sub-types mapped to their own platforms: `socket`/`pump`/
  `closing-switch` → switch (`socket` = outlet), `lock` → lock, `air-fan` →
  fan, `valve-3`/`damper` → valve, `dehumidifier` → humidifier. Unknown
  sub-types are logged and skipped.
- Diagnostics logging to learn what the controller really sends: unmapped
  device types/sub-types logged once with the raw payload; non-numeric sensor
  and unrecognized binary_sensor / button values warned once; raw status dumped
  per entity at debug; undecodable frames and failed `status-set` logged.

### Fixed
- binary_sensor on/off vocabulary from live data: `opened`/`leak` = on,
  `ok` = off (door sensors report `opened`, leak sensors `ok`).

## [0.2.0] - 2026-06-26
### Added
- Measurement sensors: `humidity-sensor`, `co2-sensor`, `illumination-sensor`,
  `current-sensor` → sensor (alongside `temperature-sensor`).
- Discrete sensors: `motion-sensor`, `door-sensor`, `leak-sensor` →
  binary_sensor.
- Physical buttons: `switch` → event (press / hold). Gesture decode is
  provisional pending confirmation against a live press.

## [0.1.0] - 2026-06-24
### Added
- Config flow with local/cloud connection; multiple controllers per HA.
- Persistent API2 WebSocket client: `authorize`, `get-devices`,
  `status-subscribe`, `status-set`.
- Push updates — decoded subscribe events applied directly; 120s safety poll.
- `temperature-sensor` → sensor; `lamp` → light with on/off control.
- Stable `unique_id` / default `entity_id` = `<serial>_<ID>_<SUBID>`.
- Dynamic device add/remove (2-snapshot grace; immediate on type change);
  name and room (area) sync.
- Options flow (`auto_remove`, `update_names`, `use_areas`, `update_areas`)
  and Reconfigure flow for connection details.
- `button.<serial>_resync_names` to force names back to Larnitech.

### Fixed
- New `websockets` `ClientConnection` has no `.open` — reconnect on exception.
- SSL context creation moved off the event loop (blocking-call warning).
- Cloud answers no WS ping/close frame — `ping_interval=None`,
  `close_timeout=1`, clean supervisor shutdown.
- Controller `type:"json"` widgets emit invalid `{{` — placeholder-key repair.
