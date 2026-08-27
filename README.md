# Larnitech — Home Assistant integration

Custom integration connecting Home Assistant to Larnitech controllers over
the API2 WebSocket. Local or cloud connection, multiple controllers per HA
instance, push updates with a safety poll, and two-way control for most of
the Larnitech widget catalog.

## Requirements

- Home Assistant ≥ 2024.8.0
- A Larnitech controller reachable locally (API2 port) or via Larnitech
  Cloud, with an API2 key
- `websockets>=12.0` (installed automatically)

## Installation

### HACS (custom repository)

1. HACS → ⋮ → Custom repositories → add this repository, category
   "Integration"
2. Install "Larnitech", restart Home Assistant

No tagged release yet (see [TODO.md](TODO.md) → Distribution) — HACS
installs from `main` until the first tag is cut.

### Manual

Copy `custom_components/larnitech` into your HA `config/custom_components/`,
restart Home Assistant.

## Setup

Settings → Devices & Services → Add Integration → **Larnitech**. Choose a
local (host/port) or cloud (serial + key) connection. Multiple controllers
are supported — add one config entry per server.

## Supported widgets

Larnitech reports each device as a `type`/`sub-type` pair; the tables below
say what each pair becomes in Home Assistant and what you can do with it.

### Actuators

| type/sub-type | HA domain | What you get |
|---|---|---|
| `lamp` | `light` | On/off light |
| `lamp/socket` | `switch` | Power outlet |
| `lamp/lock` | `lock` | Electric lock / latch — lock and unlock |
| `lamp/air-fan` | `fan` | Extractor fan, on/off |
| `lamp/pump` | `switch` | Pump, on/off |
| `lamp/valve-3`, `lamp/damper` | `valve` | 3-way valve / damper — open and close |
| `lamp/dehumidifier` | `humidifier` | Dehumidifier, on/off |
| `lamp/closing-switch` | `switch` | Pulse closer, shown as a normal on/off switch |
| `dimmer-lamp` | `light` | On/off + brightness |
| `rgb-lamp` | `light` | On/off + brightness + colour |
| `light-scheme` | `switch` | Light scene, on/off. "Activate-only" scenes accept on but ignore off — that's the controller's behaviour, not a bug in the integration |

### Climate

| type/sub-type | HA domain | What you get |
|---|---|---|
| `climate-control` | `climate` | Full zone control. HVAC modes are always the same set: `heat_cool` lets the zone both heat and cool, `heat` or `cool` restricts it to that direction only — even when the assigned automation is capable of both. Larnitech automations appear as HA presets. Two setpoints (heating and cooling) are supported. HVAC action is shown, and a separate entity reports the heat/cool demand as a percentage (−100 cooling … +100 heating). The widget's ventilation, humidity and warm-floor sections are **not** supported — contact the developer if you need them. |
| `valve-heating` (+ `warm-floor`) | `climate` | Radiator valve / underfloor heating loop. Larnitech automations appear as presets, always alongside `Manual` and `Always-off`. The target temperature is adjustable only under a named preset — in `Manual`/`Always-off` nothing regulates it, so no setpoint is shown. Modes are `off` and `heat_cool`: the valve itself doesn't know whether it's plumbed for heating or cooling, so HA doesn't claim a direction. HVAC action shows `heating` while the widget's own automation is driving the valve, and nothing when the channel was switched on from elsewhere (e.g. by a `climate-control` zone using it as a cooling actuator). |
| `fancoil` | `climate` | Fancoil unit — `off`/`heat`/`cool`, target temperature, and fan speed in 10% steps. Larnitech automations appear as presets, alongside `Manual` and `Always-off`. |
| `AC` / `conditioner` | `climate` | Air conditioner — mode, target temperature, fan speed (Auto / 1st / 2nd / 3rd), and vertical and horizontal louvre position as swing controls. Which modes, speeds and louvres are offered follows what the device itself reports; changing that on the Larnitech side is picked up without restarting HA. |
| `vent` | `climate` | CO₂-driven ventilation. Larnitech automations appear as presets, alongside `Manual` and `Always-off`. Fan speed in 10% steps. The CO₂ setpoint is a separate entity (a number you can set); the live CO₂ reading stays on its own `co2-sensor` device. |
| `virtual/ventilation` | `climate` | HRV / supply ventilation unit (Komfovent and similar). Speed preset (Auto / Low / Middle / High); current and target temperature appear only if the widget has temperature sensors linked on the Larnitech side. |
| `valve` | `valve` | Main shut-off valve — open and close |

