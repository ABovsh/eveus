# Eveus EV Charger - Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
![Version](https://img.shields.io/badge/version-4.5.0-blue?style=for-the-badge)
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
- **Session Cost** — running ₴-value of the current charging session, derived from session energy × active rate. Use it directly in notifications and Lovelace cards instead of writing a template sensor.
- Primary Rate Cost, Active Rate Cost, Rate 2 Cost, and Rate 3 Cost.
- Rate 2 Status and Rate 3 Status diagnostics.

### Automation Helpers (new in 4.5.0)

These entities exist to replace the template sensors users typically build on top of Eveus.

- **`binary_sensor.eveus_car_connected`** — `device_class: plug`, true whenever a vehicle is electrically connected (charger device-state in {Connected, Charging, Charge Complete, Paused}). Stable across charger firmware label changes — uses canonical state values, not localized strings.
- **`sensor.eveus_charging_finish_time`** — `device_class: timestamp`, the absolute UTC time when charging is expected to reach the configured target SOC. Companion to `Time to Target SOC` (which is a human-readable string for cards). The timestamp variant is what automations and `device_class: timestamp` cards consume directly (e.g. "notify me 30 min before charge finishes"). Returns unavailable when not charging, helpers missing, or target already reached. Minute-aligned to avoid state jitter every poll.

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
- Entity updates are coordinator-driven and optimized to avoid unnecessary state writes when values have not changed.

## Requirements

| Requirement | Details |
| --- | --- |
| Home Assistant | 2024.4 or newer |
| Charger | Eveus EV charger on the same reachable network |
| Network | Charger IP address or local hostname, optionally with `http://` or `https://` and a port |
| Setup fields | IP/host or URL, username, password, and charger model |
| Supported models | 16A, 32A, 48A |

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
4. Enter the charger IP address, hostname, or URL. Use `https://` when the charger is configured for HTTPS.
5. Enter the charger username and password.
6. Select the charger model: 16A, 32A, or 48A.

To change connection details later, open **Settings → Devices & Services → Eveus EV Charger → Reconfigure**.

To add another charger, run the same setup flow again with a different charger IP address or hostname.

> [!NOTE]
> Some Eveus firmware versions may return status data from `/main` even when incorrect credentials are supplied. The integration rejects credentials when the charger returns `401`, but it cannot prove credentials are wrong if the charger still returns valid Eveus data. Commands are still sent with the stored credentials.

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
| Session Cost | Cost of the current charging session in ₴ (sessionEnergy × active rate) |

### Binary Sensors

| Entity | Device class | Description |
| --- | --- | --- |
| Car Connected | `plug` | `on` when a vehicle is electrically connected (Connected, Charging, Charge Complete, or Paused); stable across firmware label changes |

### Optional SOC Sensors

These sensors require the optional helper entities listed below.

| Entity | Description |
| --- | --- |
| SOC Energy | Estimated battery energy in kWh |
| SOC Percent | Estimated battery percentage |
| Time to Target SOC | Human-readable time until the configured target SOC (e.g. "2h 15m") |
| Charging Finish Time | `device_class: timestamp` — absolute UTC time when target SOC is reached |

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
| `input_number.ev_initial_soc` | Initial EV State of Charge | % | 0 | 100 | 1 | 20 | Battery percentage at the point where SOC tracking should start |
| `input_number.ev_soc_correction` | Charging Efficiency Loss | % | 0 | 15 | 0.1 | 7.5 | Charging loss correction applied to delivered energy |
| `input_number.ev_target_soc` | Target SOC | % | 0 | 100 | 5 | 80 | Desired battery percentage for the time-to-target sensor |

The **Input Entities Status** diagnostic sensor shows which helpers are missing or invalid.

### How SOC Baselines Work

The SOC helper sensors do not treat the raw Counter A / `IEM1` value as energy added to the battery. Counter A may already contain previous charging history, especially if it is only reset by an automation when the house changes mode.

Instead, the integration captures the current Counter A / `IEM1` value as a baseline and calculates SOC from the difference:

```text
charged energy for SOC = current IEM1 - baseline IEM1
```

The baseline is created the first time a valid Counter A / `IEM1` value is seen after the helpers are available.

The baseline resets when:

- `input_number.ev_initial_soc` changes.
- Counter A / `IEM1` becomes lower than the captured baseline, which means the charger counter was reset.

The baseline does **not** reset just because charging stops and starts again. This means split charging works as expected when you set Initial SOC once, charge in several separate sessions, and do not reset Counter A between them.

Example:

```text
Battery capacity: 80 kWh
Initial SOC: 20%
Charging loss: 10%
Counter A / IEM1 when Initial SOC is set: 100 kWh
```

After the first charging session:

```text
IEM1 = 110 kWh
SOC delta = 110 - 100 = 10 kWh
Usable energy = 10 * 0.90 = 9 kWh
Estimated SOC = 31%
```

After a second separate charging session without changing Initial SOC or resetting Counter A:

```text
IEM1 = 116 kWh
SOC delta = 116 - 100 = 16 kWh
Usable energy = 16 * 0.90 = 14.4 kWh
Estimated SOC = 38%
```

If you correct Initial SOC during charging, the integration treats the new Initial SOC as the new truth from that moment and starts a fresh SOC baseline at the current Counter A / `IEM1` value.

## Troubleshooting

### Setup Cannot Connect

- Confirm the charger is powered on and connected to Wi-Fi.
- Open `http://<charger-ip>` from a browser on the same network.
- Check the IP address or hostname.
- Make sure Home Assistant can reach the charger network.
- Confirm the selected charger model matches the real charger capability.

### Any Username And Password Work

Some Eveus firmware versions appear to allow status reads from `/main` without enforcing Basic Auth. If setup succeeds with random credentials, test the charger directly:

```bash
curl -i -X POST http://CHARGER_IP/main
curl -i -X POST -u wrong:wrong http://CHARGER_IP/main
curl -i -X POST -u real_login:real_password http://CHARGER_IP/main
```

If the first or second command returns `200` with Eveus JSON, the charger is accepting status reads without valid credentials. This is charger behavior, not Home Assistant credential caching.

### Controls Do Not Respond

- Check **Connection Quality**.
- Confirm the charger is online.
- Verify the stored credentials in **Reconfigure**.
- Wait for the next coordinator refresh after sending a command.

### SOC Sensors Are Unavailable

- Create the optional `input_number.ev_*` helpers.
- Check **Input Entities Status** for missing or invalid helpers.
- Confirm helper values are numeric and inside the expected range.

### Charger Is Powered Off

Powered-off chargers are expected. The integration backs off polling and keeps normal Home Assistant logs quiet.

## Compatibility Notes

- Existing entity names and unique IDs are preserved across the 4.x releases.
- Dashboard cards and automations created for older versions should continue working after update.
- The Stop Charging switch keeps the charger-side semantics used by previous releases.
- Optional SOC helpers remain optional.

## Support

For bugs, feature requests, and release discussions, open an issue on [GitHub](https://github.com/ABovsh/eveus/issues).

## License

MIT License.
