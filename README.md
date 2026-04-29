# Eveus EV Charger - Home Assistant Integration

![Downloads](https://img.shields.io/github/downloads/ABovsh/eveus/total?color=41BDF5&logo=home-assistant&label=Downloads&suffix=%20downloads&style=for-the-badge)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-4.0.1b3-blue?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.4%2B-41BDF5?style=for-the-badge&logo=home-assistant)

Local Home Assistant integration for Eveus EV chargers. It adds charger monitoring, current control, charging mode switches, energy and cost sensors, optional EV battery estimates, diagnostics, and multi-charger support.

## What It Can Do

### Charger Monitoring

- Live voltage, current, power, session energy, session time, and lifetime energy.
- Charger state and substate decoding, including detailed error/substate labels.
- Box temperature, plug temperature, ground status, system time, and backup battery voltage.
- Connection Quality sensor with recent success rate, average latency, failure count, and health status.

### Charging Controls

- Charging Current slider with model-aware limits for 16A, 32A, and 48A chargers.
- Stop Charging switch using the charger-side stop-charge option.
- One Charge switch for single charging sessions.
- Reset Counter A control for resetting the charger energy counter.
- Optimistic UI feedback: controls update immediately, then reconcile with the charger response.

### Energy, Cost, And Rates

- Session Energy and Total Energy sensors.
- Counter A/B energy tracking.
- Counter A/B cost tracking.
- Primary Rate Cost, Active Rate Cost, Rate 2 Cost, and Rate 3 Cost.
- Rate 2 Status and Rate 3 Status diagnostics.

### Optional EV Battery Estimates

SOC helpers are optional. The charger works normally without them.

When helper entities are created, the integration can estimate:

- SOC Energy in kWh.
- SOC Percent.
- Time to Target SOC.
- Missing or invalid helper status through the Input Entities Status diagnostic sensor.

### Reliability And Maintenance

- Multiple Eveus chargers can be added to the same Home Assistant instance.
- Setup validates reachability and Eveus-compatible responses before creating the integration entry.
- Reconfigure support for updating charger IP address, credentials, or model without reinstalling.
- Reauthentication flow for credential updates if the charger rejects stored credentials.
- Home Assistant diagnostics with sensitive fields redacted.
- Repair flow for rare invalid stored setup data.
- Powered-off chargers are treated as a normal condition and stay quiet in normal Home Assistant logs.

## Requirements

| Requirement | Details |
| --- | --- |
| Home Assistant | 2024.4 or newer |
| Charger | Eveus EV charger on the same reachable network |
| Network | Charger IP address or local hostname |
| Setup fields | IP/host, username, password, and charger model |
| Supported models | 16A, 32A, 48A |

---
<img width="798" height="905" alt="image" src="https://github.com/user-attachments/assets/ccfdef56-a04e-4cad-acc7-bbb61c0879db" />
<img width="264" height="840" alt="image" src="https://github.com/user-attachments/assets/85c35fc9-b867-440c-9d46-95b74839beb9" />


---

## Installation

### HACS

1. Open HACS.
2. Open **Custom repositories**.
3. Add `https://github.com/ABovsh/eveus` as an **Integration**.
4. Search for **Eveus EV Charger**.
5. Install and restart Home Assistant.

### Manual

1. Copy `custom_components/eveus` into the Home Assistant `custom_components` directory.
2. Restart Home Assistant.

## Setup

1. Open **Settings → Devices & Services**.
2. Select **Add Integration**.
3. Search for **Eveus EV Charger**.
4. Enter the charger IP address or hostname.
5. Enter the charger username and password.
6. Select the charger model: 16A, 32A, or 48A.

To change connection details later, open **Settings → Devices & Services → Eveus EV Charger → Reconfigure**.

To add another charger, run the same setup flow again with a different charger IP address or hostname.

## Created Entities

Entity names and unique IDs are kept stable across updates so existing dashboards and automations continue working.

### Sensors

| Entity | Description |
| --- | --- |
| Voltage | Current line voltage |
| Current | Current charging amperage |
| Power | Current charging power |
| Current Set | Current limit reported by the charger |
| Session Energy | Energy delivered in the current session |
| Session Time | Duration of the current session |
| Total Energy | Lifetime delivered energy |
| Counter A Energy | Energy counter A |
| Counter B Energy | Energy counter B |
| Counter A Cost | Cost for counter A |
| Counter B Cost | Cost for counter B |
| Primary Rate Cost | Primary electricity rate |
| Active Rate Cost | Currently active electricity rate |
| Rate 2 Cost | Rate 2 electricity price |
| Rate 3 Cost | Rate 3 electricity price |

### Optional SOC Sensors

These sensors require the optional helper entities listed below.

| Entity | Description |
| --- | --- |
| SOC Energy | Estimated battery energy in kWh |
| SOC Percent | Estimated battery percentage |
| Time to Target SOC | Estimated time until the configured target SOC |

### Controls

| Entity | Type | Description |
| --- | --- | --- |
| Charging Current | Number | Current limit slider |
| Stop Charging | Switch | Charger-side stop-charge option |
| One Charge | Switch | Single charge session mode |
| Reset Counter A | Switch | Reset energy counter A |

### Diagnostics

| Entity | Description |
| --- | --- |
| State | Main charger state |
| Substate | Detailed charger state or error |
| Ground | Ground connection status |
| Box Temperature | Internal charger temperature |
| Plug Temperature | Plug temperature |
| Battery Voltage | Charger backup battery voltage |
| System Time | Charger internal time |
| Connection Quality | Recent network reliability, latency, and health attributes |
| Input Entities Status | Missing or invalid optional SOC helper status |
| Rate 2 Status | Rate 2 schedule status |
| Rate 3 Status | Rate 3 schedule status |

## Optional SOC Helpers

SOC tracking is optional. Without these helpers, charging controls, energy sensors, cost sensors, rate sensors, and diagnostics continue to work normally.

Create these helpers in **Settings → Devices & Services → Helpers → Create Helper → Number**.

| Helper entity ID | Name | Unit | Min | Max | Step | Initial | Purpose |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| `input_number.ev_battery_capacity` | EV Battery Capacity | kWh | 10 | 160 | 1 | 80 | Battery size used for SOC energy and time-to-target calculations |
| `input_number.ev_initial_soc` | Initial EV State of Charge | % | 0 | 100 | 1 | 20 | Battery percentage when the charging session starts |
| `input_number.ev_soc_correction` | Charging Efficiency Loss | % | 0 | 15 | 0.1 | 7.5 | Charging loss correction applied to delivered energy |
| `input_number.ev_target_soc` | Target SOC | % | 0 | 100 | 5 | 80 | Desired battery percentage for the time-to-target sensor |

The **Input Entities Status** diagnostic sensor shows which helpers are missing or invalid.

## Troubleshooting

### Setup Cannot Connect

- Confirm the charger is powered on and connected to Wi-Fi.
- Open `http://<charger-ip>` from a browser on the same network.
- Check the IP address or hostname.
- Make sure Home Assistant can reach the charger network.
- Confirm the selected charger model matches the real charger capability.

### SOC Sensors Are Unavailable

- Create the optional `input_number.ev_*` helpers.
- Check **Input Entities Status** for missing or invalid helpers.
- Confirm helper values are numeric and inside the expected range.

## Compatibility Notes

- Existing entity names and unique IDs are preserved across the 4.x releases.
- Dashboard cards and automations created for older versions should continue working after update.
- SOC helpers remain optional.

## Support

For bugs, feature requests, and release discussions, open an issue on [GitHub](https://github.com/ABovsh/eveus/issues).

## License

MIT License.