### Covers

| type/sub-type | HA domain | What you get |
|---|---|---|
| `blinds` | `cover` | Open / close / stop plus exact position |
| `jalousie` | `cover` | Open / close / stop. No position, and slat tilt is not supported |
| `gate` | `cover` | Open / close / stop. No position |

### Sensors

| type/sub-type | HA domain | What you get |
|---|---|---|
| `temperature-sensor` | `sensor` | Temperature, °C |
| `humidity-sensor` | `sensor` | Relative humidity, % |
| `co2-sensor` | `sensor` | CO₂, ppm |
| `illumination-sensor` | `sensor` | Illuminance, lx |
| `current-sensor` | `sensor` | Current, A — implemented, not yet confirmed on live hardware |
| `motion-sensor` | `binary_sensor` | Motion — implemented, not yet confirmed on live hardware |
| `leak-sensor` | `binary_sensor` | Water leak. If the sensor reports a fault instead of a reading, a separate "malfunction" entity shows it |
| `door-sensor` (no sub-type) | `binary_sensor` | Contact — implemented, not yet confirmed on live hardware |
| `door-sensor` (with sub-type) | `binary_sensor` | Generic contact input; the sub-type sets what it actually is — door, motion, fire, smoke, gas, CO₂, leak, glass break, lock or alarm — so HA shows the right icon and state wording |

### Buttons

| type/sub-type | HA domain | What you get |
|---|---|---|
| `switch` | `event` | Physical wall button (not a relay). Fires press, hold-repeat, short release and long release events with the hold duration — use it as an automation trigger |

### Virtual and meters

| type/sub-type | HA domain | What you get |
|---|---|---|
| `virtual/sensor` | `sensor` | Numeric value produced by a controller script, with full history and long-term statistics |
| `virtual/text`, `virtual/long-text` | `sensor` | Text value produced by a controller script. Long values are cut short in the state itself — the complete text is always available in the `full_text` attribute |
| `json/*` (e.g. `MBUS` meters) | `sensor` (several per device) | Heat and water meters. Every field the meter reports becomes its own sensor with the correct unit, picked up automatically — there is no fixed field list |

### Not implemented

Widget types with no Home Assistant entities at all:

- `security-card-reader` — access-control card reader
- `ir-receiver`, `ir-transmitter` — IR receive/send
- `remote-control` — IR/RF remote emulation
- `rtsp` — camera stream
- `speaker` — multiroom audio
- `intercom` — door intercom
- `item-ref` — reference to another widget
- `com-port` — raw serial port
- `virtual/plan` — floor plan image
- `virtual/sunrise` — sunrise/sunset times
- `virtual/prf`, `virtual/jalousie`, `virtual/gate` (and their `120`
  variants) — the controller reports no usable state for these
- `virtual/lamp`, `virtual/dimer-lamp`, `virtual/rgb-lamp` — the controller
  reports unreliable values for these
- `virtual/btunreg`, `json/btunreg` — internal diagnostic data, nothing
  useful to show

Gaps inside otherwise supported types:

- `climate-control` — the ventilation, humidity and warm-floor sections of
  the widget are not implemented yet.
- `jalousie` — slat tilt
- `valve-heating` — the cooling side: it always reports heating or nothing,
  never cooling
- `switch` (physical button) — double-click and other multi-press gestures
  beyond press / hold / release
- Local (LAN) auto-discovery — a controller has to be added by hand

