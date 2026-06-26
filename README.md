# Larnitech — Home Assistant integration

Custom integration connecting Home Assistant to Larnitech controllers over
the API2 WebSocket.

- Local or cloud connection, multiple controllers per Home Assistant.
- Push updates via `status-subscribe` (decoded events applied directly) plus a
  safety poll.
- Read + control: sensors (temperature/humidity/co2/illumination/current),
  binary sensors (motion/door/leak), lights (`lamp`, `dimmer-lamp`, `rgb-lamp`),
  `switch`/`lock`/`fan`/`valve`/`humidifier` (lamp sub-types) and physical
  buttons (`switch` → event).
- Dynamic device add/remove, name & room sync, and a resync-names button.

## Diagnostics logging

The integration logs whatever it cannot map or decode, so unknown types and
unexpected payloads can be diagnosed and the mapping fixed. At the default
level it already warns **once** for each unmapped device type/sub-type (with the
raw payload), each non-numeric sensor value, each unrecognized binary-sensor /
button value, undecodable frames, and failed writes.

To see the **full** detail — including a raw status dump per device and raw
button gesture values — enable debug logging. Add to `configuration.yaml` and
restart Home Assistant:

```yaml
logger:
  default: info
  logs:
    custom_components.larnitech: debug
```

Then read the log via **Settings → System → Logs**, or `ha core logs` over SSH.

## Versioning

See [`CHANGELOG.md`](CHANGELOG.md). The project follows
[Semantic Versioning](https://semver.org/).
