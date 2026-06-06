# Eveus EV Charger for Home Assistant

> Local-only Home Assistant integration for Eveus EV chargers. Control charging, monitor power and cost, estimate EV battery SOC, expose schedules and adaptive charging, surface dangerous-condition safety notices, and build automations without template sensors.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-4.12--rc-blue?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-41BDF5?style=for-the-badge&logo=home-assistant)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ABovsh_eveus)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=reliability_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=reliability_rating)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=security_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=security_rating)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=sqale_rating)
[![Coverage](https://img.shields.io/sonar/coverage/ABovsh_eveus?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonarcloud&label=coverage)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=coverage)

- Discussion: [Home Assistant Community thread](https://community.home-assistant.io/t/eveus-ev-charger-home-assistant-integration-local-only-hacs/1010628)
- Issues: [github.com/ABovsh/eveus/issues](https://github.com/ABovsh/eveus/issues)

<img width="1063" height="763" alt="image" src="https://github.com/user-attachments/assets/4c9ece28-8977-47d0-8fbc-78a69b95dac9" />

Local-only Home Assistant integration for Eveus EV chargers. It polls the charger directly over your LAN — no cloud, no account, no telemetry — and gives you live power/energy/cost telemetry, charging controls with optimistic UI, native EV battery (SOC) estimates, adaptive-charging and scheduled-slot visibility, optional OCPP backend control, multi-charger support, a localized (English / Ukrainian) UI, proactive safety notices for dangerous charger conditions, and automation-friendly entities so you never have to write a template sensor.

## ✨ Highlights

### 🔌 Local-only, no cloud
Talks to the charger directly over your LAN via its HTTP API. No Eveus account, no cloud relay, no telemetry leaving your network — it keeps working when the internet is down. Supports `http://` or `https://`, custom ports, and either IP or hostname.

### ⚡ Live electrical telemetry
Voltage, current, power, and the active current-limit setpoint refreshed on every poll — plus per-phase voltage/current on 3-phase setups, box and plug temperatures, ground status, and the charger's internal backup-battery voltage.

### 💰 Source-of-truth energy & cost
Session Energy, Total Energy, and two resettable counters (A/B) in kWh, each with a running ₴ cost. **Session Cost** reads the charger's native money field, so it is integrated at the rate active at each moment and never jumps when the tariff switches mid-session (e.g. night→day at 07:00). Primary / Active / Rate 2 / Rate 3 prices are exposed too.

### 🔋 EV battery SOC — no helpers needed
Pick **Advanced** mode at setup and the integration creates its own SOC inputs as native entities — `number.eveus_ev_charger_initial_soc`, `number.eveus_ev_charger_target_soc`, `number.eveus_ev_charger_battery_capacity`, `number.eveus_ev_charger_soc_correction` — and the SOC Energy / SOC Percent / Time-to-Target / Charging Finish Time sensors. No `input_number` helpers to create by hand. Pick **Basic** if you only want charging control. Switch modes anytime from **Configure**.

### 🤖 Adaptive charging & schedule visibility
Toggle the charger's adaptive throttle and read its selected current cap and voltage threshold. Both on-device schedule slots are exposed as switches plus native HH:MM time pickers, with summary sensors.

### 🧩 Automation-ready entities
`Car Connected`, `Session Active`, `Charging Finish Time` (a real `timestamp`), `Session Cost`, schedule controls, and `Connection Quality` are first-class entities — no template sensors to maintain.

### ☁️ OCPP backend control
Connect the charger to its OCPP backend (for the Eveus mobile app or a charging-network operator) with a single switch, and watch the live connection state via a dedicated binary sensor. While OCPP is on, a Repairs warning explains that Charging Current, limits, and schedules may be overridden by the backend — and how to turn it back off.

### 🌐 Localized UI
Ships English and Ukrainian translations; Home Assistant renders entity and config-flow text in the user's language automatically.

### 🛰️ Multi-charger
Add multiple Eveus chargers; each gets its own device, coordinator, and entity namespace.

### 🛡️ Safety watchdog
Surfaces dangerous charger conditions as Home Assistant Repairs — missing ground, current leakage, box/plug overheating, and the charger's relay, pilot, diode, overcurrent, voltage, GFCI-test, interface, and software faults. The charger's own firmware faults alert immediately, while raw grounding, temperature, and leakage readings use confirmation counting and recovery hysteresis so a single glitchy poll can't raise a false alarm. Grounding notices clear themselves after recovery; serious incidents stay visible until you acknowledge them. It reports only — it never sends charger commands and does not replace the charger's built-in protection or a qualified electrician.

### 🩺 Built for messy LAN reality
Offline chargers are handled quietly, polling backs off, commands surface a Home Assistant error toast on failure, and diagnostics downloads redact credentials and identifying fields.

## Requirements

| Requirement | Details |
| --- | --- |
| Home Assistant | 2025.1 or newer |
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
6. Pick the integration mode:
   - **Basic**: charging control only, no SOC sensors.
   - **Advanced**: also creates SOC input numbers and SOC/ETA sensors.

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
| `binary_sensor.eveus_ev_charger_ocpp_connected` | Binary sensor | Reported OCPP connection state (diagnostic) |
| `number.eveus_ev_charger_charging_current` | Number | Current limit slider with model-aware bounds |
| `switch.eveus_ev_charger_stop_charging` | Switch | Stop/allow charging from the charger side |
| `switch.eveus_ev_charger_one_charge` | Switch | Enable one-charge mode |
| `switch.eveus_ev_charger_connect_to_ocpp` | Switch | Connect the charger to the OCPP backend (e.g. for the mobile app). While on, a Repairs warning explains that Charging Current, limits, and schedule may be overridden by the backend, and how to turn OCPP back off |
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
| `sensor.eveus_ev_charger_session_time` | Sensor | Elapsed duration of the current charging session |
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
| `number.eveus_ev_charger_initial_soc` | % | 0-100, step 1 | 20 | Battery SOC at the start of the current charging session |
| `number.eveus_ev_charger_target_soc` | % | 0-100, step 5 | 80 | Target SOC for ETA calculations |
| `number.eveus_ev_charger_battery_capacity` | kWh | 10-160, step 1 | 50 | EV battery capacity |
| `number.eveus_ev_charger_soc_correction` | % | 0-20, step 0.5 | 7.5 | Charging loss correction |
| `sensor.eveus_ev_charger_soc_energy` | kWh | - | - | Estimated energy currently in the EV battery |
| `sensor.eveus_ev_charger_soc_percent` | % | - | - | Estimated battery percentage |
| `sensor.eveus_ev_charger_time_to_target_soc` | Sensor | - | - | Human-readable ETA to target SOC |
| `sensor.eveus_ev_charger_charging_finish_time` | Timestamp | - | - | Absolute finish time for automations and timestamp cards |

Migration from old helpers is intentionally simple: replace the prefix `input_number.ev_` with `number.eveus_ev_charger_` in cards and automations. For example, `input_number.ev_initial_soc` becomes `number.eveus_ev_charger_initial_soc`.

SOC uses the charger's native `sessionEnergy` value. The charger resets this value on every new plug-in, so continuous charging sessions survive Home Assistant restarts without a synthetic baseline. If you unplug and later resume charging, update `number.eveus_ev_charger_initial_soc` to the current battery percentage before the next session starts.

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

A complete, ready-to-paste Lovelace **Sections** view that exposes **every Eveus entity** ships at [`docs/dashboard.yaml`](docs/dashboard.yaml) (**v1.1**). 
**Requirements:** the [`mini-graph-card`](https://github.com/kalkih/mini-graph-card) HACS frontend plugin (for the two graph cards). Every other card is built-in.
<img width="1059" height="761" alt="image" src="https://github.com/user-attachments/assets/cc349dc9-612f-4919-897d-53d8bf53772a" />
<img width="1085" height="449" alt="image" src="https://github.com/user-attachments/assets/90ce8edf-f5c1-4229-9b6c-0504165cacb2" />
**Language:** the view ships in two interchangeable files — [`docs/dashboard.yaml`](docs/dashboard.yaml) (English) and [`docs/dashboard-uk.yaml`](docs/dashboard-uk.yaml) (Ukrainian, identical layout). Home Assistant does not translate dashboard labels automatically, so each file carries its own labels; the entity IDs are identical, so you can switch files anytime without touching history or automations.


**3-phase setups:** add `sensor.eveus_ev_charger_current_phase_2`/`_3` and `…_voltage_phase_2`/`_3` to the **Status** section — those sensors exist only when `Phases = 3`.

> [!IMPORTANT]
> `docs/dashboard.yaml` is a **whole dashboard view**, not a single card. Don't try to add it through **"Add Card → Manual"** — that expects one card and will error on this file. It must go into a dashboard's **raw configuration** under `views:`, as described below.


**Install (step by step):**

1. Go to **Settings → Dashboards**. Either open an existing dashboard or click **+ Add Dashboard → New dashboard from scratch** to create a fresh one (recommended, so it lives on its own).
2. Open the dashboard, then click the **pencil / ✏️ Edit** button (top right).
3. Click the **⋮ (three dots) → Raw configuration editor**.
4. You'll see YAML that starts with `views:`. Copy the **entire contents** of [`docs/dashboard.yaml`](docs/dashboard.yaml) (or [`docs/dashboard-uk.yaml`](docs/dashboard-uk.yaml) for the Ukrainian version) and paste it as a new list item under `views:`, like this:

   ```yaml
   views:
     - title: Eveus       # ← the whole docs/dashboard.yaml goes here, indented under views:
       path: eveus
       type: sections
       sections:
         - ...
   ```

   If the dashboard is brand new and empty, you can replace everything in the editor with:

   ```yaml
   views:
     - <paste docs/dashboard.yaml here, indented two spaces under the "- ">
   ```

5. Click **Save**, then close the editor. The **Eveus** view appears as a new tab.

**If your device slug differs from `eveus_ev_charger`** (e.g. you renamed the charger or have several), find-and-replace `eveus_ev_charger` with your slug, or fix each entity with Home Assistant's entity picker after pasting.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Setup cannot connect | Charger is powered on, HA can reach the charger IP/hostname, credentials are correct, selected model matches the charger |
| Controls do not respond | Connection Quality, charger online state, credentials via Reconfigure, then wait one coordinator refresh |
| SOC sensors are missing | Set the integration mode to Advanced under Configure, then restart/reload the integration if just changed |
| SOC looks wrong after unplug/replug | Update `number.eveus_ev_charger_initial_soc` to the real battery percentage before starting the next session |
| Charger is powered off | This is normal. Polling backs off and the integration avoids log spam |

### Repair notices

The integration surfaces issues through Home Assistant **Settings → Devices & Services → Repairs**. Most notices clear themselves automatically once the underlying condition is resolved. Grounding notices clear automatically after confirmed recovery. Serious safety incidents remain visible after recovery until you press **Ignore**; after an ignored incident recovers, it is reset so a future separate incident can alert again.

| Notice | When it appears | What to do |
| --- | --- | --- |
| Charger setup needs attention | Stored connection details are incomplete or invalid | Open the repair and re-enter the charger details (fixable in place) |
| Update SOC cards and automations | Legacy `input_number.ev_*` helpers are still present | Switch dashboards/automations to the native `number.eveus_ev_charger_*` entities |
| OCPP is enabled | OCPP is turned on, so the OCPP server or mobile app may override HA controls | Turn off the **Connect to OCPP** switch to restore full HA control |
| Charger battery is low | The charger's internal CR2032 coin-cell reads low for several polls | Replace it with a fresh, good-quality CR2032 cell |
| Ground is missing | The charger reports a grounding fault or repeatedly reports no ground | Stop charging and have the grounding checked; clears automatically after stable grounding returns |
| Protective ground control is disabled | The charger repeatedly reports that ground protection is disabled | Enable it in charger settings if supported; ignore only if intentionally disabled under local requirements |
| Leakage detected | The charger reports a leakage fault or sustained live leakage at the GFCI threshold | Stop using the charger and inspect the vehicle, cable, and installation |
| Box or plug overheating | The charger reports overheat or sustained temperature at/above 85 °C | Stop using the charger, let it cool, and inspect the affected equipment |
| Charger safety fault | Relay, pilot, diode, overcurrent, voltage, GFCI-test, interface, or software fault | Stop or avoid charging and follow the notice guidance |

## Privacy And Diagnostics

The integration stores only the charger connection details needed by Home Assistant. Diagnostics downloads redact credentials and identifying fields before export. Charger communication stays on your LAN.

## License

MIT.
