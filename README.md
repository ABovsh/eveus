# Eveus EV Charger for Home Assistant

**English** | [🇺🇦 Українська](README.uk.md)

> Full local control and monitoring for Eveus EV chargers: charging controls, current electrical measurements, charging costs, EV battery SOC estimates, schedules, safety notices, and automation-ready entities — no template sensors needed.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-4.13.0-blue?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-41BDF5?style=for-the-badge&logo=home-assistant)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ABovsh_eveus)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=reliability_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=reliability_rating)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=security_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=security_rating)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=sqale_rating)
[![Coverage](https://img.shields.io/sonar/coverage/ABovsh_eveus?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonarcloud&label=coverage)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=coverage)

- Discussion: [Home Assistant Community thread](https://community.home-assistant.io/t/eveus-ev-charger-home-assistant-integration-local-only-hacs/1010628)
- Issues: [github.com/ABovsh/eveus/issues](https://github.com/ABovsh/eveus/issues)

<img width="1063" height="763" alt="image" src="https://github.com/user-attachments/assets/4c9ece28-8977-47d0-8fbc-78a69b95dac9" />

The integration talks to the charger directly over your LAN via its HTTP API — it works even when the internet is down. Everything the charger knows becomes a native Home Assistant entity.

**Jump to:** [Highlights](#-highlights) · [Installation](#installation) · [Setup](#setup) · [Safety notices](#-safety-notices) · [Entity IDs](#entity-ids) · [Dashboard](#dashboard) · [Troubleshooting](#troubleshooting)

## ✨ Highlights

### ⚡ Live electrical telemetry
Everything the charger measures, updated on every poll:

- **Voltage, current, power** and the active current-limit setpoint
- **Per-phase voltage and current** on 3-phase setups
- **Box and plug temperatures**, ground status, backup-battery voltage

### 💰 Accurate energy & cost
Session Energy, Total Energy, and two resettable counters (A/B), each with a running cost. Costs come from the charger's own meter, so **Session Cost stays correct even when the tariff switches mid-session** (e.g. night→day at 07:00). All tariff rates are exposed as sensors.

### 🔋 EV battery SOC estimates
Pick **Advanced** mode and get battery SOC as native sensors — no helpers to create by hand:

- **SOC %** and **SOC energy (kWh)** — estimated battery level while charging
- **Time to Target SOC** and **Charging Finish Time** — when charging will be done
- **Energy / Cost to Target SOC** — what's left to deliver and what it will cost
- Inputs (initial SOC, target SOC, battery capacity, efficiency loss) are plain `number` entities you can set from any dashboard or automation

Pick **Basic** if you only want charging control. Switch modes anytime via **Configure**.

### 🤖 Adaptive charging & schedules
The charger can protect weak house wiring by lowering the charging current when mains voltage sags. The integration exposes this fully:

- **Adaptive Mode switch** — turn the feature on or off from HA
- **Adaptive Charging sensor** — see when throttling is actually active
- **Adaptive Current Limit / Voltage Threshold** — the cap and the trigger voltage the charger chose
- **Two on-device schedule slots** — enable switches, native HH:MM time pickers, and summary sensors; charging windows live on the charger, so they survive HA restarts

### 🧩 Automation-ready entities
The signals automations actually need, as first-class entities:

- `Car Connected` and `Session Active` binary sensors for triggers
- `Charging Finish Time` as a real `timestamp` — works with countdown cards and time-based automations
- `Session Cost`, schedule controls, `Connection Quality` — no template sensors to maintain

### ☁️ OCPP backend control
A single switch connects the charger to its OCPP backend (used by the **Grizzl-E Connect** mobile app), and a binary sensor shows the live connection state. While OCPP is on, a Repairs notice reminds you that the backend may override Charging Current, limits, and schedules — and how to switch back to full local control.

### 🌐 Localized UI
English and Ukrainian translations ship in the box; Home Assistant picks the user's language automatically.

### 🛰️ Multi-charger
Add as many Eveus chargers as you have; each gets its own device and entities.

### 🩺 Robust on real networks
- **Adaptive polling** — fast while charging, relaxed when idle, and a quick follow-up burst whenever the charger changes state on its own (a schedule kicks in, a session starts from the charger UI or OCPP)
- **Quiet offline handling** — a powered-off charger doesn't spam your logs, and it reappears in HA within a minute of being switched back on
- **Honest controls** — every command is confirmed against the charger; failures raise a visible HA error instead of silently pretending
- **Guided recovery** — a changed password opens a re-authentication flow (and isn't mislabeled as "charger offline"); broken connection settings surface as a fixable Repairs issue
- **Safe diagnostics** — downloads redact credentials and identifying fields, so they're safe to attach to a GitHub issue

### 🛡️ Safety watchdog
Your charger already protects itself — this integration makes those protections **visible and actionable in Home Assistant**. Each condition raises a clear **Repairs** notice (English and Ukrainian):

- **Missing ground**, or ground protection turned off
- **Overheating** — early warning at **80 °C**, before the charger shuts down at 85 °C
- **Current leakage** above 30 mA
- **Charger protection faults** (relay, pilot, overcurrent, voltage, GFCI self-test, …)
- **Low backup battery** (CR2032)

A dedicated **Ground Protection** switch manages the charger's missing-ground shutdown from HA. Confirmation counting and recovery hysteresis make sure one glitchy reading never raises a false alarm.

See [Safety notices](#-safety-notices) for the full list of conditions and recommended actions.

## Requirements

| Requirement | Details |
| --- | --- |
| Home Assistant | 2025.1 or newer |
| Charger | Eveus 16A, 32A, or 48A charger reachable from Home Assistant |
| Network | Local LAN access to the charger HTTP API |
| Setup details | Charger IP/hostname or URL, username, password, model |

## Installation

### HACS

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ABovsh&repository=eveus&category=integration)

Or manually:

1. HACS -> **Custom repositories**.
2. Add `https://github.com/ABovsh/eveus` as **Integration**.
3. Install **Eveus EV Charger**.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/eveus` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=eveus)

1. Go to **Settings → Devices & Services → Add Integration** and search for **Eveus EV Charger**.
2. Enter the charger address — IP, hostname, or full URL (custom port, `http://`, and `https://` all work).
3. Enter the username and password.
4. Pick the charger model: **16A**, **32A**, or **48A**.
5. Pick the integration mode:
   - **Basic** — charging control and monitoring only.
   - **Advanced** — adds the SOC inputs and SOC/ETA sensors.
6. **Advanced only:** a second screen asks for your EV's **battery capacity (kWh)** and **charging efficiency loss (%)**. Both can be changed later from the entities themselves.

Changing things later:

- **Configure** — switch between Basic and Advanced. Switching to Advanced for the first time shows the same battery-capacity screen as setup.
- **Reconfigure** — change host, credentials, model, or phase count without reinstalling.

## 🛡️ Safety notices

Dangerous and configuration conditions surface through Home Assistant **Settings → Devices & Services → Repairs**, each with a plain-language message (English and Ukrainian). Recoverable safety notices clear themselves automatically once the condition is resolved; serious incidents stay visible until you press **Ignore**, then reset after recovery so a future separate incident can alert again.

### Safety conditions

| Notice | When it appears | What to do |
| --- | --- | --- |
| Ground is missing | The charger reports a grounding fault or repeatedly reports no ground, independently of the Ground Protection switch | Stop charging and have the grounding checked by a qualified electrician; clears automatically after stable grounding returns |
| Ground protection is disabled | The charger repeatedly reports that ground protection is turned off, independently of whether ground is currently connected | Turn on the **Ground Protection** switch — the charger is allowed to charge without a confirmed ground while it is off |
| Box or plug overheating | The charger reports an overheat fault, or a sustained temperature at/above **80 °C** (early warning before the charger stops at **85 °C**) | Stop using the charger, let it cool, and contact the charger manufacturer's support if it repeats |
| Current leakage (GFCI) | The charger reports a leakage fault or sustained leakage above its **30 mA** threshold | Stop using the charger and contact the charger manufacturer's support |
| Charger protection fault | Relay, pilot, diode, overcurrent, low/high voltage, GFCI self-test, interface-timeout, or software fault reported by the charger | Stop or avoid charging and contact the charger manufacturer's support |
| Charger backup battery is low | The charger's internal CR2032 coin-cell reads low for several polls | Replace it with a fresh, good-quality CR2032 cell |

### Configuration notices

| Notice | When it appears | What to do |
| --- | --- | --- |
| Charger setup needs attention | Stored connection details are incomplete or invalid | Open the repair and re-enter the charger details (fixable in place) |
| OCPP is enabled | OCPP is on, so the OCPP server or mobile app may override HA controls | Turn off the **Connect to OCPP** switch to restore full HA control |
| Charger clock is off | The charger clock differs from Home Assistant by more than 10 minutes for several polls, so schedules and tariff windows may mistime | Check the **Time Zone** select, then press the **Sync Time** button; the notice clears once the clocks agree |
| Update SOC cards and automations | Legacy `input_number.ev_*` helpers are still present | Switch dashboards/automations to the native `number.eveus_ev_charger_*` entities |

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
| `switch.eveus_ev_charger_ground_protection` | Switch | Enable or disable the charger's missing-ground shutdown protection. Turning it off lets charging continue without a detected ground |
| `switch.eveus_ev_charger_connect_to_ocpp` | Switch | Connect the charger to the OCPP backend (used by the Grizzl-E Connect mobile app). While on, a Repairs warning explains that Charging Current, limits, and schedule may be overridden by the backend, and how to turn OCPP back off |
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
| `sensor.eveus_ev_charger_energy_to_target_soc` | kWh | - | - | Grid energy still needed to reach Target SOC (charging losses included) |
| `sensor.eveus_ev_charger_cost_to_target_soc` | UAH | - | - | Forecast cost of reaching Target SOC at the active tariff rate |

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
| A Repairs notice appeared | See [Safety notices](#-safety-notices) for what each one means and what to do |

## Privacy And Diagnostics

The integration stores only the charger connection details needed by Home Assistant. Diagnostics downloads redact credentials and identifying fields before export. Charger communication stays on your LAN.

---

If this integration is useful to you, please ⭐ the repo — it helps others find it.

## License

MIT.
