# Eveus EV Charger Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-2.0.0-blue)
![Stability](https://img.shields.io/badge/stability-stable-green)

This custom integration provides comprehensive monitoring and control of Eveus EV chargers in Home Assistant, featuring advanced state tracking, dynamic update intervals, smart current control, energy monitoring, and improved SOC tracking.

## Table of Contents

- [New in Version 2.0.0](#new-in-version-200)
- [Prerequisites](#prerequisites)
  - [Required Helper Entities](#required-helper-entities)
- [Features](#features)
  - [Basic Monitoring](#-basic-monitoring)
  - [Advanced EV Features](#-advanced-ev-features)
  - [Reliability Features](#-reliability-features)
  - [Control Features](#-control-features)
  - [Diagnostic Features](#-diagnostic-features)
- [Installation](#installation)
  - [HACS (Recommended)](#method-1-hacs-recommended)
  - [Manual Installation](#method-2-manual-installation)
- [Configuration](#configuration)
  - [Initial Setup](#initial-setup)
  - [Available Entities](#available-entities)
  - [Usage Tips](#usage-tips)
- [UI Configuration](#ui-configuration)
  - [Basic Entities Card](#basic-entities-card)
  - [Interactive Control Panel](#interactive-control-panel)
- [Notifications](#notifications)
  - [Setting Up Notifications](#setting-up-notifications)
- [Troubleshooting](#troubleshooting)
  - [Advanced Debugging](#advanced-debugging)
  - [Common Issues and Solutions](#common-issues-and-solutions)
  - [Reset Procedure](#reset-procedure)
- [Support](#support)
- [License](#license)

## New in Version 2.0.0
- **Dynamic Update Frequency**: 30-second updates when charging, 60-second updates when idle
- **Enhanced State Management**: Immediate and reliable switch state updates
- **Performance Optimizations**: Reduced network traffic with efficient data caching
- **Smart Error Handling**: Automatic recovery with exponential backoff
- **Improved Device Info**: Accurate firmware version display and better identification
- **Modular Architecture**: Better maintainability and reliability
- **Zero Breaking Changes**: Full backward compatibility maintained

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

> **Important**: The integration will verify these helpers exist during setup and display an error if any are missing or incorrectly configured.

## Features

### 🔌 Basic Monitoring
- Real-time voltage, current, and power monitoring with improved accuracy
- Session and total energy tracking with persistent storage
- Temperature monitoring (box and plug) with enhanced precision
- Ground connection safety monitoring
- Battery voltage monitoring
- Energy counters with cost tracking (in UAH)
- Enhanced session time formatting with days, hours, and minutes
- Smart data caching to minimize device queries

### 🚗 Advanced EV Features
- Accurate State of Charge monitoring (kWh and percentage)
- Dynamic time-to-target calculation with efficiency correction
- Comprehensive session time tracking
- Smart SOC estimation based on charging patterns
- Real-time efficiency adjustments during charging
- Accurate remaining time calculations based on current conditions

### 🛡️ Reliability Features
- Dynamic update intervals based on charging state
- Smart retry logic with exponential backoff
- Efficient state caching and restoration
- Enhanced error handling and recovery
- Comprehensive connection monitoring
- Automatic recovery from network issues
- Detailed diagnostic reporting

### 🎮 Control Features
- Dynamic charging current control (8-16A or 8-32A based on model)
- Reliable start/stop charging control
- One charge mode support
- Counter reset functionality
- Current adjustment with safety limits
- Improved command reliability
- Immediate state feedback

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

#### Step-by-Step Instructions for Adding to HACS

1. **Access HACS**
   - Open your Home Assistant dashboard
   - On the sidebar menu, find and click on "HACS"
   - This will open the HACS main page

2. **Add Custom Repository**
   - In the top-right corner of the HACS page, click on the three vertical dots (⋮) menu
   - From the dropdown menu, select "Custom repositories"
   - A dialog box will appear asking for repository information
   - Fill in the following information:
     - In the "Repository" field, paste this URL: `https://github.com/ABovsh/eveus`
     - In the "Category" dropdown menu, select "Integration"
   - Click the "ADD" button

3. **Install the Integration**
   - After adding the repository, go back to the HACS main page
   - Click on the "Integrations" section
   - In the search box at the top, type "Eveus"
   - The Eveus EV Charger integration should appear in the results
   - Click on the "Eveus EV Charger" card
   - On the integration detail page, click the "DOWNLOAD" button in the bottom-right corner
   - A confirmation dialog will appear - click "DOWNLOAD" again
   - Wait for the download to complete (this should only take a few seconds)

4. **Restart Home Assistant**
   - After successful installation, you need to restart Home Assistant
   - Go to your Home Assistant main menu (sidebar)
   - Click on "Configuration"
   - Click on "Server Controls"
   - Under the "Server management" section, click the "RESTART" button
   - Wait for Home Assistant to restart completely (this may take several minutes)

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
| sensor.eveus_ev_charger_time_to_target_soc | Time to Target SOC | Estimated charging time remaining | - |

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

## UI Configuration

### Basic Entities Card
![Basic Entities Card](https://github.com/user-attachments/assets/b79ee8b2-8604-4d31-aba2-76b08b320daf)

This card provides a comprehensive view of all charging information and controls in a clean, organized layout:

```yaml
type: entities
entities:
  - entity: sensor.eveus_ev_charger_soc_percent
    name: SOC (%)
    icon: mdi:battery-charging-80
  - entity: sensor.eveus_ev_charger_soc_energy
    name: SOC (kWh)
    icon: mdi:battery
  - entity: sensor.eveus_ev_charger_time_to_target_soc
    name: Time to Target
    icon: mdi:clock-time-four
  - type: divider
  - entity: sensor.eveus_ev_charger_state
    name: Charger State
    icon: mdi:car-electric
  - entity: sensor.eveus_ev_charger_substate
    name: Substate
    icon: mdi:car-cog
  - type: divider
  - entity: sensor.eveus_ev_charger_power
    name: Power (W)
    icon: mdi:flash
  - entity: sensor.eveus_ev_charger_counter_a_energy
    name: Session Energy (kWh)
    icon: mdi:counter
  - type: divider
  - entity: number.eveus_ev_charger_charging_current
    name: Current (A)
    icon: mdi:current-ac
  - entity: input_number.ev_initial_soc
    name: Initial SOC (%)
    icon: mdi:calendar-clock
  - entity: input_number.ev_target_soc
    name: Target SOC (%)
    icon: mdi:target
  - entity: input_number.ev_soc_correction
    name: SOC Correction (%)
    icon: mdi:chart-bell-curve
  - entity: input_number.ev_battery_capacity
    name: Battery Capacity (kWh)
    icon: mdi:car-battery
  - type: divider
  - entity: switch.eveus_ev_charger_one_charge
    name: One Charge Mode
    icon: mdi:power-plug
  - entity: switch.eveus_ev_charger_stop_charging
    name: Stop Charging
    icon: mdi:stop-circle
  - entity: switch.eveus_ev_charger_reset_counter_a
    name: Reset Energy Counter
    icon: mdi:reload
show_header_toggle: false
```

### Interactive Control Panel
![Interactive Control Panel](https://github.com/user-attachments/assets/afac498a-03d9-44a2-8fcd-602d5e1a64e9)

For a more interactive and visually appealing control experience, you can use this advanced control panel with sliders and buttons:

#### Features:
- **Interactive sliders** for precise control of charging current and initial SOC
- **One-touch buttons** for common actions like resetting counters or stopping charging
- **Visual feedback** with animations and color changes
- **Confirmation dialogs** for critical actions to prevent accidents
- **Haptic feedback** for touch devices (phones/tablets)

#### Installation:

1. **Install Required Cards**:
   - Go to HACS → Frontend
   - Search for and install "Slider Button Card" and "Button Card"
   - Restart Home Assistant

2. **Add the YAML Configuration**:
   - Go to your dashboard
   - Click the three dots in the top right → Edit Dashboard
   - Click the + button to add a new card
   - Select "Manual" card
   - Paste this code:

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
            haptic: light
        show_name: true
        show_state: true
        unit: " A"
        show_attribute: false
        action_button:
          show: false
        styles:
          slider:
            height: 35px
            width: 95%
            background: rgba(var(--rgb-primary-text-color), 0.1)
          card:
            padding: 4px
            height: 55px
            border-radius: var(--ha-card-border-radius, 12px)
            box-shadow: var(--ha-card-box-shadow, none)
          name:
            font-size: 12px
            font-weight: bold
            padding-top: 4px
            color: var(--primary-text-color)
          state:
            font-size: 16px
            font-weight: bold
            color: var(--primary-text-color)
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
            haptic: light
        show_name: true
        show_state: true
        unit: "%"
        show_attribute: false
        action_button:
          show: false
        styles:
          slider:
            height: 35px
            width: 95%
            background: rgba(var(--rgb-primary-text-color), 0.1)
          card:
            padding: 4px
            height: 55px
            border-radius: var(--ha-card-border-radius, 12px)
            box-shadow: var(--ha-card-box-shadow, none)
          name:
            font-size: 12px
            font-weight: bold
            padding-top: 4px
            color: var(--primary-text-color)
          state:
            font-size: 16px
            font-weight: bold
            color: var(--primary-text-color)
  - type: horizontal-stack
    cards:
      - type: custom:button-card
        entity: switch.eveus_ev_charger_reset_counter_a
        name: Reset Counter
        icon: mdi:restart
        size: 30%
        color_type: card
        color: var(--info-color, "#3949AB")
        tap_action:
          action: toggle
          confirmation:
            text: Reset the energy counter?
            exemptions: []
          haptic: success
        hold_action:
          action: more-info
          haptic: light
        styles:
          card:
            - border-radius: var(--ha-card-border-radius, 12px)
            - height: 45px
            - padding: 4px
            - margin: 2px
            - box-shadow: var(--ha-card-box-shadow, none)
            - transition: all 0.2s ease-in-out
          name:
            - font-size: 12px
            - font-weight: bold
            - padding-top: 2px
            - color: var(--primary-text-color)
            - transition: color 0.2s ease-in-out
          icon:
            - width: 20px
            - color: var(--primary-text-color)
            - transition: color 0.2s ease-in-out
        state:
          - value: "off"
            styles:
              card:
                - background-color: var(--card-background-color)
                - border: 1px solid var(--divider-color)
                - transform: scale(1)
          - value: "on"
            styles:
              card:
                - opacity: 0.9
                - background-color: var(--info-color)
                - transform: scale(0.95)
              name:
                - color: var(--primary-background-color)
              icon:
                - color: var(--primary-background-color)
      - type: custom:button-card
        entity: switch.eveus_ev_charger_one_charge
        name: OneCharge
        icon: mdi:ev-station
        size: 35%
        color_type: card
        color: var(--success-color, "#2E7D32")
        tap_action:
          action: toggle
          haptic: success
        hold_action:
          action: more-info
          haptic: light
        styles:
          card:
            - border-radius: var(--ha-card-border-radius, 12px)
            - height: 45px
            - padding: 4px
            - margin: 2px
            - box-shadow: var(--ha-card-box-shadow, none)
            - transition: all 0.2s ease-in-out
          name:
            - font-size: 12px
            - font-weight: bold
            - padding-top: 2px
            - color: var(--primary-text-color)
            - transition: color 0.2s ease-in-out
          icon:
            - width: 22px
            - color: var(--primary-text-color)
            - transition: color 0.2s ease-in-out
        state:
          - value: "off"
            styles:
              card:
                - background-color: var(--card-background-color)
                - border: 1px solid var(--divider-color)
                - transform: scale(1)
          - value: "on"
            styles:
              card:
                - background-color: var(--success-color)
                - animation: pulse 2s infinite
                - transform: scale(0.95)
              name:
                - color: var(--primary-background-color)
              icon:
                - color: var(--primary-background-color)
      - type: custom:button-card
        entity: switch.eveus_ev_charger_stop_charging
        name: Stop Charging
        icon: mdi:stop-circle
        size: 35%
        color_type: card
        color: var(--error-color, "#C62828")
        tap_action:
          action: toggle
          confirmation:
            text: Stop charging session?
            exemptions: []
          haptic: warning
        hold_action:
          action: more-info
          haptic: light
        styles:
          card:
            - border-radius: var(--ha-card-border-radius, 12px)
            - height: 45px
            - padding: 4px
            - margin: 2px
            - box-shadow: var(--ha-card-box-shadow, none)
            - transition: all 0.2s ease-in-out
          name:
            - font-size: 12px
            - font-weight: bold
            - padding-top: 2px
            - color: var(--primary-text-color)
            - transition: color 0.2s ease-in-out
          icon:
            - width: 22px
            - color: var(--primary-text-color)
            - transition: color 0.2s ease-in-out
        state:
          - value: "off"
            styles:
              card:
                - background-color: var(--card-background-color)
                - border: 1px solid var(--divider-color)
                - transform: scale(1)
          - value: "on"
            styles:
              card:
                - background-color: var(--error-color)
                - animation: pulse 1s infinite
                - transform: scale(0.95)
              name:
                - color: var(--primary-background-color)
              icon:
                - color: var(--primary-background-color)
```

> **Note**: You may need to adjust the charging current slider's min and max values based on your charger model (8-16A for 16A models or 8-32A for 32A models).

> **Tip**: For the best experience, place this control panel on your mobile dashboard for easy access when near your vehicle.

## Notifications

You can set up automations to receive notifications about your EV charging status. These will alert you when charging starts, completes, or when the current changes.

![Notifications Example](https://github.com/user-attachments/assets/d2bf4866-bf4e-4bc0-8415-e3c6d6d7b0e9)

### Setting Up Notifications

1. Go to Settings > Automations & Scenes
2. Click "+ Create Automation"
3. Choose "Create new automation" and select "Start with an empty automation"
4. Set up the following automations:

#### 1. Session Start Notification

This automation notifies you when your EV begins charging:

1. Download the YAML from [301_EV_Charging_Started.yaml](https://github.com/ABovsh/eveus/blob/main/Notifications/301_EV_Charging_Started%20(1).yaml)
2. Open the file in a text editor and copy all the content
3. In Home Assistant, after creating an empty automation:
   - Click the three dots in the top right corner
   - Select "Edit in YAML"
   - Delete any default content
   - Paste the YAML content from the downloaded file
   - Replace `notify.notify` or similar with your notification service (e.g., `notify.mobile_app_your_phone`)
   - Click "Save"

#### 2. Current Change Notification

This automation notifies you when the charging current changes:

1. Download the YAML from [302_EV_Charging_CurrentChanged.yaml](https://github.com/ABovsh/eveus/blob/main/Notifications/302_EV_Charging_CurrentChanged%20(1).yaml)
2. Create a new empty automation as described above
3. Edit in YAML mode and paste the content from the downloaded file
4. Replace the notification service with your preferred notification method
5. Click "Save"

#### 3. Session Complete Notification

This automation notifies you when charging is complete:

1. Download the YAML from [303_EV_Charging_Completed.yaml](https://github.com/ABovsh/eveus/blob/main/Notifications/303_EV_Charging_Completed%20(1).yaml)
2. Create a new empty automation as described above
3. Edit in YAML mode and paste the content from the downloaded file
4. Replace the notification service with your preferred notification method
5. Click "Save"

> **Tip**: If you're using Telegram, set your notification service to `notify.telegram` or your specific Telegram notification service name in each automation.

## Troubleshooting

If you encounter issues:
1. Check all helper entities are properly configured
2. Monitor the connection_errors sensor for network issues
3. Verify network connectivity to the charger
4. Check the logs for detailed error messages
5. Restart the integration if needed

### Advanced Debugging

If you're experiencing issues with the SOC calculations or other sensors, follow these debugging steps:

#### 1. Enable Enhanced Logging

Add the following to your `configuration.yaml` file:

```yaml
logger:
  default: info
  logs:
    custom_components.eveus.ev_sensors: debug
    custom_components.eveus.utils: debug
    custom_components.eveus.sensor: debug
    custom_components.eveus: warning
```

Restart Home Assistant and check the logs for detailed information about sensor calculations.

#### 2. Use the Diagnostic Template

Copy and paste this template into Developer Tools > Template to get a comprehensive diagnostic report:

```yaml
{# Debug template for Eveus SOC Sensors #}
{% set debug_data = {
    'inputs': {
        'initial_soc': states('input_number.ev_initial_soc'),
        'battery_capacity': states('input_number.ev_battery_capacity'),
        'soc_correction': states('input_number.ev_soc_correction'),
        'target_soc': states('input_number.ev_target_soc')
    },
    'sensors': {
        'iem1': states('sensor.eveus_ev_charger_counter_a_energy'),
        'soc_energy': states('sensor.eveus_ev_charger_soc_energy'),
        'soc_percent': states('sensor.eveus_ev_charger_soc_percent'),
        'time_to_target': states('sensor.eveus_ev_charger_time_to_target_soc'),
        'power': states('sensor.eveus_ev_charger_power')
    }
} %}

{# Calculation Preparation #}
{% set initial_soc       = debug_data.inputs.initial_soc|float(0) %}
{% set battery_capacity  = debug_data.inputs.battery_capacity|float(0) %}
{% set soc_correction    = debug_data.inputs.soc_correction|float(0) %}
{% set energy_charged    = debug_data.sensors.iem1|float(0) %}

{# Validity Check #}
{% set input_valid = (
    initial_soc != 0 and
    battery_capacity != 0 and
    energy_charged != 'unknown' and
    energy_charged != 'unavailable'
) %}

{# Calculations #}
{% if input_valid %}
  {% set initial_kwh = (initial_soc / 100) * battery_capacity %}
  {% set efficiency  = (1 - soc_correction / 100) %}
  {% set charged_kwh = energy_charged * efficiency %}
  {% set total_kwh   = initial_kwh + charged_kwh %}

  {% set soc_energy  = total_kwh|round(2) %}
  {% set soc_percent = ((total_kwh / battery_capacity) * 100)|round(0) %}
{% else %}
  {% set soc_energy  = 'Cannot calculate - missing inputs' %}
  {% set soc_percent = 'Cannot calculate - missing inputs' %}
{% endif %}

### Eveus SOC Sensor Debug Report ###

## Required Input Entities ##
{% for name, value in debug_data.inputs.items() %}
- input_number.ev_{{ name }}: {{ value }}
{% endfor %}

## Sensor Values ##
{% for name, value in debug_data.sensors.items() %}
- {{ name }}: {{ value }}
{% endfor %}

## Entity Existence Check ##
{% set check_entities = [
  'input_number.ev_initial_soc',
  'input_number.ev_battery_capacity',
  'input_number.ev_soc_correction',
  'input_number.ev_target_soc',
  'sensor.eveus_ev_charger_counter_a_energy',
  'sensor.eveus_ev_charger_power'
] %}
{% for entity_id in check_entities %}
- {{ entity_id }}: {{ states(entity_id) != 'unknown' and states(entity_id) != 'unavailable' }}
{% endfor %}

## Expected Values ##
- Expected SOC Energy: {{ soc_energy }}
- Expected SOC Percent: {{ soc_percent }}

## Calculation Details ##
{% if input_valid %}
Initial SOC: {{ initial_soc }}%
Battery Capacity: {{ battery_capacity }} kWh
Efficiency Correction: {{ soc_correction }}%
Energy Charged: {{ energy_charged }} kWh
Initial Energy: {{ initial_kwh }} kWh
Efficiency Factor: {{ efficiency }}
Charged Energy (with efficiency): {{ charged_kwh }} kWh
Total Energy: {{ total_kwh }} kWh
{% else %}
Cannot perform calculations due to missing inputs.
{% endif %}
```

### Common Issues and Solutions

| Issue | Possible Solution |
|-------|-------------------|
| SOC sensors show "unknown" | Verify all required helper entities are created with exact names |
| "Missing input entity" error | Check that all input_number entities exist and have valid values |
| Automation errors | Update entity names in automations to match integration's naming pattern (eveus_ev_charger_*) |
| Connection failures | Verify network connectivity, check IP address, username, and password |
| Incorrect SOC calculations | Set initial SOC to match your EV's current charge level |

### Reset Procedure

If you encounter persistent issues:
1. Remove the integration
2. Restart Home Assistant
3. Delete any remaining eveus entities (if any)
4. Add the integration again
5. Recreate any missing input_number entities

## Support

For bugs and feature requests, please open an issue on GitHub.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
