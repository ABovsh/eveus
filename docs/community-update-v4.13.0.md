## Eveus EV Charger 4.13.0: cost forecasts, clock-drift warning, and faster reactions

Eveus EV Charger `v4.13.0` is available. The integration provides local control
and monitoring for Eveus chargers in Home Assistant and can be installed through
HACS as a custom repository.

New documentation is now available in English and Ukrainian:

- English: <https://abovsh.github.io/eveus/>
- Українська: <https://abovsh.github.io/eveus/uk/>

### What's new in 4.13.0

- **Energy to Target SOC** and **Cost to Target SOC** forecast how much grid
  energy and money remain before the EV reaches its target charge.
- A new **charger clock-drift Repairs notice** warns when schedules and tariff
  windows may be mistimed, then guides you through checking Time Zone and using
  Sync Time.
- Charging started by a schedule, the charger UI, or OCPP appears in Home
  Assistant faster.
- A charger that is powered back on is detected within a minute.
- Credential rejection now starts reauthentication instead of making the
  charger look offline.
- Battery, OCPP, diagnostics, privacy, and system-clock handling received
  additional hardening.

There are no removed or renamed entities in this release.

The integration also includes local charging controls, schedules, live
electrical telemetry, energy and cost counters, adaptive charging, SOC/ETA
estimates, OCPP control, English and Ukrainian UI, and Home Assistant Repairs
notices for grounding, overheating, leakage current, charger faults, and backup
battery condition.

### Install through HACS

Use the direct HACS link:

<https://my.home-assistant.io/redirect/hacs_repository/?owner=ABovsh&repository=eveus&category=integration>

Or add this as a custom Integration repository:

`https://github.com/ABovsh/eveus`

Release notes: <https://github.com/ABovsh/eveus/releases/tag/v4.13.0>

Issues and feature requests: <https://github.com/ABovsh/eveus/issues>
