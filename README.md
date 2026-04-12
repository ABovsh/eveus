# Eveus EV Charger Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-3.0.3-blue)
![Stability](https://img.shields.io/badge/stability-stable-green)

Custom integration for monitoring and controlling Eveus EV chargers in Home Assistant. Supports real-time monitoring, smart current control, energy tracking, multi-rate billing, and SOC estimation.

## What's New in v3

### Multi-Device Support
You can now add **multiple Eveus chargers** to the same Home Assistant instance. Each charger gets its own device entry, entities, and unique IDs. Existing single-charger setups are fully backward compatible — no reconfiguration needed.

### 48A Model Support
Added support for the **48A charger model** alongside existing 16A and 32A models.

### Safer Restarts
Switches and number controls **no longer send commands to the charger on Home Assistant restart**. Previously, HA would re-apply the last known state on boot, which could override manual changes made on the charger itself. Now only the display value is restored.

### Optimistic UI for Controls
The current slider and charging switches now provide **instant visual feedback** when you interact with them, then reconcile with the actual device state within seconds. No more waiting 30–60 seconds to see your changes reflected.

### Smarter Offline Handling
- **Silent mode**: When the charger is powered off for an extended period, the integration stops flooding logs and quietly waits for it to come back.
- **Grace periods**: Brief WiFi drops (under 60 seconds) no longer cause sensors to flicker to "Unavailable".
- **Control safety**: Switches and current controls use a shorter 30-second grace period — if the device is offline, controls become unavailable quickly to prevent stale commands.

### Leaner Codebase
The integration was refactored from **19 files / ~3,500 lines** down to **15 files / ~3,200 lines** while gaining all the features above. Five modules were removed (`entity_registry.py`, `fallback_creator.py`, `input_status_sensor.py`, `network_utils.py`, and the old `sensor_registry.py`), with their useful functionality consolidated into the remaining files.

### Bug Fixes
- Fixed connection validation checking HTTP 401 after already raising an exception (dead code).
- Fixed `is_dst()` cache that never actually reused entries (keyed on raw float timestamps).
- Fixed entity updates being silently skipped when only temperature, cost, or rate data changed.
- Fixed randomized backoff using a deterministic clock instead of actual randomness.
- Fixed config flow and setup both assigning device numbers (potential race condition).
- Removed `aiohttp` and `voluptuous` from `manifest.json` requirements (they are HA core dependencies).
- Network session now uses Home Assistant's shared HTTP session instead of a custom one, preventing orphaned connections.
- Fixed an issue where the EV Battery Sensor would fail to report a completely empty battery (0 kWh), incorrectly showing the last known value instead.
- Fixed a logic error in network monitoring. The integration will now correctly log and notify you when a device appears to be offline.
- Updated English translations to correctly reflect support for 48A charging models.
- Synced missing error strings (invalid_input, invalid_device) to ensure clear feedback when configuration issues occur.

### Optional EV Helpers
The SOC helper entities (`input_number.ev_*`) are now **fully optional**. If they're missing, the integration works normally — you just won't see SOC and time-to-target sensors. Previously, missing helpers could cause warnings in logs.

---

## Prerequisites

### Optional Helper Entities

For SOC tracking features (battery percentage, time to target), create these helper entities in **Settings → Devices & Services → Helpers → + Create Helper → Number**:

```yaml
input_number:
  ev_battery_capacity:
    name: "EV Battery Capacity"
    min: 10
    max: 160
    step: 1
    initial: 80
    unit_of_measurement: "kWh"
    mode: slider
    icon: mdi:car-battery

  ev_initial_soc:
    name: "Initial EV State of Charge"
    min: 0
    max: 100
    step: 1
    initial: 20
    unit_of_measurement: "%"
    mode: slider
    icon: mdi:battery-charging-40

  ev_soc_correction:
    name: "Charging Efficiency Loss"
    min: 0
    max: 15
    step: 0.1
    initial: 7.5
    unit_of_measurement: "%"
    mode: slider
    icon: mdi:chart-bell-curve

  ev_target_soc:
    name: "Target SOC"
    min: 0
    max: 100
    step: 5
    initial: 80
    unit_of_measurement: "%"
    mode: slider
    icon: mdi:battery-charging-high
```

> **Note**: The integration will work without these helpers. The "Input Entities Status" diagnostic sensor shows which helpers are present and provides setup instructions for any that are missing.

## Features

**Monitoring** — Real-time voltage, current, power, energy, temperatures, and ground status. Session and lifetime energy counters with cost tracking in UAH.

**EV Tracking** — SOC estimation in kWh and percent, time-to-target calculation with efficiency correction, all updating instantly when you change input values.

**Controls** — Charging current slider (model-aware limits), start/stop charging, one-charge mode, counter reset.

**Reliability** — Adaptive polling (30s charging / 60s idle), exponential backoff on errors, silent offline mode, WiFi-optimized grace periods, connection quality monitoring.

**Multi-Rate Billing** — Primary rate, Rate 2, and Rate 3 cost tracking with schedule enable/disable status.

**Multi-Device** — Run multiple chargers on the same HA instance with independent entities and device entries.

## Installation

### HACS (Recommended)

1. Open HACS → click ⋮ → **Custom repositories**
2. Add `https://github.com/ABovsh/eveus` as an **Integration**
3. Search "Eveus" in HACS Integrations and install
4. Restart Home Assistant

### Manual

1. Copy `custom_components/eveus` to your HA `custom_components` folder
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → + Add Integration**
2. Search for **Eveus**
3. Enter your charger's IP address, username, password, and model (16A, 32A, or 48A)

To add a second charger, repeat the steps with the other charger's IP address.

## Available Entities

### Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| Voltage | Current voltage | V |
| Current | Charging current | A |
| Power | Charging power | W |
| Current Set | Configured current limit | A |
| Session Energy | Energy in current session | kWh |
| Session Time | Duration of current session | — |
| Total Energy | Lifetime energy delivered | kWh |
| Counter A / B Energy | Energy counters | kWh |
| Counter A / B Cost | Cost counters | ₴ |
| SOC Energy | Battery charge level (requires helpers) | kWh |
| SOC Percent | Battery charge level (requires helpers) | % |
| Time to Target SOC | Estimated time remaining (requires helpers) | — |

### Diagnostics

| Entity | Description |
|--------|-------------|
| State | Charger state (Standby, Connected, Charging, etc.) |
| Substate | Detailed status / error info |
| Ground | Ground connection status |
| Box / Plug Temperature | Internal and plug temperatures |
| Battery Voltage | Backup battery voltage |
| System Time | Charger internal clock |
| Connection Quality | Network reliability percentage |
| Input Entities Status | Shows which SOC helpers are configured |

### Rate Sensors

| Entity | Description |
|--------|-------------|
| Primary / Active Rate Cost | Current electricity rate |
| Rate 2 / 3 Cost | Time-based rates |
| Rate 2 / 3 Status | Schedule enabled/disabled |

### Controls

| Entity | Description |
|--------|-------------|
| Charging Current | Set current limit (slider, model-aware min/max) |
| Stop Charging | Enable/disable EVSE |
| One Charge | Single charge session mode |
| Reset Counter A | Clear primary energy counter |

## UI Examples

### Entities Card

![Basic Entities Card](https://github.com/user-attachments/assets/b79ee8b2-8604-4d31-aba2-76b08b320daf)

```yaml
type: entities
entities:
  - entity: sensor.eveus_ev_charger_soc_percent
    name: SOC (%)
  - entity: sensor.eveus_ev_charger_soc_energy
    name: SOC (kWh)
  - entity: sensor.eveus_ev_charger_time_to_target_soc
    name: Time to Target
  - type: divider
  - entity: sensor.eveus_ev_charger_state
    name: Charger State
  - entity: sensor.eveus_ev_charger_substate
    name: Substate
  - type: divider
  - entity: sensor.eveus_ev_charger_power
    name: Power (W)
  - entity: sensor.eveus_ev_charger_counter_a_energy
    name: Session Energy (kWh)
  - type: divider
  - entity: number.eveus_ev_charger_charging_current
    name: Current (A)
  - entity: input_number.ev_initial_soc
    name: Initial SOC (%)
  - entity: input_number.ev_target_soc
    name: Target SOC (%)
  - entity: input_number.ev_soc_correction
    name: SOC Correction (%)
  - entity: input_number.ev_battery_capacity
    name: Battery Capacity (kWh)
  - type: divider
  - entity: switch.eveus_ev_charger_one_charge
    name: One Charge Mode
  - entity: switch.eveus_ev_charger_stop_charging
    name: Stop Charging
  - entity: switch.eveus_ev_charger_reset_counter_a
    name: Reset Energy Counter
show_header_toggle: false
```

### Interactive Control Panel

![Interactive Control Panel](https://github.com/user-attachments/assets/afac498a-03d9-44a2-8fcd-602d5e1a64e9)

Requires [Slider Button Card](https://github.com/custom-cards/slider-button-card) and [Button Card](https://github.com/custom-cards/button-card) from HACS.

```yaml
type: vertical-stack
cards:
  - type: horizontal-stack
    cards:
      - type: custom:slider-button-card
        entity: number.eveus_ev_charger_charging_current
        name: Current
        compact: true
        slider:
          direction: left-right
          background: gradient
          use_state_color: true
          show_track: true
          min: 8
          max: 16
          step: 1
        icon:
          show: true
          icon: mdi:flash
          tap_action:
            action: more-info
        show_name: true
        show_state: true
        unit: " A"
        action_button:
          show: false
        styles:
          slider:
            height: 35px
            width: 95%
          card:
            padding: 4px
            height: 55px
          name:
            font-size: 12px
            font-weight: bold
          state:
            font-size: 16px
            font-weight: bold
      - type: custom:slider-button-card
        entity: input_number.ev_initial_soc
        name: Init SOC
        compact: true
        slider:
          direction: left-right
          background: gradient
          use_state_color: true
          show_track: true
          min: 0
          max: 100
          step: 1
        icon:
          show: true
          icon: mdi:battery
          tap_action:
            action: more-info
        show_name: true
        show_state: true
        unit: "%"
        action_button:
          show: false
        styles:
          slider:
            height: 35px
            width: 95%
          card:
            padding: 4px
            height: 55px
          name:
            font-size: 12px
            font-weight: bold
          state:
            font-size: 16px
            font-weight: bold
  - type: horizontal-stack
    cards:
      - type: custom:button-card
        entity: switch.eveus_ev_charger_reset_counter_a
        name: Reset Counter
        icon: mdi:restart
        size: 30%
        tap_action:
          action: toggle
          confirmation:
            text: Reset the energy counter?
        styles:
          card:
            - height: 45px
            - padding: 4px
          name:
            - font-size: 12px
            - font-weight: bold
      - type: custom:button-card
        entity: switch.eveus_ev_charger_one_charge
        name: OneCharge
        icon: mdi:ev-station
        size: 35%
        tap_action:
          action: toggle
        styles:
          card:
            - height: 45px
            - padding: 4px
          name:
            - font-size: 12px
            - font-weight: bold
      - type: custom:button-card
        entity: switch.eveus_ev_charger_stop_charging
        name: Stop Charging
        icon: mdi:stop-circle
        size: 35%
        tap_action:
          action: toggle
          confirmation:
            text: Stop charging session?
        styles:
          card:
            - height: 45px
            - padding: 4px
          name:
            - font-size: 12px
            - font-weight: bold
```

> **Tip**: Adjust the current slider max to match your model (16, 32, or 48).

## Notifications

You can set up automations for charging events. Example notification automations are available in the repository's [Notifications](https://github.com/ABovsh/eveus/tree/main/Notifications) folder:

- **Session Started** — notifies when charging begins
- **Current Changed** — notifies when charging current changes
- **Session Complete** — notifies when charging finishes

## Troubleshooting

### Quick Checks

1. Verify the charger is reachable on your network (try `http://<IP>` in a browser)
2. Check the **Connection Quality** sensor for connectivity issues
3. Check the **Input Entities Status** sensor for missing SOC helpers
4. Review HA logs for `eveus` entries

### Enable Debug Logging

```yaml
logger:
  default: info
  logs:
    custom_components.eveus: debug
```

### Diagnostic Template

Paste this in **Developer Tools → Template** to check the integration state:

```yaml
{% set inputs = {
    'initial_soc': states('input_number.ev_initial_soc'),
    'battery_capacity': states('input_number.ev_battery_capacity'),
    'soc_correction': states('input_number.ev_soc_correction'),
    'target_soc': states('input_number.ev_target_soc')
} %}
{% set sensors = {
    'counter_a': states('sensor.eveus_ev_charger_counter_a_energy'),
    'soc_energy': states('sensor.eveus_ev_charger_soc_energy'),
    'soc_percent': states('sensor.eveus_ev_charger_soc_percent'),
    'time_to_target': states('sensor.eveus_ev_charger_time_to_target_soc'),
    'power': states('sensor.eveus_ev_charger_power'),
    'state': states('sensor.eveus_ev_charger_state'),
    'connection': states('sensor.eveus_ev_charger_connection_quality')
} %}

### Input Helpers ###
{% for k, v in inputs.items() %}
- {{ k }}: {{ v }}
{% endfor %}

### Sensors ###
{% for k, v in sensors.items() %}
- {{ k }}: {{ v }}
{% endfor %}
```

### Common Issues

| Issue | Solution |
|-------|----------|
| SOC sensors show "Unavailable" | Create the required `input_number` helpers (see Prerequisites) |
| Controls don't respond | Check that the charger is online (Connection Quality sensor) |
| Entity names changed after update | Entity unique IDs are preserved — display names may differ but automations using entity IDs still work |
| Multiple chargers show same data | Each charger must have a unique IP address |

### Reset Procedure

If you encounter persistent issues:
1. Remove the integration from **Settings → Devices & Services**
2. Restart Home Assistant
3. Re-add the integration

## Support

For bugs and feature requests, open an issue on [GitHub](https://github.com/ABovsh/eveus/issues).

## License

MIT License — see LICENSE file for details.