Need any of these? Get in touch — see [Contact](#contact).

## Settings

### Connection settings

Set when the integration is added, changed later via the entry's **⋮ →
Reconfigure**. Kept separate from the everyday options below because
changing them is deliberate and has side effects.

| Setting | What it's for |
|---|---|
| **Connection type (cloud / local)** | How Home Assistant reaches the controller. Cloud goes through Larnitech's servers, local goes straight to the controller on your network. |
| **Serial number** | Which controller to connect to, in cloud mode. |
| **Host / IP** and **Port** | Where the controller lives on your network, in local mode. |
| **API key** | The controller's API key, taken from LT_Setup. |
| **Entity ID naming pattern** | How entity IDs are built. Four choices: serial + address (`sensor.eaffc8c1_110_42`, the default), "larnitech" + address (`sensor.larnitech_110_42`), room + device name (`sensor.kitchen_temperature`), or room + device name + address (`sensor.kitchen_temperature_110_42`). Changing it **renames every entity that already exists** on this controller — history follows the rename, but Home Assistant does not update dashboards, automations or scripts that point at the old IDs, so check those afterwards. |

### Options

Changed any time via the entry's **Configure** button; saving reloads the
integration.

| Option | What it does | Default |
|---|---|---|
| **Auto-remove devices that disappear or change type** | Deletes a device from Home Assistant once the controller stops reporting it, or recreates it if its type changed. If more than half the known devices vanish at once, nothing is deleted automatically — you get a repair notification asking to confirm first, since a drop that large is usually a connection problem rather than a real change. | on |
| **Auto-update names from Larnitech** | Keeps device and entity names matching what the controller calls them. Anything you renamed by hand in Home Assistant is left alone. | on |
| **Use Larnitech rooms** | Creates Home Assistant areas matching the rooms in Larnitech and puts each device in the right one. | on |
| **Auto-update device room placement** | Moves a device to a different area when its room changes in Larnitech. Requires the option above, and overrides an area you set by hand. | on |
| **Read-only mode** | Never sends anything to the controller. Controlling an entity from Home Assistant does nothing — it immediately snaps back to the controller's real state. Useful while testing, or on an installation you must not touch. | off |
| **Append the Larnitech address to entity names** | Adds the controller address to entity names, e.g. `Temperature (1:98)`, making it easy to match an entity back to the widget in Larnitech. Can be switched on and off freely; entities you renamed by hand keep your name. | off |
| **Poll interval** | How often Home Assistant re-reads everything from the controller as a safety net, in seconds (30-290). Updates normally arrive instantly by push — this only catches anything missed, and keeps the connection alive. | 120 |

## Diagnostics

Settings → Devices & Services → the Larnitech entry → ⋮ → **Download
diagnostics** — the entry's config (key redacted), connection state, a
census of every device type found, anything the integration couldn't map,
and the controller's full raw device snapshot. Attach it to a bug report:
almost every quirk found in this integration so far was only diagnosable
from that raw data.

### Logging

The integration logs whatever it cannot map or decode by default: unknown
device types, non-numeric sensor values, unrecognized button values,
undecodable frames, and failed writes — each once.

For full detail (a raw status dump per entity, raw button gesture values),
enable debug logging:

```yaml
logger:
  default: info
  logs:
    custom_components.larnitech: debug
```

Then read it via **Settings → System → Logs**, or `ha core logs` over SSH.

## Not yet verified on live hardware

Implemented, but no suitable device has been available to confirm it works
end to end. Report back if you have one:

- `current-sensor`, `motion-sensor`, `leak-sensor` and `door-sensor` (no
  sub-type) — these read correctly at rest, but the triggered state has
  never been observed on a real device

## Contributing

Issues and pull requests are welcome on GitHub. Found a device that doesn't
map correctly, or a Larnitech quirk not covered here? Open an issue with a
diagnostics download attached (see above) — that's the fastest path to a fix.
Also feel free to contact with me.

## Versioning

See [CHANGELOG.md](CHANGELOG.md). The project follows
[Semantic Versioning](https://semver.org/).

## License
[MIT](LICENSE)

## Contact

Mykhailo Popovych
- Telegram: [t.me/M_Popovych_ThinkHome](https://t.me/M_Popovych_ThinkHome)
- Phone (WhatsApp): +370 632 89 991, +380 99 333 99 96
- Email: [m.popovych@thinkhome.io](mailto:m.popovych@thinkhome.io)
