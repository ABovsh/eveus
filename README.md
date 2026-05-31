# Eveus EV Charger for Home Assistant

> Local-only Home Assistant integration for Eveus EV chargers. Control charging, monitor power and cost, estimate EV battery SOC, expose schedules and adaptive charging, and build automations without template sensors.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-4.10.0-blue?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.4%2B-41BDF5?style=for-the-badge&logo=home-assistant)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ABovsh_eveus)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=reliability_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=reliability_rating)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=security_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=security_rating)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=sqale_rating)
[![Coverage](https://img.shields.io/sonar/coverage/ABovsh_eveus?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonarcloud&label=coverage)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=coverage)

- Discussion: [Home Assistant Community thread](https://community.home-assistant.io/t/eveus-ev-charger-home-assistant-integration-local-only-hacs/1010628)
- Issues: [github.com/ABovsh/eveus/issues](https://github.com/ABovsh/eveus/issues)

<img width="1189" alt="Eveus dashboard overview" src="https://github.com/user-attachments/assets/7a591592-7d0e-49a4-ac46-a8232638fc42" />

Local-only Home Assistant integration for Eveus EV chargers. It polls the charger directly over your LAN — no cloud, no account, no telemetry — and gives you live power/energy/cost telemetry, charging controls with optimistic UI, native EV battery (SOC) estimates, adaptive-charging and scheduled-slot visibility, multi-charger support, and automation-friendly entities so you never have to write a template sensor.

## ✨ Highlights

### 🔌 Local-only, no cloud
Talks to the charger directly over your LAN via its HTTP API. No Eveus account, no cloud relay, no telemetry leaving your network — it keeps working when the internet is down. Supports `http://` or `https://`, custom ports, and either IP or hostname.

### ⚡ Live electrical telemetry
Voltage, current, power, and the active current-limit setpoint refreshed on every poll — plus per-phase voltage/current on 3-phase setups, box and plug temperatures, ground status, and the charger's internal backup-battery voltage.

### 💰 Source-of-truth energy & cost
Session Energy, Total Energy, and two resettable counters (A/B) in kWh, each with a running ₴ cost. **Session Cost** reads the charger's native money field, so it is integrated at the rate active at each moment and never jumps when the tariff switches mid-session (e.g. night→day at 07:00). Primary / Active / Rate 2 / Rate 3 prices are exposed too.

### 🔋 EV battery SOC — no helpers needed
Pick **Advanced** mode at setup and the integration creates its own SOC inputs as native entities — `number.eveus_initial_soc`, `number.eveus_target_soc`, `number.eveus_battery_capacity`, `number.eveus_soc_correction` — and the SOC Energy / SOC Percent / Time-to-Target / Charging Finish Time sensors. No `input_number` helpers to create by hand. Pick **Basic** if you only want charging control. Switch modes anytime from **Configure**.

### 🤖 Adaptive charging & schedule visibility
Toggle the charger's adaptive throttle and read its selected current cap and voltage threshold. Both on-device schedule slots are exposed as switches plus native HH:MM time pickers, with summary sensors.

### 🧩 Automation-ready entities
`Car Connected`, `Session Active`, `Charging Finish Time` (a real `timestamp`), `Session Cost`, schedule controls, and `Connection Quality` are first-class entities — no template sensors to maintain.

### 🛰️ Multi-charger
Add multiple Eveus chargers; each gets its own device, coordinator, and entity namespace.

### 🩺 Built for messy LAN reality
Offline chargers are handled quietly, polling backs off, commands surface a Home Assistant error toast on failure, and diagnostics downloads redact credentials and identifying fields.

## Requirements

| Requirement | Details |
| --- | --- |
| Home Assistant | 2024.4 or newer |
| Charger | Eveus 16A, 32A, or 48A charger reachable from Home Assistant |
| Network | Local LAN access to the charger HTTP API |
| Setup details | Charger IP/hostname or URL, username, password, model |

## Installation

### HACS

1. HACS -> **Custom repositories**.
2. Add `https://github.com/ABovsh/eveus` as **Integration**.
3. Install **Eveus EV Charger**.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/eveus` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.

## Setup

1. Go to **Settings -> Devices & Services -> Add Integration**.
2. Search for **Eveus EV Charger**.
3. Enter the charger address. IP, hostname, full URL, custom port, `http://`, and `https://` are supported.
4. Enter username and password.
5. Pick the charger model: **16A**, **32A**, or **48A**.
6. Pick SOC monitoring mode:
   - **Basic**: charging control only, no SOC sensors.
   - **Advanced**: creates SOC input numbers and SOC/ETA sensors.

Use **Configure** to switch Basic/Advanced later. Use **Reconfigure** to change host, credentials, model, or phase count without reinstalling.

## Entity IDs

The tables below show default entity IDs for the first charger named **Eveus EV Charger**. Home Assistant may add suffixes if you rename entities or add multiple chargers. Unique IDs are stable, so your history and dashboard cards survive normal updates.

### Charging State And Controls

| Entity ID | Type | What it gives you |
| --- | --- | --- |
| `sensor.eveus_ev_charger_state` | Sensor | Main charger state, such as standby, charging, complete, or error |
| `sensor.eveus_ev_charger_substate` | Sensor | Detailed charger substate or error label |
| `binary_sensor.eveus_ev_charger_car_connected` | Binary sensor | Vehicle is electrically connected |
| `binary_sensor.eveus_ev_charger_session_active` | Binary sensor | Charging session is active or paused |
| `number.eveus_ev_charger_charging_current` | Number | Current limit slider with model-aware bounds |
| `switch.eveus_ev_charger_stop_charging` | Switch | Stop/allow charging from the charger side |
| `switch.eveus_ev_charger_one_charge` | Switch | Enable one-charge mode |
| `button.eveus_ev_charger_force_refresh` | Button | Poll the charger immediately |

### Live Electrical Data

| Entity ID | Unit | What it gives you |
| --- | --- | --- |
| `sensor.eveus_ev_charger_voltage` | V | Line voltage |
| `sensor.eveus_ev_charger_current` | A | Charging current |
| `sensor.eveus_ev_charger_power` | W | Charging power |
| `sensor.eveus_ev_charger_current_set` | A | Charger current setpoint |
| `sensor.eveus_ev_charger_current_phase_2` | A | Phase 2 current when `Phases = 3` |
| `sensor.eveus_ev_charger_current_phase_3` | A | Phase 3 current when `Phases = 3` |
| `sensor.eveus_ev_charger_voltage_phase_2` | V | Phase 2 voltage when `Phases = 3` |
| `sensor.eveus_ev_charger_voltage_phase_3` | V | Phase 3 voltage when `Phases = 3` |

### Energy, Cost, And Tariffs

| Entity ID | Unit | What it gives you |
| --- | --- | --- |
| `sensor.eveus_ev_charger_session_energy` | kWh | Energy delivered in the current session |
| `sensor.eveus_ev_charger_total_energy` | kWh | Lifetime energy counter |
| `sensor.eveus_ev_charger_counter_a_energy` | kWh | Resettable energy counter A |
| `sensor.eveus_ev_charger_counter_b_energy` | kWh | Resettable energy counter B |
| `sensor.eveus_ev_charger_session_cost` | UAH | Current session cost from the charger |
| `sensor.eveus_ev_charger_counter_a_cost` | UAH | Cost accumulated on counter A |
| `sensor.eveus_ev_charger_counter_b_cost` | UAH | Cost accumulated on counter B |
| `sensor.eveus_ev_charger_primary_rate_cost` | UAH/kWh | Primary tariff |
| `sensor.eveus_ev_charger_active_rate_cost` | UAH/kWh | Currently active tariff |
| `sensor.eveus_ev_charger_rate_2_cost` | UAH/kWh | Rate 2 price |
| `sensor.eveus_ev_charger_rate_3_cost` | UAH/kWh | Rate 3 price |
| `sensor.eveus_ev_charger_rate_2_status` | Sensor | Whether Rate 2 schedule is enabled |
| `sensor.eveus_ev_charger_rate_3_status` | Sensor | Whether Rate 3 schedule is enabled |

### SOC And ETA, Advanced Mode

Advanced mode creates four native input numbers. Older `input_number.ev_*` helpers are no longer read.

| Entity ID | Unit | Range | Default | What it gives you |
| --- | --- | --- | --- | --- |
| `number.eveus_initial_soc` | % | 0-100, step 1 | 20 | Battery SOC at the start of the current charging session |
| `number.eveus_target_soc` | % | 0-100, step 5 | 80 | Target SOC for ETA calculations |
| `number.eveus_battery_capacity` | kWh | 10-160, step 1 | 50 | EV battery capacity |
| `number.eveus_soc_correction` | % | 0-20, step 0.5 | 7.5 | Charging loss correction |
| `sensor.eveus_ev_charger_soc_energy` | kWh | - | - | Estimated energy currently in the EV battery |
| `sensor.eveus_ev_charger_soc_percent` | % | - | - | Estimated battery percentage |
| `sensor.eveus_ev_charger_time_to_target_soc` | Sensor | - | - | Human-readable ETA to target SOC |
| `sensor.eveus_ev_charger_charging_finish_time` | Timestamp | - | - | Absolute finish time for automations and timestamp cards |

Migration from old helpers is intentionally simple: replace the prefix `input_number.ev_` with `number.eveus_` in cards and automations. For example, `input_number.ev_initial_soc` becomes `number.eveus_initial_soc`.

SOC uses the charger's native `sessionEnergy` value. The charger resets this value on every new plug-in, so continuous charging sessions survive Home Assistant restarts without a synthetic baseline. If you unplug and later resume charging, update `number.eveus_initial_soc` to the current battery percentage before the next session starts.

### Adaptive Charging And Schedules

| Entity ID | Type | What it gives you |
| --- | --- | --- |
| `switch.eveus_ev_charger_adaptive_mode` | Switch | Toggle the charger adaptive throttle |
| `sensor.eveus_ev_charger_adaptive_charging` | Sensor | Whether adaptive throttling is active |
| `sensor.eveus_ev_charger_adaptive_current_limit` | A | Current cap selected by adaptive mode |
| `sensor.eveus_ev_charger_adaptive_voltage_threshold` | V | Voltage floor for adaptive throttling |
| `switch.eveus_ev_charger_schedule_1_enabled` | Switch | Enable or disable schedule slot 1 |
| `time.eveus_ev_charger_schedule_1_start` | Time | Schedule 1 start time |
| `time.eveus_ev_charger_schedule_1_stop` | Time | Schedule 1 stop time |
| `sensor.eveus_ev_charger_schedule_1` | Sensor | Schedule 1 summary and attributes |
| `switch.eveus_ev_charger_schedule_2_enabled` | Switch | Enable or disable schedule slot 2 |
| `time.eveus_ev_charger_schedule_2_start` | Time | Schedule 2 start time |
| `time.eveus_ev_charger_schedule_2_stop` | Time | Schedule 2 stop time |
| `sensor.eveus_ev_charger_schedule_2` | Sensor | Schedule 2 summary and attributes |

### Diagnostics And Maintenance

| Entity ID | Unit | What it gives you |
| --- | --- | --- |
| `sensor.eveus_ev_charger_connection_quality` | % | Recent polling success, latency, and health attributes |
| `sensor.eveus_ev_charger_ground` | Sensor | Ground status |
| `sensor.eveus_ev_charger_system_time` | Sensor | Charger internal clock |
| `sensor.eveus_ev_charger_box_temperature` | °C | Charger body temperature |
| `sensor.eveus_ev_charger_plug_temperature` | °C | Plug temperature |
| `sensor.eveus_ev_charger_battery_voltage` | V | Charger backup battery voltage |
| `sensor.eveus_ev_charger_leakage_current` | mA | Current leakage reading |
| `sensor.eveus_ev_charger_leakage_current_peak` | mA | Peak leakage reading |
| `sensor.eveus_ev_charger_wifi_signal` | dBm | Charger Wi-Fi signal |
| `select.eveus_ev_charger_time_zone` | Select | Charger time-zone offset, `-12` to `+14` |
| `button.eveus_ev_charger_sync_time` | Button | Push Home Assistant time to the charger |
| `button.eveus_ev_charger_reset_counter_a` | Button | Reset counter A |
| `button.eveus_ev_charger_reset_counter_b` | Button | Reset counter B |
| `update.eveus_ev_charger_update` | Update | HACS update entity |
| `switch.eveus_ev_charger_pre_release` | Switch | HACS pre-release toggle |

## Dashboard

A complete, ready-to-paste Lovelace view that exposes **every Eveus capability** ships at [`docs/dashboard.yaml`](docs/dashboard.yaml). It covers:

- Live status tiles (state, car connected, voltage/current/power)
- SOC card with the four `number.eveus_*` inputs and the SOC/ETA sensors
- All writable controls — current slider, stop / one-charge switches, adaptive mode, time-zone select, sync / refresh / reset buttons
- Both on-device schedule slots with native HH:MM time pickers
- Session totals, lifetime counters, tariffs, and session cost
- Diagnostics — temperatures, leakage, Wi-Fi signal, connection quality
- 24-hour mini-graph charts for power, current, and temperatures

**Requirements:** the [`mini-graph-card`](https://github.com/kalkih/mini-graph-card) HACS frontend plugin (for the graph cards).

**Install:** open your dashboard → **⋮ → Edit dashboard → ⋮ → Raw configuration editor**, then paste the view under `views:`. If your device slug differs from `eveus_ev_charger`, use Home Assistant's entity picker or a find-and-replace.

<img width="1189" alt="Eveus dashboard overview" src="https://github.com/user-attachments/assets/7a591592-7d0e-49a4-ac46-a8232638fc42" />
<img width="1185" alt="Eveus dashboard details" src="https://github.com/user-attachments/assets/c3b1f004-8b01-408b-8dfe-c84823009d2b" />

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Setup cannot connect | Charger is powered on, HA can reach the charger IP/hostname, credentials are correct, selected model matches the charger |
| Controls do not respond | Connection Quality, charger online state, credentials via Reconfigure, then wait one coordinator refresh |
| SOC sensors are missing | SOC monitoring is set to Advanced under Configure, then restart/reload the integration if just changed |
| SOC looks wrong after unplug/replug | Update `number.eveus_initial_soc` to the real battery percentage before starting the next session |
| Charger is powered off | This is normal. Polling backs off and the integration avoids log spam |

## Privacy And Diagnostics

The integration stores only the charger connection details needed by Home Assistant. Diagnostics downloads redact credentials and identifying fields before export. Charger communication stays on your LAN.

## License

MIT.
