# Larnitech — Home Assistant integration

Custom integration connecting Home Assistant to Larnitech SVIT controllers over
the API2 WebSocket.

- Local or cloud connection, multiple controllers per Home Assistant.
- Push updates via `status-subscribe` (decoded events applied directly) plus a
  safety poll.
- Read + control: `temperature-sensor` → sensor, `lamp` → light (on/off).
- Dynamic device add/remove, name & room sync, and a resync-names button.

## Install via HACS

1. HACS → ⋮ → **Custom repositories** → add this repository, category
   **Integration**.
2. Search **Larnitech** in HACS, download it.
3. Restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Larnitech.**

Updates appear in HACS when a new version is published.

## Versioning

See [`CHANGELOG.md`](CHANGELOG.md). The project follows
[Semantic Versioning](https://semver.org/).
