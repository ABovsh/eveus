# Eveus EV Charger Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

This custom integration allows you to monitor and control your Eveus EV charger in Home Assistant.

## Table of Contents
- [Features](#features)
  - [Basic Monitoring](#basic-monitoring)
  - [Advanced EV Features](#advanced-ev-features)
  - [Control Features](#control-features)
- [Installation](#installation)
  - [HACS Installation](#hacs-recommended)
  - [Manual Installation](#manual-installation)
- [Configuration](#configuration)
  - [Initial Setup](#initial-setup)
  - [Required Helper Entities](#required-helper-entities)
- [Available Entities](#available-entities)
  - [Sensors](#sensors)
  - [Number Entities](#number-entities)
  - [Switches](#switches)
- [State Information](#state-information)
  - [Charging States](#charging-states)
  - [Error States](#error-states)
- [Support](#support)
- [License](#license)

## Features

### Basic Monitoring
- Real-time voltage, current, and power monitoring
- Session and total energy tracking
- Temperature monitoring (box and plug)
- Ground connection safety monitoring
- Battery voltage monitoring
- State and substates tracking with detailed status
- Energy counters with cost tracking

### Advanced EV Features
- Charging current control (8-16A or 8-32A based on model)
- State of Charge monitoring (kWh and percentage)
- Time to target calculation
- Charging efficiency calculation
- Session time tracking with formatted display

### Control Features
- Start/Stop charging
- One charge mode
- Counter reset functionality
- Current adjustment with min/max safety limits

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository:
   - Repository: `https://github.com/ABovsh/eveus`
   - Category: `Integration`
2. Click Install
3. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/eveus` directory to your Home Assistant's `custom_components` folder
2. Restart Home Assistant

## Configuration

### Initial Setup
1. Go to Configuration → Integrations
2. Click "+ Add Integration"
3. Search for "Eveus"
4. Enter:
   - IP Address
   - Username
   - Password
   - Charger Model (16A or 32A)

### Required Helper Entities
The following input_number helpers are required for advanced features:
```yaml
input_number:
  ev_battery_capacity:
    name: "EV Battery Capacity"
    min: 10
    max: 100
    step: 1
    unit_of_measurement: "kWh"
    icon: mdi:car-battery

  initial_ev_soc:
    name: "Initial EV State of Charge"
    min: 0
    max: 100
    step: 1
    unit_of_measurement: "%"
    icon: mdi:battery-charging-40

  ev_soc_correction:
    name: "Charging Efficiency Loss"
    min: 0
    max: 10
    step: 0.1
    initial: 7.5
    unit_of_measurement: "%"
    icon: mdi:chart-bell-curve

  target_soc:
    name: "Target SOC"
    min: 80
    max: 100
    step: 10
    initial: 80
    unit_of_measurement: "%"
    icon: mdi:battery-charging-high
```

## Available Entities

### Sensors
| Entity | Name | Description | Unit |
|--------|------|-------------|------|
| sensor.eveus_ev_charger_voltage | Voltage | Current voltage | V |
| sensor.eveus_ev_charger_current | Current | Charging current | A |
| sensor.eveus_ev_charger_power | Power | Charging power | W |
| sensor.eveus_ev_charger_session_energy | Session Energy | Energy used in session | kWh |
| sensor.eveus_ev_charger_total_energy | Total Energy | Total energy delivered | kWh |
| sensor.eveus_ev_charger_box_temperature | Box Temperature | Internal temperature | °C |
| sensor.eveus_ev_charger_plug_temperature | Plug Temperature | Plug temperature | °C |
| sensor.eveus_ev_charger_battery_voltage | Battery Voltage | Internal battery voltage | V |
| sensor.eveus_ev_charger_counter_a_energy | Counter A Energy | Energy counter A | kWh |
| sensor.eveus_ev_charger_counter_b_energy | Counter B Energy | Energy counter B | kWh |
| sensor.eveus_ev_charger_counter_a_cost | Counter A Cost | Cost counter A | ₴ |
| sensor.eveus_ev_charger_counter_b_cost | Counter B Cost | Cost counter B | ₴ |
| sensor.eveus_ev_charger_soc_kwh | EV State of Charge | Current battery charge | kWh |
| sensor.eveus_ev_charger_soc_percent | EV State of Charge | Current battery charge | % |
| sensor.eveus_ev_charger_time_to_target | Time to Target | Time until target SOC | - |

### Number Entities
| Entity | Name | Description | Range |
|--------|------|-------------|--------|
| number.eveus_ev_charger_charging_current | Charging Current | Control charging current | 8-16A/32A |

### Switches
| Entity | Name | Description |
|--------|------|-------------|
| switch.eveus_ev_charger_stop_charging | Stop Charging | Control charging state |
| switch.eveus_ev_charger_one_charge | One Charge | Enable one charge mode |
| switch.eveus_ev_charger_reset_counter_a | Reset Counter A | Reset energy counter A |

### Diagnostic Sensors
| Entity | Name | Description |
|--------|------|-------------|
| sensor.eveus_ev_charger_state | State | Charger state |
| sensor.eveus_ev_charger_substate | Substate | Detailed status |
| sensor.eveus_ev_charger_ground | Ground | Ground connection status |
| sensor.eveus_ev_charger_enabled | Enabled | Charging enabled status |

## State Information

### Charging States
- Startup
- System Test
- Standby
- Connected
- Charging
- Charge Complete
- Paused
- Error

### Error States
When in error state, the charger can report:
- No Error
- Grounding Error
- Current Leak (High/Low)
- Relay Error
- Temperature Errors (Box/Plug)
- Pilot Error
- Voltage Errors
- Overcurrent
- System Errors

## Support

For bugs and feature requests, please open an issue on GitHub.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
