# TODO

Backlog of features and ideas. When a task is done, **remove it from here** and
add it to `CHANGELOG.md` (under `### Added` / `### Fixed`).

## Phase 3 — widget types

**Done** — every widget type is now either implemented or explicitly dropped.
Mapping reference: `larnitech_integration_spec.md` → "Карта маппинга типов".

Dropped by decision, not backlog (do NOT re-add without a new decision):
`virtual/prf`, `virtual/jalousie`+`120`, `virtual/gate`+`120`, `virtual/sunrise`,
`virtual/plan`, `virtual/btunreg`, `virtual/lamp`, `virtual/dimer-lamp`,
`virtual/rgb-lamp`, `json/btunreg`, `security-card-reader`, `ir-receiver`,
`ir-transmitter`, `remote-control`, `rtsp`, `speaker`, `intercom`, `item-ref`,
`com-port`. Reasons per type are in the spec's mapping tables.

Blocked on hardware, not on code — needs a real object, can't be reproduced on
the stand (implemented, dispatch verified, "on" state never observed live):

- [ ] `current-sensor`, `motion-sensor`, `leak-sensor`, `door-sensor` (no sub-type)

## Safety

- [ ] Guard against mass device removal: if a `get-devices` snapshot is missing
  more than half of the previously-known devices (vs. flat-out empty, already
  handled — see spec's "Жизненный цикл (reconcile)"), the coordinator must NOT
  silently auto-remove them via the normal `auto_remove` / 2-missed-snapshots
  path. Instead, raise an HA repair issue (`issue_registry`) with a confirm
  action, e.g. "На Larnitech объекте пропало больше половины устройств, вы
  хотите удалить их из HA?" — actual removal only happens if the user
  confirms. Rationale: a >50% drop is far more likely a controller/network
  hiccup (bad `get-devices` reply, object misconfigured, wrong server) than a
  real mass-deletion on the Larnitech side, and the existing 2-consecutive-
  snapshot debounce doesn't protect against a hiccup that persists across
  both polls.

## Distribution

- [ ] HACS support (decide repo layout / `hacs.json`) — note: HACS doesn't yet read the local `brand/` folder (open HACS bug), so it'll show "icon not available" until either HACS adds support or the `home-assistant/brands` PR (#11017) merges

## Ideas / nice-to-have

- [ ] `vent` CO2 setpoint `number` entity range/step (400-2000 ppm, step 50) — not documented anywhere, a guess pending live confirmation
- [ ] `valve-heating`/`fancoil`/`vent` `"Always-off"` preset write (raw `"always-off"` string) — still untested against a live device. The `"Manual"` (empty-value) side is confirmed live 2026-08-18.
- [ ] `fancoil` `fan_mode` write — untested against a live device (`hvac_mode` write confirmed live 2026-08-18)
- [ ] `AC`/`conditioner` fan-speed bits 4-6 (4th/5th speed, silent mode) — the wire vocabulary only covers `auto`/`low`/`middle`/`high`, so these can't be written (probed live 2026-08-17: every candidate value for the missing speeds — numbers, `"5"`, `"speed5"` — rejected by `status-set` with `code 9`). **Bug (Larnitech-side):** reading is broken too — `status.fan` comes back as raw JSON `null` whenever the physical unit is actually running above the 3rd speed, instead of some undecoded value we could add to the dictionary. Revisit if Larnitech ever exposes a real value for these.
- [ ] **Bug (Larnitech-side):** the `fans`/`funs` capability-mask attribute never reaches the API at all — confirmed twice live on `1:101` (`fans="0x47"` and later `fans="0x77"`, neither ever appeared in `get-devices`/`status-get`). We always fall back to the vendor default (`0x1F` AC / `0x0F` conditioner) — there is currently no way to learn a device's real fan-speed mask through the API. Revisit if Larnitech ever starts sending it.
- [ ] **Bug (Larnitech-side):** `conditioner`'s `modes` device-level key arrives, but with the wrong value — configured `modes="0x1A"` in XML, API reported back `"0x1F"` (everything) regardless (confirmed live 2026-08-17). `AC`'s `modes` is unaffected (confirmed correct on `1:101`). Currently bypassed client-side — `LarnitechConditioner` never reads the live `modes`/`funs` keys, always shows the full default set. Snap back to reading them live if Larnitech fixes this server-side (see `_LarnitechACBase`/`LarnitechConditioner` in `climate.py`).
- [ ] `AC`/`conditioner` `swing_mode`/`swing_horizontal_mode` writes (vane index) — untested against a live device
- [ ] `valve-heating` cool-side `hvac_action` — heat-only for now (see climate.py `LarnitechValveHeating.hvac_action`); extend once there's a way to detect a dual heat/cool valve-heating widget's active side
- [ ] `AC`/`conditioner` `hvac_action` (mirrors `mode`, climate.py `_LarnitechACBase.hvac_action`) — not live-tested, no AC/conditioner device found on any accessible object this session
- [ ] Hub device per controller — group entities under one `Larnitech <serial>` device
- [ ] Options flow: setting for the `entity_id` naming pattern, choice of:
  - `serial_id_subid` (current default)
  - `larnitech_id_subid`
  - `room_devicename`
  - `room_devicename_id_subid`
- [ ] Обновить файл README.md чтобы в нем была указано таблица мапингов типов с коментариями нюансов по каждому
- [ ] Configurable poll interval in the options flow
- [ ] Download diagnostics (entry + raw get-devices snapshot)
- [ ] UI translations in the client language (uk / ru / lt)
- [ ] Map `current-sensor` / power to the Energy dashboard
- [ ] Local (LAN) discovery via mDNS / zeroconf
- [ ] Suffix entity/device names with the Larnitech address, e.g. `Temperature (1:98)` — helps map HA entities back to the controller UI
