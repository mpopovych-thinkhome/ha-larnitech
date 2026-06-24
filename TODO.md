# TODO

Backlog of features and ideas. When a task is done, **remove it from here** and
add it to `CHANGELOG.md` (under `### Added` / `### Fixed`).

## Phase 3 — widget types

Mapping reference: `larnitech_integration_spec.md` → "Карта маппинга типов".

- [ ] `dimmer-lamp` → light with brightness (`level` 0.0–1.0)
- [ ] `rgb-lamp` → light with rgb/rgbw
- [ ] `AC` / `conditioner` / `climate-control` / `fancoil` → climate
- [ ] `valve-heating` (+ `warm-floor`) → climate
- [ ] `blinds` / `jalousie` / `gate` → cover (position, tilt, device_class)
- [ ] sensors: `humidity-sensor`, `co2-sensor`, `illumination-sensor`, `current-sensor` → sensor
- [ ] binary: `motion-sensor`, `door-sensor`, `leak-sensor` → binary_sensor
- [ ] `switch` (physical button) → event (press / hold)
- [ ] `lamp` sub-types: `lock`→lock, `socket`→switch, `air-fan`→fan, `pump`→switch, `valve-3`/`damper`→valve, `dehumidifier`→humidifier
- [ ] `virtual` sub-types: `sensor`/`text`/`long-text`/`prf`→sensor, `lamp`/`dimer-lamp`/`rgb-lamp`→light, `jalousie`/`gate`(+`120`)→cover

## Distribution

- [ ] HACS support (decide repo layout / `hacs.json`)
- [ ] Brand logo — PR to `home-assistant/brands` (`custom_integrations/larnitech`)

## Ideas / nice-to-have

- [ ] Hub device per controller — group entities under one `Larnitech <serial>` device
- [ ] Configurable poll interval in the options flow
- [ ] Download diagnostics (entry + raw get-devices snapshot)
- [ ] UI translations in the client language (uk / ru / lt)
- [ ] Map `current-sensor` / power to the Energy dashboard
- [ ] Local (LAN) discovery via mDNS / zeroconf
