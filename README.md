# Eveus Home Assistant Integration

> **Eveus EV charger integration for Home Assistant** — installable via HACS. Local-only polling over your LAN, no cloud, no account.

💬 **Discussion:** [Home Assistant Community thread](https://community.home-assistant.io/t/eveus-ev-charger-home-assistant-integration-local-only-hacs/1010628)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-4.10.0-blue?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.4%2B-41BDF5?style=for-the-badge&logo=home-assistant)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ABovsh_eveus)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=reliability_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=reliability_rating)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=security_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=security_rating)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=sqale_rating)
[![Coverage](https://img.shields.io/sonar/coverage/ABovsh_eveus?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonarcloud&label=coverage)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=coverage)

Local-only Home Assistant integration for Eveus EV chargers. Polls the charger directly over your LAN — no cloud, no account, no telemetry. Gives you live power/energy/cost telemetry, charging controls with optimistic UI, optional EV battery (SOC) estimates, adaptive-charging and scheduled-slot visibility, multi-charger support, and a small set of automation-friendly entities (Car Connected, Charging Finish Time, Session Cost) so you do not need to write template sensors.

## Highlights

### 🔌 Local-only, no cloud
Polls the charger directly over your LAN via its HTTP API. No Eveus account, no cloud relay, no telemetry leaving your network. Works fully offline-from-the-internet. Supports `http://` or `https://`, custom ports, and either IP or hostname.

### ⚡ Live electrical telemetry
Voltage, current, power, and the active current-limit setpoint, refreshed on every coordinator tick. Box temperature, plug temperature, ground status, and the charger's internal backup battery voltage round out the diagnostic picture.

### 💰 Source-of-truth energy & cost
- **Session Energy**, **Total Energy**, and two resettable counters (**Counter A/B**) — kWh delivered.
- **Counter A/B Cost** — running ₴ cost on each counter.
- **Session Cost** — reads the charger's native `sessionMoney` field, so it is integrated at the rate that was active at each moment. The session total never jumps backward or forward when the tariff switches mid-session (e.g. night→day at 07:00).
- **Primary / Active / Rate 2 / Rate 3 Cost** — current electricity rates in ₴/kWh, plus diagnostic status sensors showing which rate schedules are enabled.

### 🔋 EV battery (SOC) estimates
Pick a **SOC monitoring** mode during setup:
- **Basic** — charging control only, no SOC sensors.
- **Advanced** — the integration auto-creates four native `number.eveus_*` entities (Initial SOC, Target SOC, Battery Capacity, SOC Correction) and estimates:
  - **SOC Energy** in kWh, **SOC Percent**, and **Time to Target SOC** as a human-readable string for cards.
  - **Charging Finish Time** as a `device_class: timestamp` for automations and timestamp cards.

No manual `input_number` helpers are needed. Switch modes anytime from the integration's **Configure** dialog. SOC math uses the charger-native `sessionEnergy` so there is no fragile per-restart baseline state to manage.

### 🤖 Adaptive charging & schedule visibility
Eveus chargers can throttle current automatically when the supply voltage sags, and run two configurable time-window slots for scheduled charging. The integration exposes both:
- **Adaptive Charging** (`Active`/`Idle`), **Adaptive Current Limit** (A), **Adaptive Voltage Threshold** (V).
- **Schedule 1** / **Schedule 2** — enabled/disabled state with `window` (HH:MM–HH:MM), optional current and energy caps as attributes.

### 🎛 Charging controls with optimistic UI
- **Charging Current** number slider with model-aware bounds (16 / 32 / 48 A).
- **Stop Charging** and **One Charge** switches; **Reset Counter A** and **Reset Counter B** buttons.

All controls update the UI immediately and reconcile with the charger on the next poll — no waiting on the round-trip before the slider moves.

### 🤝 Automation-friendly entities (no templates required)
Designed to replace the template sensors users typically build on top of Eveus:
- **`binary_sensor.eveus_car_connected`** (`device_class: plug`) — `on` whenever a vehicle is electrically connected. Uses canonical numeric state values from the charger, so it survives firmware label changes.
- **`sensor.eveus_charging_finish_time`** (`device_class: timestamp`) — absolute UTC time when target SOC will be reached. Minute-aligned to avoid state jitter every poll. Returns unavailable when not charging or target already reached.

### 📊 Reliability & connection health
- **Connection Quality** sensor exposes recent success-rate %, average latency, and a health label — useful both as a dashboard tile and as an automation trigger.
- Powered-off / unreachable chargers are treated as a normal condition. Polling backs off automatically and HA logs stay quiet — no spam in the log book.
- Coordinator-driven updates skip state writes when nothing changed, so the recorder database doesn't bloat.

### 🏠 Multi-charger support
Add as many Eveus chargers as you want to one HA instance. Each charger gets its own device, entity namespace (`sensor.eveus_*`, `sensor.eveus2_*`, …), and independent coordinator. Reconfigure or reauthenticate any of them without touching the others.

### 🧰 Setup quality of life
- **Reconfigure** flow to update IP, credentials, or charger model without reinstalling.
- **Reauthentication** flow when the charger rejects stored credentials.
- Setup validates reachability and verifies the response looks like a real Eveus charger before creating the entry.
- **Repair flow** for rare invalid stored setup data.
- **HA Diagnostics** download with sensitive fields redacted.

### 🔒 Stable contracts
Entity IDs and unique IDs are preserved across the 4.x line. Dashboards, automations, scripts, and statistics history from earlier versions keep working after every update.

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
| Leakage Current | mA | Live RCD reading; `0` is normal, non-zero signals a ground-fault leak *(diag, new in 4.9.0)* |
| Leakage Current Peak | mA | Peak-hold leakage reading from the firmware *(diag, new in 4.9.0)* |

When the integration is configured for a **3-phase** charger (`Phases = 3` in setup), four additional measurement sensors are exposed: `Current Phase 2`, `Current Phase 3`, `Voltage Phase 2`, `Voltage Phase 3`. *(new in 4.9.0)*

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

### Adaptive charging & schedules

The charger has a built-in adaptive mode that throttles current when the supply voltage sags, and two configurable time-window slots for scheduled charging. These entities expose that state so you can build dashboards and automations around them.

| Entity | Description |
| --- | --- |
| Adaptive Charging | `Active` / `Idle` — whether the charger is currently throttling current to protect a weak supply *(diag)* |
| Adaptive Current Limit | Current cap (A) chosen by the adaptive throttle *(diag)* |
| Adaptive Voltage Threshold | Voltage floor (V) below which the throttle engages *(diag)* |
| Schedule 1 | `Enabled` / `Disabled` with attributes `window` (HH:MM–HH:MM), `start`, `stop`, optional `current_limit_a`, `energy_limit_kwh` *(diag)* |
| Schedule 2 | Same as Schedule 1 for the second slot *(diag)* |
| Schedule 1 / 2 Enabled | Switch — arm or disarm each on-device schedule slot |
| Schedule 1 / 2 Start, Stop | `time` entities — native HH:MM picker for each slot's window |

### Automation-friendly entities

These exist specifically to replace template sensors users typically build on top of Eveus.

| Entity | Type | Description |
| --- | --- | --- |
| `binary_sensor.eveus_car_connected` | `device_class: plug` | `on` whenever a vehicle is electrically connected (Connected, Charging, Charge Complete, or Paused). Uses canonical numeric state values — stable across charger firmware label changes |
| `sensor.eveus_charging_finish_time` | `device_class: timestamp` | Absolute UTC time when target SOC will be reached. Minute-aligned so it does not jitter every poll. Returns unavailable when not charging, in Basic mode, or target already reached |

### Controls

| Entity | Type | Description |
| --- | --- | --- |
| Charging Current | Number | Current-limit slider, model-aware bounds (16/32/48 A) |
| Stop Charging | Switch | Charger-side stop-charge option |
| One Charge | Switch | Single charging session |
| Adaptive Mode | Switch | Toggle the charger's adaptive (AI) throttle on/off *(new in 4.9.0)* |
| Time Zone | Select | Charger time-zone offset, `-12..+14` *(new in 4.9.0)* |
| Force Refresh | Button | Force an immediate coordinator poll |
| Sync Time | Button | Push the host's current UTC time to the charger's clock *(new in 4.9.0)* |
| Reset Counter A | Button | Reset energy counter A (one-shot action) |
| Reset Counter B | Button | Reset energy counter B (one-shot action) |

Controls use **optimistic UI**: the slider/switch updates immediately, then reconciles with the next charger response.

### SOC sensors (Advanced mode)

Created automatically in **Advanced** SOC mode. Driven by the native `number.eveus_ev_charger_*` SOC entities (see below; replace `eveus_ev_charger` with your device slug if different).

| Entity | Description |
| --- | --- |
| SOC Energy | Estimated battery energy in kWh |
| SOC Percent | Estimated battery percentage |
| Time to Target SOC | Human-readable ETA to target SOC (e.g. `2h 15m`). For automations prefer `Charging Finish Time` |

## Dashboard

A ready-to-paste Lovelace view exposing every Eveus capability — live status tiles, all writable controls (current slider, switches, time-zone select, refresh / sync / reset buttons), both on-device schedule slots with native HH:MM time pickers, adaptive-mode card, 24-hour mini-graph charts, session totals, tariffs, and diagnostics — ships in [`docs/dashboard.yaml`](docs/dashboard.yaml).

**Requirements:** the [`mini-graph-card`](https://github.com/kalkih/mini-graph-card) HACS frontend plugin.

**Install:** open your dashboard → ⋮ → *Edit dashboard* → ⋮ → *Raw configuration editor*, then paste the view under `views:`. If your device slug differs from `eveus_ev_charger`, do a find-and-replace.

## Screenshots

See the dashboard YAML used to render these views: [`docs/dashboard.yaml`](docs/dashboard.yaml).
<img width="1189" height="711" alt="image" src="https://github.com/user-attachments/assets/7a591592-7d0e-49a4-ac46-a8232638fc42" />
<img width="1185" height="577" alt="image" src="https://github.com/user-attachments/assets/c3b1f004-8b01-408b-8dfe-c84823009d2b" />


## SOC monitoring

SOC tracking is optional. Energy/cost/controls/diagnostics all work without it. Pick the mode during setup (or change it later under **Settings → Devices & Services → Eveus → Configure**):

- **Basic** — charging control only. No SOC sensors are created.
- **Advanced** — the integration creates four native input entities automatically. No manual `input_number` helpers required:

| Entity ID | Name | Unit | Min | Max | Step | Default |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `number.eveus_battery_capacity` | Battery Capacity | kWh | 10 | 160 | 1 | 50 |
| `number.eveus_initial_soc` | Initial SOC | % | 0 | 100 | 1 | 20 |
| `number.eveus_soc_correction` | SOC Correction | % | 0 | 20 | 0.5 | 7.5 |
| `number.eveus_target_soc` | Target SOC | % | 0 | 100 | 5 | 80 |

> **Migrating from older versions:** existing SOC users are moved to **Advanced** automatically, with the old helper values carried over. Repoint dashboard cards and automations by replacing the prefix `input_number.ev_` with `number.eveus_`, then delete the old helpers when nothing references them.

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

**Split-charging across plug-out/plug-in:** the charger starts a fresh session every time the cable is reinserted, so `sessionEnergy` resets to 0. If you unplug at 50% and plug back in later, update `number.eveus_initial_soc` to the current dashboard value (manually, or from an automation triggered by `binary_sensor.eveus_car_connected` going `off`) before charging resumes — otherwise SOC will project from the old value.

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

- Confirm SOC monitoring is set to **Advanced** (Settings → Devices & Services → Eveus → Configure). Basic mode creates no SOC sensors.
- Confirm the charger is online — SOC sensors populate once the first poll succeeds.
- Check the `number.eveus_ev_charger_*` SOC entities hold sensible values (battery capacity, initial/target SOC, correction). If your device slug differs, use your device page to find the matching SOC number entities.

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
