# Eveus EV Charger for Home Assistant

**English** | [🇺🇦 Українська](README.uk.md)

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/default)
![Version](https://img.shields.io/badge/version-4.22.0-blue?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-41BDF5?style=for-the-badge&logo=home-assistant)
[![Downloads](https://img.shields.io/github/downloads/ABovsh/eveus/total?style=for-the-badge&color=41BDF5&label=downloads)](https://github.com/ABovsh/eveus/releases)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ABovsh_eveus)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=reliability_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=reliability_rating)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=security_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=security_rating)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ABovsh_eveus&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=sqale_rating)
[![Coverage](https://img.shields.io/sonar/coverage/ABovsh_eveus?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonarcloud&label=coverage)](https://sonarcloud.io/component_measures?id=ABovsh_eveus&metric=coverage)

- Documentation: <https://abovsh.github.io/eveus/>
- Discussion: [Home Assistant Community thread](https://community.home-assistant.io/t/eveus-ev-charger-home-assistant-integration-local-only-hacs/1010628)
- Issues: [github.com/ABovsh/eveus/issues](https://github.com/ABovsh/eveus/issues)

<p align="center">
  <img alt="Eveus card — Advanced mode" src="docs/images/card-advanced.jpg" width="49%">
  <img alt="Eveus card — Basic mode" src="docs/images/card-basic.jpg" width="49%">
</p>

An integration for Eveus EV chargers: charging control, charger readings, energy and cost tracking, EV battery SOC estimates, schedules, safety notices and entities for automations. It talks to the charger's HTTP API directly on your LAN, so it works without internet access. Supported readings and settings are available as Home Assistant entities.

**Jump to:** [Highlights](#-highlights) · [Installation](#installation) · [Setup](#setup) · [Safety notices](#-safety-notices) · [Eveus card](#eveus-card) · [Entity IDs](#entity-ids) · [Events & Device Triggers](#events--device-triggers) · [Dashboard](#dashboard) · [Energy Dashboard](#energy-dashboard) · [Troubleshooting](#troubleshooting)

## ✨ Highlights

### ⚡ Charger readings

The integration displays data received from the charger:

- **Voltage, current, power** and the active current-limit setpoint
- **Per-phase voltage and current** on 3-phase setups
- **Box and plug temperatures**, ground status, backup-battery voltage

### 💰 Energy & cost
Session Energy, Total Energy, and two resettable counters (A/B), each with a running cost. Costs come from the charger's own meter, so **Session Cost stays correct even when the tariff switches mid-session** (e.g. night→day at 07:00). All tariff rates are exposed as sensors.

### 🔋 EV battery SOC
In **Advanced** mode, you get:

- **SOC %** and **SOC energy (kWh)** — estimated battery level while charging
- **Time to Target SOC** and **Charging Finish Time** — estimated time to reach the target battery level
- **Energy / Cost to Target SOC** — grid energy still needed, including charging losses, and its estimated cost at the current tariff
- Inputs (initial SOC, target SOC, battery capacity, efficiency loss) are plain `number` entities you can set from any dashboard or automation
- Optionally point the integration at your car's own SOC sensor and Initial SOC fills itself in once per plug-in

Pick **Basic** if you only want charging control. Switch modes anytime via **Configure**.

### 🛑 Charge limits
Set charging limits in Home Assistant and enable the ones you need:

- **Time, Energy and Cost limits** — stop by session duration, delivered kWh, or session cost
- **Stop at Target SOC** (Advanced mode) — the integration stops charging when the estimated SOC reaches **Target SOC**. Home Assistant must be running and able to reach the charger
- **Per-schedule caps** — each schedule slot gets its own current and energy limit
- **One master switch** suspends every limit at once without losing the values

### 🤖 Adaptive charging & schedules
The charger can lower the charging current when mains voltage sags. The integration exposes this fully:

- **Adaptive Mode selector** — pick Off / Voltage / Auto / Power to match the charger's own modes
- **Adaptive Charging sensor** — which adaptive mode is active right now
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

### 🌐 English and Ukrainian
The integration ships English and Ukrainian translations. Home Assistant shows entity names and messages in the current user's language.

### 🛰️ Several chargers
Add as many Eveus chargers as you have; each gets its own device and entities.

### 🩺 Polling and reconnection
- **Variable polling rate** — more often while charging, less often when idle, and a few extra polls in a row whenever the charger changes state on its own (a schedule starts, a session is started from the charger UI or through OCPP)
- **Powered-off charger** — does not fill the log with errors, and reappears in Home Assistant within a minute of being switched back on
- **Command confirmation** — the integration checks whether the charger accepted a command and verifies setting changes against its reported state. Errors appear in Home Assistant
- **Access recovery** — a changed password makes Home Assistant ask for new credentials instead of showing the charger as unavailable; broken connection settings appear in **Repairs** and are fixed there
- **Diagnostics** — downloads contain no credentials or identifying fields, so you can attach them to a GitHub issue

### 🛡️ Safety watchdog
The charger has its own protections. The integration shows when they trip in Home Assistant, each as a separate **Repairs** notice (English and Ukrainian):

- **Missing ground**, or ground protection turned off
- **Overheating** — early warning at **80 °C**, before the charger shuts down at 85 °C
- **Current leakage** above 30 mA
- **Charger protection faults** (relay, pilot, overcurrent, voltage, GFCI self-test, …)
- **Low backup battery** (CR2032)

The **Ground Protection** switch sets whether the charger shuts down when there is no ground.

Warnings based on temperature, leakage and ground readings require several consecutive confirmations. Known charger fault codes trigger a notice immediately.

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

The integration is in the **HACS default store** — no custom repository needed.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ABovsh&repository=eveus&category=integration)

Or manually:

1. Open **HACS** and search for **Eveus EV Charger**.
2. Install it.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/eveus` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=eveus)

1. Go to **Settings → Devices & Services → Add Integration** and search for **Eveus EV Charger**.
2. Enter the charger address — IP, hostname, or full URL (custom port, `http://`, and `https://` all work).
3. Enter the username and password. These are the charger's own web-interface credentials, **not** your Home Assistant login.
4. Pick the charger model: **16A**, **32A**, **40A**, or **48A**. Pick the one that matches your hardware — a mismatch stops the charger from reporting.
5. Pick the integration mode:
   - **Basic** — charging control and monitoring only.
   - **Advanced** — adds the SOC inputs and SOC/ETA sensors.
6. **Advanced only:** a second screen asks for your EV's **battery capacity (kWh)** and **charging efficiency loss (%)**. Both can be changed later from the entities themselves.

Changing things later:

- **Configure** — switch between Basic and Advanced, and optionally pick a car SOC sensor (see [Filling Initial SOC from your car](#filling-initial-soc-from-your-car-optional)). Switching to Advanced for the first time shows the same battery-capacity screen as setup.
- **Reconfigure** — change host, credentials, model, or phase count without reinstalling.

## 🛡️ Safety notices

Dangerous and configuration conditions surface in **Settings → System → Repairs**, each with a plain-language message (English and Ukrainian). Recoverable safety notices clear themselves automatically once the condition is resolved; serious incidents stay visible until you press **Ignore**, then reset after recovery so a future separate incident can alert again.

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

## Eveus card

The card is part of the integration; there is nothing to install separately. It is **one card** with a **layout** setting. When you add the card to a dashboard, you pick one layout, which sets the level of detail: from a single line to full charging control. To have both a short and a full card on a dashboard, add the card twice and pick a layout for each.

<p align="center">
  <img alt="Eveus card — Advanced mode" src="docs/images/card-advanced.jpg" width="49%">
  <img alt="Eveus card — Basic mode" src="docs/images/card-basic.jpg" width="49%">
</p>

*Each screenshot shows the same card four times — set to `compact`, `status`, `control` and `full`, from top to bottom. Left: Advanced mode. Right: Basic mode.*

### Layouts

| Layout | Shows | Controls |
|---|---|---|
| `compact` | One line: state, SOC bar, SOC · power · time to target | — |
| `status` | SOC, time to target, current, session energy and cost, power and voltage, state | — (read-only) |
| `control` | SOC, time to target, current, session energy and cost | **One Charge**, **Stop Charging**, **Charging Current** slider |
| `full` | Everything in `control`, plus energy and cost to target and the finish time | Everything in `control`, plus **Initial SOC**, **Target SOC**, **Battery Capacity**, **SOC Correction** and the **Limit: SOC enabled** switch |

- **Advanced and Basic mode.** The card uses the mode chosen for the integration (**Configure**). In Basic mode the card shows power and session time instead of SOC and time to target, and `full` shows voltage, temperatures and state instead of the SOC settings. `mode: basic` switches the card to its Basic view regardless of the integration's mode.
- **Tapping.** Tapping a tile opens that entity's dialog. **One Charge**, **Stop Charging** and **Limit: SOC enabled** toggle with one tap; before stopping a running charge the card asks for confirmation. The slider sends its value when you let go. The − / + buttons send the value after a short pause, so several taps in a row make one command.
- **Faults.** If the charger is in the `Error` state or has no ground, a red line appears in the `status`, `control` and `full` layouts.

### How to add the card

1. Install or update the integration and restart Home Assistant. The card registers itself.
2. Refresh the browser page. In the Home Assistant mobile app: **Settings → Companion app → Troubleshooting → Reset frontend cache**, then restart the app.
3. Open a dashboard and click the pencil (**Edit dashboard**).
4. Click **Add card**, search for **Eveus EV Charger** and pick the card.
5. In the **layout** field, pick a layout. The preview shows the result straight away; a new card starts as `control`.
6. Fill in the other fields only if needed: **device_id** — the charger, when you have several; **mode** — the card's Basic view; **language** — the card's language. The defaults work for one charger.
7. Click **Save**.

### Card in YAML

```yaml
type: custom:eveus-card
layout: control      # compact | status | control | full
# device_id: ...     # only with several chargers
# mode: auto         # auto (default, follows the integration) | basic
# language: uk       # auto (default, Home Assistant's language) | uk | en
```

If **Custom element doesn't exist** appears instead of the card right after an update, repeat step 2.

## Entity IDs

The tables below show default entity IDs for the first charger named **Eveus EV Charger**. Home Assistant may add suffixes if you rename entities or add multiple chargers. Unique IDs are stable, so your history and dashboard cards survive normal updates.

### Charging State And Controls

<details>
<summary>Show entities</summary>

| Entity ID | Type | What it gives you |
| --- | --- | --- |
| `sensor.eveus_ev_charger_state` | Sensor | Main charger state, such as standby, charging, complete, or error. `enum` device class — automation state triggers offer a dropdown of all possible values |
| `sensor.eveus_ev_charger_substate` | Sensor | Detailed charger substate or error label. `enum` device class — same dropdown behavior |
| `sensor.eveus_ev_charger_not_charging_reason` | Sensor | Why charging is not running right now, in one value — cable not connected, waiting for the car, a limit reached, waiting for a schedule, and so on. Reads **Controlled by OCPP** while the OCPP switch is on, since the backend or mobile app decides when a session starts. `enum` device class; in the error state the fault name is in the `error` attribute (modern firmware) |
| `binary_sensor.eveus_ev_charger_car_connected` | Binary sensor | Vehicle is electrically connected |
| `binary_sensor.eveus_ev_charger_session_active` | Binary sensor | Charging session is active or paused |
| `binary_sensor.eveus_ev_charger_ocpp_connected` | Binary sensor | Reported OCPP connection state (diagnostic) |
| `number.eveus_ev_charger_charging_current` | Number | Current limit slider with model-aware bounds |
| `number.eveus_ev_charger_limit_time` | Number | **Limit: Time** session limit (min) |
| `number.eveus_ev_charger_limit_energy` | Number | **Limit: Energy** session limit (kWh) |
| `number.eveus_ev_charger_limit_cost` | Number | **Limit: Cost** session limit (UAH) |
| `select.eveus_ev_charger_minimum_voltage` | Select | **Minimum voltage** — lowest supply voltage at which the charger still charges (150–200 V). Separate from the adaptive **Undervoltage threshold** |
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

### Charger readings

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
| `sensor.eveus_ev_charger_last_session_duration` | s | Duration of the most recently finished session |

Each Last Session sensor is populated when a session finishes and carries `reason` and `finished_at` attributes.

</details>

### SOC And ETA, Advanced Mode

Advanced mode provides four `number` entities for SOC settings.

<details>
<summary>Show entities</summary>

| Entity ID | Unit | Range | Default | What it gives you |
| --- | --- | --- | --- | --- |
| `number.eveus_ev_charger_initial_soc` | % | 0-100, step 1 | 20 | Battery SOC at the start of the current charging session |
| `number.eveus_ev_charger_target_soc` | % | 0-100, step 5 | 80 | Target battery level for forecasts and the SOC limit |
| `number.eveus_ev_charger_battery_capacity` | kWh | 10-160, step 1 | 50 | EV battery capacity |
| `number.eveus_ev_charger_soc_correction` | % | 0-20, step 0.5 | 7.5 | Charging loss correction |
| `sensor.eveus_ev_charger_soc_energy` | kWh | - | - | Estimated energy currently in the EV battery |
| `sensor.eveus_ev_charger_soc_percent` | % | - | - | Estimated battery percentage |
| `sensor.eveus_ev_charger_time_to_target_soc` | Sensor | - | - | Human-readable ETA to target SOC |
| `sensor.eveus_ev_charger_charging_finish_time` | Timestamp | - | - | Absolute finish time for automations and timestamp cards |
| `sensor.eveus_ev_charger_energy_to_target_soc` | kWh | - | - | Grid energy still needed to reach Target SOC (charging losses included) |
| `sensor.eveus_ev_charger_cost_to_target_soc` | UAH | - | - | Forecast cost of reaching Target SOC at the active tariff rate |

Migration from old helpers is intentionally simple: replace the prefix `input_number.ev_` with `number.eveus_ev_charger_` in cards and automations. For example, `input_number.ev_initial_soc` becomes `number.eveus_ev_charger_initial_soc`.

SOC is calculated from the charger's own `sessionEnergy` value. The charger resets it on every new plug-in, so after a Home Assistant restart the calculation for the current session continues using the stored SOC settings. If you unplug and later resume charging, update `number.eveus_ev_charger_initial_soc` to the current battery percentage before the next session starts.

</details>

#### Filling Initial SOC from your car (optional)

If another integration exposes your car's battery level, Eveus can read it instead of you moving the Initial SOC slider before every charge. SOC and forecasts are calculated from Initial SOC, battery capacity, charging loss correction and the charger’s energy meter.

**Setting it up**

Pick the sensor during setup on the **SOC Monitoring Setup** screen, or later under **Settings → Devices & Services → Eveus EV Charger → Configure**. It has to be a `sensor` with device class `battery` and unit `%` — the picker filters by the `battery` device class. If yours is missing, check its domain and device class; its value must be a battery percentage from 0 to 100. Advanced mode only. If no sensor is selected, set **Initial SOC** manually.

**When it reads the car**

Once per plug-in, at the start of charging. If the car is asleep then — normal with a departure timer or a cheap-tariff window — it keeps trying on every poll until a reading arrives, subtracting whatever energy has already gone into the car so a late reading gives the same result as an immediate one. Once a reading lands, the sensor is not consulted again until you unplug.

**SOC during pauses and restarts.** Pause and resume, stop and restart, let the car finish and top up again, ride out a fault the charger recovers from — to the charger that is all one session and its energy meter keeps running through it. Initial SOC is retained and the calculation accounts for accumulated energy. Unplugging and reconnecting requires a new initial value.

**Your own value always wins.** Move the slider by hand and it stays until the next plug-in — including across a Home Assistant restart, or saving the integration's options mid-charge.

**Seeing which value is in use.** SOC Percent carries a `soc_anchor` attribute naming how Initial SOC was last set — the reading and sensor it was seeded from, why a seed could not be taken, or `set manually`. A stale anchor is what silently skews every SOC figure, so this makes it visible.

**If nothing gets filled in**

Check the log. One line per plug-in names the sensor and the reason (`the sensor reads 'unavailable'`, `the charger did not report session energy`, …). **Download diagnostics** shows the same under `soc`, plus what the car sensor reads right now. With no sensor configured, none of this runs and nothing is logged.

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

The energy, cost and duration in `eveus_charging_finished` come from the last poll while the session was still running. They are kept even if the charger resets its own counters at the end of the session, but can lag the final values by one poll interval. State changes that happen while the charger is unreachable or Home Assistant is down create no events, so there are no false events after reconnecting or restarting.

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

A ready-made **Sections** dashboard view with the main Eveus readings and controls is in [`docs/dashboard.yaml`](docs/dashboard.yaml) (**v1.2**). The Ukrainian version, with the same layout, is [`docs/dashboard-uk.yaml`](docs/dashboard-uk.yaml). Home Assistant does not translate dashboard labels, so each file has its own labels; the entity IDs are the same in both, so you can swap files at any time without losing history or automations.

The two graphs need the [`mini-graph-card`](https://github.com/kalkih/mini-graph-card) card from HACS. Every other card is a standard Home Assistant card.

<img width="1188" height="477" alt="image" src="https://github.com/user-attachments/assets/064dd525-ecb9-4f7f-ac0c-2dc9a16b7039" />
<img width="1189" height="386" alt="image" src="https://github.com/user-attachments/assets/48412a75-3368-4215-aa83-43b835b0180f" />
<img width="1178" height="620" alt="image" src="https://github.com/user-attachments/assets/b96a52db-7d3d-4a43-be09-09324b84f681" />

> [!IMPORTANT]
> `docs/dashboard.yaml` is a **whole dashboard view**, not a single card. Don't paste it through **Add card → Manual**: that expects one card and will show an error for this file. It goes into the dashboard's raw configuration, under `views:`, as described below.

### How to add the dashboard

1. Go to **Settings → Dashboards**. Open an existing dashboard, or create a separate one: **Add dashboard → New dashboard from scratch**.
2. Open the dashboard and click the pencil (**Edit dashboard**).
3. Click **⋮ → Raw configuration editor**.
4. The editor shows YAML that starts with `views:`. Copy the **entire contents** of [`docs/dashboard.yaml`](docs/dashboard.yaml) and paste it as a new item in the `views:` list:

   ```yaml
   views:
     - title: Eveus       # ← the whole docs/dashboard.yaml goes here, indented under views:
       path: eveus
       type: sections
       sections:
         - ...
   ```

   If the dashboard is new and empty, you can replace everything in the editor with:

   ```yaml
   views:
     - <docs/dashboard.yaml, indented two spaces after the "- ">
   ```

5. Click **Save** and close the editor. The dashboard gets an **Eveus** tab.

If your entity IDs don't contain `eveus_ev_charger` (you renamed the charger or have several), replace `eveus_ev_charger` in the file with your own ID fragment — or, after pasting, fix each entity with Home Assistant's entity picker.

For a 3-phase charger, add `sensor.eveus_ev_charger_current_phase_2`/`_3` and `…_voltage_phase_2`/`_3` to the **Status** section — those sensors exist only when `Phases = 3`.

## Energy Dashboard

To add EV charging to Home Assistant's Energy dashboard:

1. Go to **Settings → Energy** (before Home Assistant 2026: **Settings → Dashboards → Energy**).
2. Under **Individual devices**, click **Add device**.
3. Pick `sensor.eveus_ev_charger_total_energy` and save.

Charging shows up as its own bar in the energy graphs, with daily and monthly
history. The charger calculates cost and the integration displays it — see
`sensor.eveus_ev_charger_session_cost` and the Counter A and B cost sensors.
They use the tariffs configured on the charger, including the night rate.

## Blueprints

Two ready-made automations ship with the integration. Import either one from
**Settings → Automations & Scenes → Blueprints → Import Blueprint**, paste the
URL, then fill in the two or three fields it asks for — no YAML.

| Blueprint | What it does | URL |
| --- | --- | --- |
| Charging session notification | Notifies on start and finish through the action of your choice; the finish message includes session energy, cost and duration | [`notify_session.yaml`](https://github.com/ABovsh/eveus/blob/main/blueprints/automation/eveus/notify_session.yaml) |
| Stop charging on low house battery | When your inverter's battery stays below a threshold for two minutes, stops charging with the charger's own **Stop Charging** switch, not by cutting its power. Does not automatically resume charging after recovery | [`stop_on_low_house_battery.yaml`](https://github.com/ABovsh/eveus/blob/main/blueprints/automation/eveus/stop_on_low_house_battery.yaml) |

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Setup cannot connect | The setup dialog shows the reason in parentheses — e.g. `Failed to connect to charger (HTTP 404)` or `(Connection error: TimeoutError)`. Check the charger is powered on, HA can reach the charger IP/hostname, credentials are correct, and the selected model matches the charger |
| Controls do not respond | Check **Connection Quality**, whether the charger is reachable, and the credentials via **Reconfigure**; then wait for the next poll |
| SOC sensors are missing | Set the integration mode to Advanced under Configure, the integration reloads automatically after saving |
| SOC looks wrong after unplug/replug | Update `number.eveus_ev_charger_initial_soc` to the real battery percentage before starting the next session |
| Charger is powered off | This is normal: the integration polls less often and does not fill the log with errors |
| A Repairs notice appeared | See [Safety notices](#-safety-notices) for what each one means and what to do |
| The charger is nowhere in Devices | See [The charger does not appear after installing](#the-charger-does-not-appear-after-installing) |

### The charger does not appear after installing

Work through these in order:

1. **Installing from HACS does not add the integration.** HACS only downloads the files, and it creates its own entry named *Eveus EV Charger* holding a single update entity — that entry is HACS, not your charger. After installing, restart Home Assistant, then go to **Settings → Devices & Services → Add Integration** and add **Eveus EV Charger** separately.
2. **Check the disabled integrations.** A disabled entry vanishes from the device list entirely. On **Settings → Devices & Services**, scroll to the bottom of the **Integrations** tab and click **Show disabled integrations**; re-enable the entry from there.
3. **Check the Integrations tab, not Devices.** If setup fails, the entry exists but no device or entities are created yet — so it is invisible under Devices while the *Eveus* card on the **Integrations** tab shows **Failed setup, will retry**. Open that card to see the reason, which also appears once in **Settings → System → Logs**.

The number of entities depends on the integration mode and phase count. If you see a single update entity, you are looking at the HACS entry, not the integration.

### Older charger firmware

Older firmware (R3.01.x has been reported) sets up and works: setup accepts any charger that responds, including chargers with no serial number set that return arbitrary bytes in its place. Updating is still recommended: message **@energy_star** on Telegram for the firmware files, then flash the update from the charger's web interface.

Firmware 1.x (EnergyStar V-series) also sets up and works, with some fields degraded: the firmware version is read from the charger's boot info instead of the usual field, and this firmware's own state codes are translated to the standard names (idle shows as Standby; Charging is detected while power is actually flowing). A code the integration doesn't recognize shows as `Unknown`, with the numeric code kept in the State sensor's `raw_state` attribute. Fields the firmware doesn't report at all (such as serial number, Substate, or OCPP status) stay unavailable rather than showing stale or wrong data.

If a charger still fails to set up, note the error shown in the setup dialog, find the integration's warning in **Settings → System → Logs** (it contains the HTTP status, content type, and the first bytes of the charger's reply; no debug logging needed), and open a [GitHub issue](https://github.com/ABovsh/eveus/issues) with your firmware version, the dialog error text, and that warning line. Hide anything sensitive first (your IP addresses, serial numbers).

## Privacy And Diagnostics

Home Assistant stores connection details, SOC settings and the last-session summary. Entity history follows your Recorder settings. Diagnostics downloads redact credentials and identifying fields before export. Charger communication stays on your LAN.

---

If this integration is useful to you, please ⭐ the repo — it helps others find it.

## License

MIT.
