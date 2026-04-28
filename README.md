# Eveus EV Charger for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-4.0.0-blue)
![Stability](https://img.shields.io/badge/stability-stable-green)

Home Assistant integration for monitoring and controlling Eveus EV chargers over the local network.

## Highlights

- **Complete charger overview**: voltage, current, power, session energy, total energy, charging state, substate, temperatures, ground status, and connection quality.
- **Safe charger controls**: set charging current from Home Assistant, enable One Charge mode, use the charger-side Stop Charging option, and reset Counter A.
- **Model-aware current limits**: current control automatically follows the selected 16A, 32A, or 48A charger model.
- **Energy and cost tracking**: monitor session/lifetime energy, Counter A/B energy, Counter A/B cost, active electricity rate, and Rate 2/3 pricing.
- **Optional EV battery estimates**: add helper entities to estimate SOC in kWh, SOC percentage, and time remaining to target SOC.
- **Multi-rate billing visibility**: see the active rate and whether Rate 2 or Rate 3 schedules are enabled.
- **Connection health at a glance**: the Connection Quality sensor shows network reliability, latency, and health status so Wi-Fi issues are easier to spot.
- **Smart diagnostic sensors**: charger state, substate, ground status, temperatures, backup battery voltage, current setpoint, rate schedule status, and SOC helper status are grouped under Diagnostics.
- **Multiple charger support**: add more than one Eveus charger, each with separate devices, entities, controls, and diagnostic sensors.
- **Reliable setup and maintenance**: setup validates the charger before creating the entry, and Reconfigure lets you update IP address, credentials, or model later.

## Requirements

- Home Assistant 2024.4 or newer.
- Eveus charger reachable from Home Assistant on the local network.
- Charger IP address, username, password, and model.

## Installation

### HACS

1. Open HACS.
2. Go to **Custom repositories**.
3. Add `https://github.com/ABovsh/eveus` as an **Integration**.
4. Search for **Eveus EV Charger** and install it.
5. Restart Home Assistant.

### Manual

1. Copy `custom_components/eveus` into the Home Assistant `custom_components` directory.
2. Restart Home Assistant.

## Setup

1. Open **Settings → Devices & Services**.
2. Select **Add Integration**.
3. Search for **Eveus**.
4. Enter the charger IP address, username, password, and model.

During setup, the integration checks that the charger is reachable, authentication works, and the `/main` response looks like an Eveus charger.

To change the IP address, credentials, or model later, open **Settings → Devices & Services → Eveus → Reconfigure**.

To add another charger, run the setup flow again with the other charger IP address.

## Entities

Entity names and unique IDs are kept stable across updates.

### Main Sensors

| Entity | Description |
| --- | --- |
| Voltage | Current line voltage |
| Current | Current charging amperage |
| Power | Current charging power |
| Session Energy | Energy delivered in the current session |
| Session Time | Duration of the current session |
| Total Energy | Lifetime delivered energy |
| Counter A Energy | Energy counter A |
| Counter B Energy | Energy counter B |
| Counter A Cost | Cost for counter A |
| Counter B Cost | Cost for counter B |

### Rate Sensors

| Entity | Description |
| --- | --- |
| Primary Rate Cost | Primary electricity rate |
| Active Rate Cost | Currently active electricity rate |
| Rate 2 Cost | Rate 2 electricity price |
| Rate 3 Cost | Rate 3 electricity price |

### Optional SOC Sensors

These appear when the optional helper entities are present.

| Entity | Description |
| --- | --- |
| SOC Energy | Estimated battery energy in kWh |
| SOC Percent | Estimated battery percentage |
| Time to Target SOC | Estimated time until target SOC |

### Controls

| Entity | Description |
| --- | --- |
| Charging Current | Current limit slider |
| Stop Charging | Charger-side stop-charge option |
| One Charge | Single charge session mode |
| Reset Counter A | Reset energy counter A |

### Diagnostics

| Entity | Description |
| --- | --- |
| State | Charger state |
| Substate | Detailed charger state or error |
| Ground | Ground connection status |
| Current Set | Current limit reported by the charger |
| Box Temperature | Internal charger temperature |
| Plug Temperature | Plug temperature |
| Battery Voltage | Charger backup battery voltage |
| System Time | Charger internal time |
| Connection Quality | Network reliability percentage with latency and health attributes |
| Input Entities Status | Shows missing or invalid optional SOC helpers |
| Rate 2 Status | Rate 2 schedule status |
| Rate 3 Status | Rate 3 schedule status |

## Optional SOC Helpers

SOC tracking is optional. Without these helpers, charging control, energy, cost, and diagnostics continue to work normally.

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
- Check the IP address, username, password, and selected model.
- Make sure Home Assistant can reach the charger network.

### Controls Do Not Respond

- Check **Connection Quality**.
- Confirm the charger is online.
- Wait for the next coordinator refresh after sending a command.
- Review Home Assistant logs for `custom_components.eveus`.

### SOC Sensors Are Unavailable

- Create the optional `input_number.ev_*` helpers.
- Check **Input Entities Status** for missing or invalid helpers.

## Support

For bugs, feature requests, and release discussions, open an issue on [GitHub](https://github.com/ABovsh/eveus/issues).

## License

MIT License.
