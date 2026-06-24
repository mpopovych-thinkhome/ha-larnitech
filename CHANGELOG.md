# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
