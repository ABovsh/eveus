# Eveus EV Charger for Home Assistant

**English** | [🇺🇦 Українська](README.uk.md)

> Full local control and monitoring for Eveus EV chargers: charging controls, current electrical measurements, charging costs, EV battery SOC estimates, schedules, safety notices, and automation-ready entities — no template sensors needed.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-4.18.1-blue?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-41BDF5?style=for-the-badge&logo=home-assistant)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ABovsh_eveus)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=reliability_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=reliability_rating)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=security_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=security_rating)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=sqale_rating)
[![Coverage](https://img.shields.io/sonar/coverage/ABovsh_eveus?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonarcloud&label=coverage)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=coverage)

- Documentation: <https://abovsh.github.io/eveus/>
- Discussion: [Home Assistant Community thread](https://community.home-assistant.io/t/eveus-ev-charger-home-assistant-integration-local-only-hacs/1010628)
- Issues: [github.com/ABovsh/eveus/issues](https://github.com/ABovsh/eveus/issues)

<img width="1188" height="477" alt="image" src="https://github.com/user-attachments/assets/064dd525-ecb9-4f7f-ac0c-2dc9a16b7039" />
<img width="1189" height="386" alt="image" src="https://github.com/user-attachments/assets/48412a75-3368-4215-aa83-43b835b0180f" />
<img width="1178" height="620" alt="image" src="https://github.com/user-attachments/assets/b96a52db-7d3d-4a43-be09-09324b84f681" />


The integration talks to the charger directly over your LAN via its HTTP API — it works even when the internet is down. Everything the charger knows becomes a native Home Assistant entity.

**Jump to:** [Highlights](#-highlights) · [Installation](#installation) · [Setup](#setup) · [Safety notices](#-safety-notices) · [Entity IDs](#entity-ids) · [Events & Device Triggers](#events--device-triggers) · [Dashboard](#dashboard) · [Troubleshooting](#troubleshooting)

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

### 🛑 Charge limits
Stop a session automatically — every limit the charger supports, set straight from Home Assistant, each with its own enable switch plus a master **Limit: disable all**:

- **Time, Energy and Cost limits** — stop by session duration, delivered kWh, or session cost
- **Stop at Target SOC** (Advanced mode) — **Limit: SOC enabled** halts charging when the car reaches your target battery level
- **Per-schedule caps** — each schedule slot gets its own current and energy limit
- **One master switch** suspends every limit at once without losing the values

### 🤖 Adaptive charging & schedules
The charger can protect weak house wiring by lowering the charging current when mains voltage sags. The integration exposes this fully:

- **Adaptive Mode selector** — pick Off / Voltage / Auto / Power to match the charger's own modes
- **Adaptive Charging sensor** — see which adaptive mode is active
- **Adaptive Current Limit sensor** — the cap the charger chose
- **Undervoltage threshold** — set the Voltage-mode trigger voltage (210–220 V) from HA
- **Two on-device schedule slots** — enable switches, native HH:MM time pickers, and summary sensors; charging windows live on the charger, so they survive HA restarts

### 🧩 Automation-ready entities
The signals automations actually need, as first-class entities:

- `Car Connected` and `Session Active` binary sensors for triggers
- `Charging Finish Time` as a real `timestamp` — works with countdown cards and time-based automations
- `Session Cost`, schedule controls, `Connection Quality` — no template sensors to maintain
- **Device triggers** for charging started/finished, error, and car connected/disconnected — pick them straight from the automation UI, no YAML needed
- **Bus events** (`eveus_charging_started`, `eveus_charging_finished`, `eveus_error`, `eveus_car_connected`, `eveus_car_disconnected`) for automations that need the full event payload — see [Events & Device Triggers](#events--device-triggers)

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
| Charger | Eveus 16A, 32A, 40A, or 48A charger reachable from Home Assistant |
| Charger firmware | Verified on R3.05.x; older firmware (R3.01.x, and firmware 1.x with some fields unavailable) is supported — updating to the latest firmware is still recommended, see [Older charger firmware](#older-charger-firmware) |
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
4. Pick the charger model: **16A**, **32A**, **40A**, or **48A**.
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
| Charger error with unknown cause | The charger is in the Error state but the fault code is missing or not recognized, for several consecutive polls | Check the fault on the charger's own display or app, power-cycle the charger, and update its firmware if the error keeps recurring; clears once the fault ends and you press **Ignore** |
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

<details>
<summary>Show entities</summary>

| Entity ID | Type | What it gives you |
| --- | --- | --- |
| `sensor.eveus_ev_charger_state` | Sensor | Main charger state, such as standby, charging, complete, or error. `enum` device class — automation state triggers offer a dropdown of all possible values |
| `sensor.eveus_ev_charger_substate` | Sensor | Detailed charger substate or error label. `enum` device class — same dropdown behavior |
| `binary_sensor.eveus_ev_charger_car_connected` | Binary sensor | Vehicle is electrically connected |
| `binary_sensor.eveus_ev_charger_session_active` | Binary sensor | Charging session is active or paused |
| `binary_sensor.eveus_ev_charger_ocpp_connected` | Binary sensor | Reported OCPP connection state (diagnostic) |
| `number.eveus_ev_charger_charging_current` | Number | Current limit slider with model-aware bounds |
| `number.eveus_ev_charger_limit_time` | Number | **Limit: Time** session limit (min) |
| `number.eveus_ev_charger_limit_energy` | Number | **Limit: Energy** session limit (kWh) |
| `number.eveus_ev_charger_limit_cost` | Number | **Limit: Cost** session limit (UAH) |
| `select.eveus_ev_charger_minimum_voltage` | Select | **Minimum voltage** undervoltage threshold (150–200 V) |
| `switch.eveus_ev_charger_stop_charging` | Switch | Stop/allow charging from the charger side |
| `switch.eveus_ev_charger_one_charge` | Switch | Enable one-charge mode |
| `switch.eveus_ev_charger_ground_protection` | Switch | Enable or disable the charger's missing-ground shutdown protection. Turning it off lets charging continue without a detected ground |
| `switch.eveus_ev_charger_connect_to_ocpp` | Switch | Connect the charger to the OCPP backend (used by the Grizzl-E Connect mobile app). While on, a Repairs warning explains that Charging Current, limits, and schedule may be overridden by the backend, and how to turn OCPP back off |
| `switch.eveus_ev_charger_limit_time_enabled` | Switch | **Limit: Time enabled** |
| `switch.eveus_ev_charger_limit_energy_enabled` | Switch | **Limit: Energy enabled** |
| `switch.eveus_ev_charger_limit_cost_enabled` | Switch | **Limit: Cost enabled** |
| `switch.eveus_ev_charger_limit_disable_all` | Switch | **Limit: disable all** |
| `switch.eveus_ev_charger_limit_soc_enabled` | Switch | **Limit: SOC enabled** (Advanced mode only) |
| `button.eveus_ev_charger_force_refresh` | Button | Poll the charger immediately |

The charger natively enforces the Time, Energy, and Cost session limits: set a value, then turn on its enabled switch. **Limit: disable all** is the master switch that suspends all of them. **Limit: SOC enabled** is enforced by the integration in Advanced mode: when the car reaches **Target SOC**, the integration issues the normal **Stop Charging** command and fires the `eveus_soc_limit_reached` event with `device_number`, `soc`, and `target_soc` in its payload.

You can route that event to your own notification automation:

```yaml
automation:
  - alias: Eveus SOC limit reached
    triggers:
      - trigger: event
        event_type: eveus_soc_limit_reached
    actions:
      - action: notify.notify
        data:
          message: "Eveus reached target SOC: {{ trigger.event.data.soc }}%"
```

</details>

### Live Electrical Data

<details>
<summary>Show entities</summary>

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

</details>

### Energy, Cost, And Tariffs

<details>
<summary>Show entities</summary>

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
| `sensor.eveus_ev_charger_last_session_energy` | kWh | Energy delivered by the most recently finished session; keeps its value across restarts and while the charger is offline |
| `sensor.eveus_ev_charger_last_session_cost` | UAH | Cost of the most recently finished session |
| `sensor.eveus_ev_charger_last_session_duration` | Sensor | Duration of the most recently finished session |

Each Last Session sensor is populated when a session finishes and carries `reason` and `finished_at` attributes.

</details>

### SOC And ETA, Advanced Mode

Advanced mode creates four native input numbers. Older `input_number.ev_*` helpers are no longer read.

<details>
<summary>Show entities</summary>

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

</details>

### Adaptive Charging And Schedules

<details>
<summary>Show entities</summary>

| Entity ID | Type | What it gives you |
| --- | --- | --- |
| `select.eveus_ev_charger_adaptive_mode` | Select | Adaptive mode: Off / Voltage / Auto / Power |
| `sensor.eveus_ev_charger_adaptive_charging` | Sensor | Active adaptive mode (Off / Voltage / Auto / Power) |
| `sensor.eveus_ev_charger_adaptive_current_limit` | A | Current cap selected by adaptive mode |
| `number.eveus_ev_charger_undervoltage_threshold` | V | **Undervoltage threshold** — Voltage-mode trigger (210–220 V) |
| `switch.eveus_ev_charger_schedule_1_enabled` | Switch | Enable or disable schedule slot 1 |
| `time.eveus_ev_charger_schedule_1_start` | Time | Schedule 1 start time |
| `time.eveus_ev_charger_schedule_1_stop` | Time | Schedule 1 stop time |
| `number.eveus_ev_charger_schedule_1_current_limit` | A | **Schedule 1 Current limit** |
| `switch.eveus_ev_charger_schedule_1_current_limit_enabled` | Switch | **Schedule 1 Current limit enabled** |
| `number.eveus_ev_charger_schedule_1_energy_limit` | kWh | **Schedule 1 Energy limit** |
| `switch.eveus_ev_charger_schedule_1_energy_limit_enabled` | Switch | **Schedule 1 Energy limit enabled** |
| `sensor.eveus_ev_charger_schedule_1` | Sensor | Schedule 1 summary and attributes |
| `switch.eveus_ev_charger_schedule_2_enabled` | Switch | Enable or disable schedule slot 2 |
| `time.eveus_ev_charger_schedule_2_start` | Time | Schedule 2 start time |
| `time.eveus_ev_charger_schedule_2_stop` | Time | Schedule 2 stop time |
| `number.eveus_ev_charger_schedule_2_current_limit` | A | **Schedule 2 Current limit** |
| `switch.eveus_ev_charger_schedule_2_current_limit_enabled` | Switch | **Schedule 2 Current limit enabled** |
| `number.eveus_ev_charger_schedule_2_energy_limit` | kWh | **Schedule 2 Energy limit** |
| `switch.eveus_ev_charger_schedule_2_energy_limit_enabled` | Switch | **Schedule 2 Energy limit enabled** |
| `sensor.eveus_ev_charger_schedule_2` | Sensor | Schedule 2 summary and attributes |

Each schedule has its own current and energy caps with separate enable switches.

</details>

### Diagnostics And Maintenance

<details>
<summary>Show entities</summary>

| Entity ID | Unit | What it gives you |
| --- | --- | --- |
| `sensor.eveus_ev_charger_connection_quality` | % | Recent polling success, latency, and health attributes |
| `sensor.eveus_ev_charger_ground` | Sensor | Ground status |
| `sensor.eveus_ev_charger_time_drift` | s | Charger local clock vs Home Assistant local time (0 = in sync; a steady ±3600 means a wrong Time Zone or DST mismatch) |
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

</details>

### Events & Device Triggers

The integration fires events on the Home Assistant event bus for charger state transitions. Every payload includes `device_number`:

| Event | Fires when | Extra payload fields |
| --- | --- | --- |
| `eveus_charging_started` | A charging session begins | — |
| `eveus_charging_finished` | A charging session ends | `reason` (`complete`, `unplugged`, `stopped`, or `paused`), `session_energy_kwh`, `session_cost`, `session_duration_s` |
| `eveus_error` | The charger enters the error state | `error_code`, `error_text` |
| `eveus_car_connected` | The car is electrically connected | — |
| `eveus_car_disconnected` | The car is disconnected | — |

`eveus_charging_finished`'s energy/cost/duration fields are a snapshot taken from the last poll while the session was still alive, so the values survive the charger resetting its own counters at session end — they can lag the true final value by up to one poll interval. Transitions that happen while the charger is unreachable, or while Home Assistant is down, are deliberately silent — you won't see a false event after reconnecting or restarting.

Each event also has a matching **device trigger**: in the automation UI, choosing the Eveus device offers "Charging started", "Charging finished", "Error occurred", "Car connected", and "Car disconnected" as ready-made triggers — no YAML needed.

For automations that need the event payload, trigger on the event directly:

```yaml
automation:
  - alias: Eveus session summary
    triggers:
      - trigger: event
        event_type: eveus_charging_finished
    actions:
      - action: notify.notify
        data:
          message: >-
            Session finished ({{ trigger.event.data.reason }}):
            {{ trigger.event.data.session_energy_kwh }} kWh,
            {{ trigger.event.data.session_cost }} UAH
```

## Dashboard

A complete, ready-to-paste Lovelace **Sections** view that exposes **every Eveus entity** ships at [`docs/dashboard.yaml`](docs/dashboard.yaml) (**v1.2**).
**Requirements:** the [`mini-graph-card`](https://github.com/kalkih/mini-graph-card) HACS frontend plugin (for the two graph cards). Every other card is built-in.
<img width="1188" height="477" alt="image" src="https://github.com/user-attachments/assets/064dd525-ecb9-4f7f-ac0c-2dc9a16b7039" />
<img width="1189" height="386" alt="image" src="https://github.com/user-attachments/assets/48412a75-3368-4215-aa83-43b835b0180f" />
<img width="1178" height="620" alt="image" src="https://github.com/user-attachments/assets/b96a52db-7d3d-4a43-be09-09324b84f681" />
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
| Setup cannot connect | The setup dialog shows the reason in parentheses — e.g. `Failed to connect to charger (HTTP 404)` or `(Connection error: TimeoutError)`. Check the charger is powered on, HA can reach the charger IP/hostname, credentials are correct, and the selected model matches the charger |
| Controls do not respond | Connection Quality, charger online state, credentials via Reconfigure, then wait one coordinator refresh |
| SOC sensors are missing | Set the integration mode to Advanced under Configure, then restart/reload the integration if just changed |
| SOC looks wrong after unplug/replug | Update `number.eveus_ev_charger_initial_soc` to the real battery percentage before starting the next session |
| Charger is powered off | This is normal. Polling backs off and the integration avoids log spam |
| A Repairs notice appeared | See [Safety notices](#-safety-notices) for what each one means and what to do |

### Older charger firmware

Older firmware (R3.01.x has been reported) now sets up and works normally — setup accepts any responding charger, and chargers with an unset serial number that return garbage bytes are handled tolerantly. Updating is still recommended: message **@energy_star** on Telegram for the firmware files, then flash the update from the charger's web interface.

Firmware 1.x (EnergyStar V-series) also sets up and works, with some fields degraded: the firmware version is read from the charger's boot info instead of the usual field, and this firmware's own state codes are translated to the standard names (idle shows as Standby; Charging is detected while power is actually flowing). A code the integration doesn't recognize shows as `Unknown`, with the numeric code kept in the State sensor's `raw_state` attribute. Fields the firmware doesn't report at all (such as serial number, Substate, or OCPP status) stay unavailable rather than showing stale or wrong data.

If a charger still fails to set up, note the error shown in the setup dialog, find the integration's warning in the Home Assistant log (Settings → System → Logs — it contains the HTTP status, content type, and the first bytes of the charger's reply; no debug logging needed), and open a [GitHub issue](https://github.com/ABovsh/eveus/issues) with your firmware version, the dialog error text, and that warning line. Hide anything sensitive first (your IP addresses, serial numbers).

## Privacy And Diagnostics

The integration stores only the charger connection details needed by Home Assistant. Diagnostics downloads redact credentials and identifying fields before export. Charger communication stays on your LAN.

---

If this integration is useful to you, please ⭐ the repo — it helps others find it.

## License

MIT.
