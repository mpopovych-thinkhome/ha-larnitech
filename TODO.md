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

## Distribution

- [ ] HACS support — `hacs.json` is already in place and the repo is public, but there's no git tag yet, and HACS needs a release to install/update a custom repository from. **Deferred to the `0.9.0` release** — cut the first tag then. `home-assistant/brands` PR #11017 was closed by maintainers 2026-08-21: the brands repo no longer accepts custom-integration icons since HA 2026.3.0 — local `brand/` is now the only path, and it already works (HA reads it directly, independent of HACS — no HACS-side blocker here).

## Ideas / nice-to-have

- [ ] `vent` CO2 setpoint `number` entity range/step (400-2000 ppm, step 50) — not documented anywhere, a guess pending live confirmation
- [ ] `valve-heating` cool-side detection — currently `hvac_action` only ever reports `heating`/`None` (own automation vs. externally-driven), never `cooling`, because the widget itself has no way to know it's plumbed as a cooling channel; extend once there's a way to detect a dual heat/cool valve-heating widget's active side
- [ ] `AC`/`conditioner` `hvac_action` (mirrors `mode`, climate.py `_LarnitechACBase.hvac_action`) — not live-tested, no AC/conditioner device found on any accessible object this session
- [ ] **Low priority, deferred 2026-08-21** — Local (LAN) discovery via mDNS / zeroconf. No Larnitech controller is reachable on any LAN we develop from (the accessible objects are all cloud-connected), so the service type it advertises cannot be probed and the discovery cannot be tested. The API2 wiki only documents the hostname `de-mg.local`, which is a plain A-record — HA's zeroconf browses DNS-SD service types, not hostnames, so a hostname alone is not enough to write the `manifest.json` entry. Revisit only if a controller ends up on a LAN we can reach.

## Open investigations

- [ ] **Sensors reading 0 on the Stage stand (CO2, then virtual temperature sensors)** — found 2026-08-27. Confirmed twice, directly against Larnitech (bypassing HA, via the dedicated MCP tool) that the controller itself reports `0` for the affected addrs (`1:219` CO2, `1:171`/`1:172` virtual temp) — not a coordinator merge/replace bug on our side, and unrelated to the climate addrs being written during testing (different, unconnected addrs). Onset coincides with a window of rapid HA core restarts and live write testing on that same stand. Leading suspicion: stand degradation under heavy testing load, not an integration bug — not confirmed as root cause. Revisit if it recurs outside a heavy-testing window, or affects a client object.
