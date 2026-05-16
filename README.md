# Eveus EV Charger — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-4.7.0-blue?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.4%2B-41BDF5?style=for-the-badge&logo=home-assistant)

Local-only Home Assistant integration for Eveus EV chargers. Polls the charger directly over your LAN — no cloud, no account, no telemetry. Gives you live power/energy/cost telemetry, charging controls with optimistic UI, optional EV battery (SOC) estimates, adaptive-charging and scheduled-slot visibility, multi-charger support, and a small set of automation-friendly entities (Car Connected, Charging Finish Time, Session Cost) so you do not need to write template sensors.

## Highlights

- **Pure local polling.** No cloud, no account required. Reads directly from the charger's HTTP API on your LAN.
- **Source-of-truth numbers.** Session Cost and SOC are read from charger-native fields (`sessionMoney`, `sessionEnergy`), so the cost reflects the actual rate at each moment — no retroactive re-pricing when the tariff switches at 07:00.
- **Automation-ready.** Dedicated `binary_sensor.eveus_car_connected` (`device_class: plug`) and `sensor.eveus_charging_finish_time` (`device_class: timestamp`) replace the template sensors you would otherwise have to write.
- **Adaptive-charging visibility.** See when the charger throttles current to protect a weak supply, and what slot schedule is active.
- **Multi-charger.** Add as many Eveus chargers as you like to one HA instance.
- **Stable contracts.** Entity IDs and unique IDs are preserved across releases — existing dashboards and automations keep working.

## Requirements

| | |
| --- | --- |
| Home Assistant | 2024.4 or newer |
| Network | Eveus charger reachable from HA on the local network |
| Setup | Charger IP/hostname (or full URL), username, password, charger model |
| Supported models | 16A, 32A, 48A |

## Installation

### HACS

1. HACS → **Custom repositories** → add `https://github.com/ABovsh/eveus` as **Integration**.
2. Search for **Eveus EV Charger**, install, restart Home Assistant.

### Manual

1. Copy `custom_components/eveus` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.

## Setup

1. **Settings → Devices & Services → Add Integration → Eveus EV Charger**.
2. Enter the charger address: IP, hostname, or full URL. Use `https://` if the charger is configured for HTTPS; you can append `:port` if non-standard.
3. Enter the charger username and password.
4. Pick the charger model: 16A / 32A / 48A.

To change connection details later: **Settings → Devices & Services → Eveus EV Charger → Reconfigure**. To add another charger, run the same flow again with a different address.

## Entities

Every entity below is created automatically. Names and unique IDs are stable across 4.x releases. Diagnostic entities are placed under the device's *Diagnostic* section in HA.

### Live measurements

| Entity | Unit | Description |
| --- | --- | --- |
| Voltage | V | Live line voltage |
| Current | A | Live charging current |
| Power | W | Live charging power |
| Box Temperature | °C | Internal charger temperature *(diag)* |
| Plug Temperature | °C | Plug temperature *(diag)* |
| Battery Voltage | V | Charger backup battery *(diag)* |

### Energy & cost

| Entity | Unit | Description |
| --- | --- | --- |
| Session Energy | kWh | Energy delivered in the current session |
| Total Energy | kWh | Lifetime delivered energy |
| Counter A Energy | kWh | Resettable energy counter A |
| Counter B Energy | kWh | Resettable energy counter B |
| Counter A Cost | ₴ | Cost accumulated on counter A |
| Counter B Cost | ₴ | Cost accumulated on counter B |
| **Session Cost** | ₴ | Cost of the current session — read directly from the charger, integrated at the rate active at each moment (no retroactive re-pricing on tariff switch) |
| Primary Rate Cost | ₴/kWh | Primary electricity rate |
| Active Rate Cost | ₴/kWh | Currently active rate |
| Rate 2 Cost | ₴/kWh | Rate 2 price |
| Rate 3 Cost | ₴/kWh | Rate 3 price |

### Status & state

| Entity | Description |
| --- | --- |
| State | High-level charger state *(diag)* |
| Substate | Detailed substate / error label *(diag)* |
| Ground | Ground connection status *(diag)* |
| Session Time | Duration of the current session |
| System Time | Charger internal clock *(diag)* |
| Connection Quality | Recent success-rate %, with latency and health attributes *(diag)* |
| Rate 2 Status | Whether Rate 2 schedule is enabled *(diag)* |
| Rate 3 Status | Whether Rate 3 schedule is enabled *(diag)* |
| Input Entities Status | Reports missing/invalid optional SOC helpers *(diag)* |

### Adaptive charging & schedules *(new in 4.7.0)*

The charger has a built-in adaptive ("AI") mode that throttles current when the supply voltage sags, and two configurable time-window slots for scheduled charging. These entities expose that state so you can build dashboards and automations around them.

| Entity | Description |
| --- | --- |
| Adaptive Charging | `Active` / `Idle` — whether the charger is currently throttling current to protect a weak supply *(diag)* |
| Adaptive Current Limit | Current cap (A) chosen by the adaptive throttle *(diag)* |
| Adaptive Voltage Threshold | Voltage floor (V) below which the throttle engages *(diag)* |
| Schedule 1 | `Enabled` / `Disabled` with attributes `window` (HH:MM–HH:MM), `start`, `stop`, optional `current_limit_a`, `energy_limit_kwh` *(diag)* |
| Schedule 2 | Same as Schedule 1 for the second slot *(diag)* |

