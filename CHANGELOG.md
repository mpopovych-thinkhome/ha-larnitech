# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
