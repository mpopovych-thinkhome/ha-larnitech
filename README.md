# Larnitech — Home Assistant integration

Custom integration connecting Home Assistant to Larnitech SVIT controllers over
the API2 WebSocket.

- Local or cloud connection, multiple controllers per Home Assistant.
- Push updates via `status-subscribe` (decoded events applied directly) plus a
  safety poll.
- Read + control: `temperature-sensor` → sensor, `lamp` → light (on/off).
- Dynamic device add/remove, name & room sync, and a resync-names button.

Design notes and the full Larnitech → Home Assistant type mapping live in
[`larnitech_integration_spec.md`](larnitech_integration_spec.md).

## Branches

- `main` — stable, released versions.
- `stage` — work in progress; merged into `main` on release.

## Versioning

See [`CHANGELOG.md`](CHANGELOG.md). The project follows
[Semantic Versioning](https://semver.org/).

## Install

Copy this folder to `/config/custom_components/larnitech/`, restart Home
Assistant, then add the integration via **Settings → Devices & Services**.