### Automation-friendly entities

These exist specifically to replace template sensors users typically build on top of Eveus.

| Entity | Type | Description |
| --- | --- | --- |
| `binary_sensor.eveus_car_connected` | `device_class: plug` | `on` whenever a vehicle is electrically connected (Connected, Charging, Charge Complete, or Paused). Uses canonical numeric state values — stable across charger firmware label changes |
| `sensor.eveus_charging_finish_time` | `device_class: timestamp` | Absolute UTC time when target SOC will be reached. Minute-aligned so it does not jitter every poll. Returns unavailable when not charging, helpers missing, or target already reached |

### Controls

| Entity | Type | Description |
| --- | --- | --- |
| Charging Current | Number | Current-limit slider, model-aware bounds (16/32/48 A) |
| Stop Charging | Switch | Charger-side stop-charge option |
| One Charge | Switch | Single charging session |
| Reset Counter A | Switch | Reset energy counter A |

Controls use **optimistic UI**: the slider/switch updates immediately, then reconciles with the next charger response.

### Optional SOC sensors

Created automatically. Show as *unavailable* until you add the helpers in the next section.

| Entity | Description |
| --- | --- |
| SOC Energy | Estimated battery energy in kWh |
| SOC Percent | Estimated battery percentage |
| Time to Target SOC | Human-readable ETA to target SOC (e.g. `2h 15m`). For automations prefer `Charging Finish Time` |

## Optional SOC helpers

SOC tracking is optional. Energy/cost/controls/diagnostics all work without it. To enable SOC estimates, create these four `input_number` helpers in **Settings → Devices & Services → Helpers**:

| Helper entity ID | Name | Unit | Min | Max | Step | Initial |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `input_number.ev_battery_capacity` | EV Battery Capacity | kWh | 10 | 160 | 1 | 80 |
| `input_number.ev_initial_soc` | Initial EV State of Charge | % | 0 | 100 | 1 | 20 |
| `input_number.ev_soc_correction` | Charging Efficiency Loss | % | 0 | 15 | 0.1 | 7.5 |
| `input_number.ev_target_soc` | Target SOC | % | 0 | 100 | 5 | 80 |

The **Input Entities Status** diagnostic sensor tells you which helpers are still missing or out of range.

### How SOC is calculated

SOC uses the charger's native `sessionEnergy` field (kWh delivered in the current session). The charger resets it to zero on every new plug-in, so the integration does not need to snapshot or persist a baseline across restarts — the behavior is structural.

```
charged kWh = sessionEnergy
usable kWh  = sessionEnergy × (1 − charging_loss / 100)
SOC %       = initial_soc + (usable kWh / battery_capacity) × 100
```

Example — 80 kWh battery, Initial SOC 20%, loss 10%:

```
sessionEnergy = 10 kWh  →  usable = 9    kWh  →  SOC = 31%
sessionEnergy = 16 kWh  →  usable = 14.4 kWh  →  SOC = 38%
```

Changing **Initial SOC** mid-session is fine — SOC reprojects from the new value on the next poll.

**Split-charging across plug-out/plug-in:** the charger starts a fresh session every time the cable is reinserted, so `sessionEnergy` resets to 0. If you unplug at 50% and plug back in later, update `input_number.ev_initial_soc` to the current dashboard value (manually, or from an automation triggered by `binary_sensor.eveus_car_connected` going `off`) before charging resumes — otherwise SOC will project from the old value.

## Troubleshooting

### Setup cannot connect

- Confirm the charger is powered on and connected to Wi-Fi.
- Open `http://<charger-ip>` from a browser on the same network.
- Verify the IP / hostname.
- Confirm HA can reach the charger's network segment.
- Confirm the selected charger model matches the real device.

### Controls do not respond

- Check **Connection Quality**.
- Confirm the charger is online.
- Verify the stored credentials via **Reconfigure**.
- Wait one coordinator refresh after sending a command.

### SOC sensors are unavailable

- Create the optional `input_number.ev_*` helpers (see above).
- Check **Input Entities Status** for missing / invalid helpers.
- Confirm helper values are numeric and within the declared range.

### Charger is powered off

This is expected. The integration backs off polling automatically and keeps Home Assistant logs quiet — no spam in the log book.

## Reliability notes

- Multi-charger: add as many Eveus chargers as you want.
- Setup validates reachability and an Eveus-compatible response before creating the entry.
- HA diagnostics download is supported, with credentials redacted.
- Repair flow handles rare invalid stored setup data.
- Coordinator-driven updates skip state writes when nothing changed.
- Existing entity IDs / unique IDs are preserved across the 4.x line — dashboards and automations from older versions continue to work.

## Support

Bug reports, feature requests, and release discussions: [github.com/ABovsh/eveus/issues](https://github.com/ABovsh/eveus/issues).

## License

MIT.
