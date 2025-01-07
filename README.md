# Eveus EV Charger Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

This custom integration allows you to monitor and control your Eveus EV charger in Home Assistant.

## Features

- Real-time monitoring of:
  - Voltage, Current, and Power measurements
  - Session and Total Energy tracking
  - Charging State and Substate monitoring
  - Temperature monitoring (Box and Plug)
  - Session Time tracking
  - Battery Voltage monitoring
  - Energy Counters (A/B) with cost tracking
- Advanced Controls:
  - Charging Current Adjustment (8A-16A)
  - Stop/Start Charging
  - One Charge Mode
  - Counter Reset functionality
- Smart Features:
  - Automatic error recovery
  - Connection retry mechanism
  - State restoration after restarts
  - Comprehensive diagnostic data
  - Cost tracking in local currency

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

1. Go to Configuration → Integrations
2. Click "+ Add Integration"
3. Search for "Eveus"
4. Enter your:
   - IP Address
   - Username
   - Password

## Available Entities

### Sensors
| Entity | Name | Description | Unit |
|--------|------|-------------|------|
| sensor.eveus_ev_charger_voltage | Voltage | Current voltage | V |
| sensor.eveus_ev_charger_current | Current | Charging current | A |
| sensor.eveus_ev_charger_power | Power | Charging power | W |
| sensor.eveus_ev_charger_session_energy | Session Energy | Energy used in current session | kWh |
| sensor.eveus_ev_charger_total_energy | Total Energy | Total energy delivered | kWh |
| sensor.eveus_ev_charger_state | State | Charger state | - |
| sensor.eveus_ev_charger_substate | Substate | Detailed status | - |
| sensor.eveus_ev_charger_box_temperature | Box Temperature | Internal temperature | °C |
| sensor.eveus_ev_charger_plug_temperature | Plug Temperature | Plug temperature | °C |
| sensor.eveus_ev_charger_battery_voltage | Battery Voltage | Internal battery voltage | V |
| sensor.eveus_ev_charger_counter_a_energy | Counter A Energy | Energy counter A | kWh |
| sensor.eveus_ev_charger_counter_b_energy | Counter B Energy | Energy counter B | kWh |
| sensor.eveus_ev_charger_counter_a_cost | Counter A Cost | Cost counter A | ₴ |
| sensor.eveus_ev_charger_counter_b_cost | Counter B Cost | Cost counter B | ₴ |
| sensor.eveus_ev_charger_session_time | Session Time | Current session duration | - |
| sensor.eveus_ev_charger_ground | Ground | Ground connection status | - |
| sensor.eveus_ev_charger_enabled | Enabled | Charging enabled status | - |

### Controls
| Entity | Name | Description |
|--------|------|-------------|
| number.eveus_ev_charger_charging_current | Charging Current | Control charging current (8-16A) |
| switch.eveus_ev_charger_stop_charging | Stop Charging | Control charging state |
| switch.eveus_ev_charger_one_charge | One Charge | Enable/disable one charge mode |
| switch.eveus_ev_charger_reset_counter_a | Reset Counter A | Reset energy counter A |

## States

The charger reports the following states:

- Startup
- System Test
- Standby
- Connected
- Charging
- Charge Complete
- Paused
- Error

## Error States
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

## Acknowledgments

Created and maintained by ABovsh
