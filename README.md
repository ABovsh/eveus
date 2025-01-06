# README.md - Updated
# Eveus EV Charger Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

This custom integration allows you to monitor and control your Eveus EV charger in Home Assistant.

## Features

- Real-time monitoring of:
  - Voltage, Current, and Power
  - Session and Total Energy
  - Charging State and Status
  - Temperatures (Box and Plug)
  - Session Time and Battery Voltage
  - Counter A/B Energy and Cost
- Automatic error recovery
- State restoration after restarts
- Comprehensive diagnostic data

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

## Available Sensors

| Sensor | Description | Unit |
|--------|-------------|------|
| Voltage | Current voltage | V |
| Current | Charging current | A |
| Power | Charging power | W |
| Session Energy | Energy used in current session | kWh |
| Total Energy | Total energy delivered | kWh |
| State | Charger state | - |
| Substate | Detailed status | - |
| Box Temperature | Internal temperature | °C |
| Plug Temperature | Plug temperature | °C |
| Battery Voltage | Internal battery voltage | V |
| Counter A/B Energy | Energy counters | kWh |
| Counter A/B Cost | Cost counters | ₴ |
| Session Time | Current session duration | - |

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

## Support

For bugs and feature requests, please open an issue on GitHub.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
