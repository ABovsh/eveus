# Eveus EV Charger Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-2.0.0-blue)
![Stability](https://img.shields.io/badge/stability-stable-green)

This custom integration provides comprehensive monitoring and control of Eveus EV chargers in Home Assistant, featuring advanced state tracking, current control, energy monitoring, and improved SOC tracking.

## Prerequisites

### Required Helper Entities
Before installing the integration, you must create these helper entities in Home Assistant:

1. Go to Settings → Devices & Services → Helpers
2. Click the "+ CREATE HELPER" button
3. Choose "Number"
4. Create each of these helpers with the exact input_number names:

```yaml
input_number:
  ev_battery_capacity:
    name: "EV Battery Capacity"
    min: 10
    max: 160
    step: 1
    unit_of_measurement: "kWh"
    mode: slider      # Optional but recommended
    icon: mdi:car-battery
    # Initial value should match your EV's battery capacity

  ev_initial_soc:
    name: "Initial EV State of Charge"
    min: 0
    max: 100
    step: 1
    unit_of_measurement: "%"
    mode: slider      # Optional but recommended
    icon: mdi:battery-charging-40
    # Set this before each charging session

  ev_soc_correction:
    name: "Charging Efficiency Loss"
    min: 0
    max: 10
    step: 0.1
    initial: 7.5     # Default efficiency loss
    unit_of_measurement: "%"
    mode: slider      # Optional but recommended
    icon: mdi:chart-bell-curve
    # Adjust based on your observed charging efficiency

  ev_target_soc:
    name: "Target SOC"
    min: 80
    max: 100
    step: 10
    initial: 80      # Default target
    unit_of_measurement: "%"
    mode: slider      # Optional but recommended
    icon: mdi:battery-charging-high
    # Adjust based on your charging needs
```

Alternatively, you can add these helpers via YAML by adding the above configuration to your `configuration.yaml`.

> **Important**: The integration will verify these helpers exist during setup and display an error if any are missing or incorrectly configured.

## Features

### 🔌 Basic Monitoring
- Real-time voltage, current, and power monitoring
- Session and total energy tracking
- Temperature monitoring (box and plug)
- Ground connection safety monitoring
- Battery voltage monitoring
- Energy counters with cost tracking (in UAH)
- Enhanced session time formatting with days, hours, and minutes
- Improved measurement precision

### 🚗 Advanced EV Features
- Accurate State of Charge monitoring (kWh and percentage)
- Dynamic time-to-target calculation with real-time updates
- Charging efficiency calculation and correction
- Comprehensive session time tracking
- Smart SOC estimation based on charging patterns
- Real-time efficiency adjustments during charging
- Accurate remaining time calculations based on current conditions

### 🛡️ Reliability Features
- Connection quality monitoring and reporting
- Automatic error recovery with exponential backoff
- Smart state restoration after system restarts
- Enhanced error tracking and diagnostics
- Improved network interruption handling
- Detailed connection status reporting
- Automatic recovery from temporary failures

### 🎮 Control Features
- Dynamic charging current control (8-16A or 8-32A based on model)
- Start/Stop charging control
- One charge mode support
- Counter reset functionality
- Current adjustment with safety limits
- Improved state persistence
- Enhanced command reliability

### 📊 Diagnostic Features
- Connection quality metrics
- Detailed error tracking and reporting
- Temperature monitoring with improved accuracy
- Ground connection monitoring
- Enhanced status reporting
- Comprehensive error logging
- System performance monitoring

## Installation

### Method 1: HACS (Recommended)
1. Add this repository to HACS as a custom repository:
   ```
   Repository: https://github.com/ABovsh/eveus
   Category: Integration
   ```
2. Click Install
3. Restart Home Assistant

### Method 2: Manual Installation
1. Download the repository
2. Copy the `custom_components/eveus` directory to your Home Assistant's `custom_components` folder
3. Restart Home Assistant

## Configuration

### Initial Setup
1. Create all required helper entities as described in Prerequisites
2. Go to Configuration → Integrations
3. Click "+ Add Integration"
4. Search for "Eveus"
5. Enter the following details:
   - IP Address
   - Username
   - Password
   - Charger Model (16A or 32A)

### Available Entities

#### Basic Sensors
| Entity | Name | Description | Unit |
|--------|------|-------------|------|
| sensor.eveus_ev_charger_voltage | Voltage | Current voltage | V |
| sensor.eveus_ev_charger_current | Current | Charging current | A |
| sensor.eveus_ev_charger_power | Power | Charging power | W |
| sensor.eveus_ev_charger_session_energy | Session Energy | Energy used in session | kWh |
| sensor.eveus_ev_charger_total_energy | Total Energy | Total energy delivered | kWh |
| sensor.eveus_ev_charger_counter_a_energy | Counter A Energy | Energy counter A | kWh |
| sensor.eveus_ev_charger_counter_b_energy | Counter B Energy | Energy counter B | kWh |
| sensor.eveus_ev_charger_counter_a_cost | Counter A Cost | Cost counter A | ₴ |
| sensor.eveus_ev_charger_counter_b_cost | Counter B Cost | Cost counter B | ₴ |

#### SOC Sensors
| Entity | Name | Description | Unit |
|--------|------|-------------|------|
| sensor.eveus_ev_charger_soc_energy | SOC Energy | Current battery charge | kWh |
| sensor.eveus_ev_charger_soc_percent | SOC Percent | Current battery charge | % |
| sensor.eveus_ev_charger_time_to_target | Time to Target | Estimated charging time remaining | - |

#### Diagnostic Sensors
| Entity | Name | Description |
|--------|------|-------------|
| sensor.eveus_ev_charger_state | State | Charger state |
| sensor.eveus_ev_charger_substate | Substate | Detailed status |
| sensor.eveus_ev_charger_ground | Ground | Ground connection status |
| sensor.eveus_ev_charger_enabled | Enabled | Charging enabled status |
| sensor.eveus_ev_charger_connection_errors | Connection Errors | Connection reliability monitoring |

#### Temperature Sensors
| Entity | Name | Description | Unit |
|--------|------|-------------|------|
| sensor.eveus_ev_charger_box_temperature | Box Temperature | Internal temperature | °C |
| sensor.eveus_ev_charger_plug_temperature | Plug Temperature | Plug temperature | °C |

#### Controls
| Entity | Name | Description |
|--------|------|-------------|
| number.eveus_ev_charger_charging_current | Charging Current | Control charging current (8-16A/32A) |
| switch.eveus_ev_charger_stop_charging | Stop Charging | Control charging state |
| switch.eveus_ev_charger_one_charge | One Charge | Enable one charge mode |
| switch.eveus_ev_charger_reset_counter_a | Reset Counter A | Reset energy counter A |

### Usage Tips

#### 1. Before Starting a Charging Session
- Set the correct EV battery capacity
- Set the current state of charge (initial_soc)
- Adjust the efficiency correction if needed
- Set your desired target SOC

#### 2. During Charging
- Monitor charging progress with improved SOC sensors
- Check the new time-to-target estimation
- Monitor connection stability with the new error sensor
- Adjust current if needed using the slider

#### 3. After Charging
- Reset Counter A before starting a new session
- Record efficiency for future reference
- Check total energy usage in session history
- Review connection quality metrics

### Troubleshooting
If you encounter issues:
1. Check all helper entities are properly configured
2. Monitor the connection_errors sensor for network issues
3. Verify network connectivity to the charger
4. Check the logs for detailed error messages
5. Restart the integration if needed

## Support

For bugs and feature requests, please open an issue on GitHub.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
