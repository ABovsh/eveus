# Changelog

## 4.12.0 - 2026-06-07

### ✨ Added
- **Safety problems now surface in Home Assistant Repairs.** Eveus warns about a missing ground, disabled ground protection, current leakage, box/plug overheating, and relay, pilot, diode, overcurrent, low/high voltage, GFCI-test, interface, and software faults. Faults the charger reports itself alert immediately; the raw grounding, temperature, and leakage checks require several consecutive readings and apply recovery hysteresis, so a single glitchy poll cannot raise a false alarm. The raw temperature check now warns at 80 °C, before the charger stops charging at 85 °C, so you get an early heads-up. Each notice carries a clear, plain-language repair message in English and Ukrainian explaining what happened and what to do — and now points you to the charger manufacturer's support for hardware faults.
- **Ground protection can now be controlled from Home Assistant.** The new opt-in **Ground Protection** switch (`switch.eveus_ev_charger_ground_protection`) mirrors the charger's `groundCtrl` setting. When it is on, the charger checks for a protective earth connection and blocks charging if ground is missing; when it is off, the charger only reports ground status and lets charging continue without a detected ground. It is disabled by default in the entity registry because turning it off bypasses a safety protection. Missing ground and disabled protection remain two independent Repairs notices.
- **Both dashboards now include the Ground Protection control.** The English (`docs/dashboard.yaml`) and Ukrainian (`docs/dashboard-uk.yaml`) Lovelace views add the new switch alongside the existing charging controls.
- **Safety notices match the seriousness of the condition.** Grounding and other recoverable notices clear automatically after confirmed recovery; serious incidents stay visible until you press **Ignore**, then reset after recovery so a future separate incident can alert again. The integration monitors and reports; the only safety setting it can change is the Ground Protection switch you operate yourself, and it does not replace the charger's built-in protection or a qualified electrician.

### 🐛 Fixed
- **A repeat safety condition always warns you again.** If a serious safety notice (such as box or plug overheat) had already cleared by the time you pressed **Ignore**, a later recurrence now raises a fresh notice instead of staying hidden under the earlier acknowledgement — including when the temperature briefly hovers in the recovery band in between.

## 4.11.0 - 2026-06-05

### ✨ Added
- **Low charger battery warning.** Home Assistant now raises a repair notice when the charger's internal CR2032 coin-cell battery runs low, so you can replace it in time. While the battery is low some charger functions may be limited; the notice clears itself once a fresh battery is fitted.

### 🐛 Fixed
- **Controls recover cleanly when the system clock jumps backward** (NTP correction, VM resume, manual change): a control no longer stays stuck on its last set value, and the charger no longer stays *unavailable* until the clock catches up.
- **A control now reflects the charger dropping offline mid-command** instead of briefly showing a stale *available* state.
- **Force Refresh reliably bypasses the offline back-off** even if a routine poll lands at the same moment.
- **Time Zone keeps your selection during a brief outage** and restores the last shown value after a restart, instead of snapping back to the old zone or going *unknown*.
- **More corrupt readings read as `unknown` instead of plausible-but-wrong values:** a fractional Charging Current setpoint, an Adaptive Current Limit or schedule current limit above your charger model's maximum, and an absurd Session Time.
- **The charging-time estimate can no longer freeze on a stale value** caused by a corrupt power reading.
- **A malformed firmware field can no longer appear as a bogus device firmware version** on the Devices page.

### 🔒 Privacy
- **Upgrading an old entry whose stored address contained embedded credentials** now strips those credentials from both the stored address and the integration title.

### 📊 Dashboard
- **The English dashboard (`docs/dashboard.yaml`) was reworked to use screen space more effectively**, and a matching **Ukrainian dashboard (`docs/dashboard-uk.yaml`) was created from it** with the identical layout and added alongside.

## 4.10.1 - 2026-06-05

### 🐛 Fixed
- **Controls feel snappier and always show your latest value.** Dragging the **Charging Current** slider or quickly re-tapping a switch no longer briefly flashes an older value before settling on the one you chose, and toggling controls in quick succession (for example **Stop Charging** then **One Charge**) no longer lets a slow reply from the previous action overwrite your newest one.
- **Controls stay responsive across clock changes and reloads.** A backward system-clock correction can no longer momentarily stall the controls, and reloading or removing the integration right after a command no longer leaves stray background refreshes running.
- **Time Zone no longer snaps back.** Changing the offset keeps your choice while the charger applies it, instead of briefly reverting to the old zone on the next poll.
- **Switching Integration mode from Advanced to Basic tidies up after itself.** The SOC entities it no longer provides (`SOC %`, `SOC Energy`, the charging-time estimates, and the four SOC inputs) are removed instead of left behind as permanently *unavailable*. The same applies to the Phase 2/3 voltage and current sensors when you switch from 3-phase to single-phase.
- **That cleanup now waits until the charger has fully reloaded.** If the charger is briefly offline during a Basic/single-phase switch, your old entities — and their area, name, and dashboard placement — are kept and removed on the next successful load, rather than deleted on a setup attempt that Home Assistant then retries.
- **The "update your SOC dashboard" reminder clears itself.** It disappears automatically once you remove the old `input_number.ev_*` helpers or return to Basic mode, instead of lingering indefinitely.
- **One bad reading can't poison your statistics.** The energy and cost sensors (`Total Energy`, `Counter A/B Energy`, `Counter A/B Cost`, `Session Energy`, `Session Cost`), the `Box Temperature`, `Plug Temperature`, `Battery Voltage`, `Leakage Current`/`Peak` and per-kWh tariff cost sensors, and the SOC sensors (`SOC %`, `SOC Energy`, `Time to Target SOC`, `Charging Finish Time`) now ignore corrupt, impossible readings — so a single glitch can't poison long-term history or fake a full battery, a "target reached", or a "< 1 min" estimate.
- **Running costs recover after a restart.** `Counter A/B Cost` and `Session Cost` recover cleanly from a corrupt stored value, so the next real meter reset is detected correctly and statistics keep accumulating without phantom resets.
- **`Session Time` reads `unknown` instead of a misleading `0m`** when the charger reports an impossible negative duration.
- **Clearer setup messages.** Adding a charger that's already set up — or pointing one at an address another charger already uses — now shows a clear **"already configured"** message instead of a confusing **"unknown error"**.
- **Charger addresses are validated up front.** An address pasted with hidden/invalid characters is rejected rather than silently altered into a different target; an unbalanced IPv6 bracket (for example `[2001:db8::1`) gives a clear invalid-address message; and a stored address using an uppercase scheme with a trailing path (for example `HTTP://…/main`) upgrades itself cleanly without a manual repair.
- **Reauthentication works even with an invalid stored mode.** Re-entering your password succeeds even if the stored integration-mode setting had somehow become invalid.
- **A corrupt charger no longer affects your others.** A charger with corrupted stored settings no longer blocks your other chargers from loading or leaves its **Reconfigure** form unable to open — corrupted credentials raise a fixable repair notice instead of retrying forever. And if a repaired charger fails to reload, the repair notice now stays put so you can try again, instead of silently vanishing.

### 🔒 Privacy
- **Failed-setup errors omit the charger address.** If setup fails unexpectedly, the error shown in Home Assistant no longer includes the charger's address.

## 4.10.0 - 2026-06-02

### ✨ Ukrainian localization
- The integration is now fully available in Ukrainian — setup, the mode options, the migration notice, the OCPP warning, and **every entity name** (sensors, controls, buttons, schedules). It appears automatically when Home Assistant runs in Ukrainian; entity IDs are unchanged.

### ✨ Automatic SOC inputs — no more manual helpers
- You no longer create `input_number.ev_*` helpers by hand for battery tracking. The integration now creates the four SOC inputs for you as native entities: `number.eveus_ev_charger_initial_soc`, `number.eveus_ev_charger_target_soc`, `number.eveus_ev_charger_battery_capacity`, `number.eveus_ev_charger_soc_correction`.
- A new **Integration mode** option chooses what the integration sets up:
  - **Basic** — full charger monitoring and control: voltage, current, power, energy, temperatures, costs, schedules, OCPP, and every switch and button.
  - **Advanced** — everything in Basic, **plus** EV battery tracking: SOC %, SOC Energy, Time to Target SOC, Charging Finish Time, and the four SOC inputs above.
- Switch between Basic and Advanced anytime from **Configure**.
- **Upgrading:** existing SOC users are moved to Advanced automatically, with Battery Capacity and SOC Correction carried over — nothing to re-enter.
- **If you used the old helpers:** update your dashboards, automations, scripts, and templates to the new entities by replacing the prefix `input_number.ev_` with `number.eveus_ev_charger_`. Once everything points at the new entities, you can delete the old `input_number.ev_*` helpers.
- The old **Input Entities Status** diagnostic sensor has been removed — it only existed to remind you to create those helpers, which the integration now handles.

### ✨ OCPP control
- New **Connect to OCPP** switch (`switch.eveus_ev_charger_connect_to_ocpp`) links the charger to the OCPP backend (used by the Eveus mobile app) straight from Home Assistant. Turn it off to return the charger to full local control. A companion `binary_sensor.eveus_ev_charger_ocpp_connected` reflects the charger's reported OCPP connection state.
- While OCPP is connected, a Home Assistant Repairs notice explains that Charging Current, charge limits, and the charging schedule may be overridden by the OCPP backend, and walks you through turning it off again. It clears automatically once OCPP is disabled — including when toggled from the mobile app.

### 🔧 Changed
- **Minimum Home Assistant version is now 2025.1.**
- SOC %/kWh sensors are available as soon as the charger is online.

### 🐛 Fixed
- SOC inputs (Initial SOC, Target SOC, Battery Capacity, SOC Correction) now reject an out-of-range or non-numeric value instead of silently snapping it to the nearest limit.
- During polling, a reading with a missing or non-finite current setpoint is rejected, so a misrouted host can no longer briefly come online showing a garbage `Charging Current`.
- An insecure (`http://`) connection warning now appears before the integration connects, on every setup, Reconfigure, and repair attempt. Bracketed IPv6 addresses are accepted as the charger host.

## 4.9.2 - 2026-05-29

Reliability, statistics-correctness, and privacy improvements, plus two new automation-friendly entities. No entity renames or removals versus 4.9.1; existing setups upgrade transparently.

### ✨ New

- **`binary_sensor.eveus_session_active`** (`device_class: running`) — turns on whenever a charging session is in progress, including brief mid-session pauses. Use it as a single automation trigger instead of templating on the `State` string.
- **`sensor.eveus_wifi_signal`** — WiFi signal strength (dBm) reported by the charger, so you can correlate polling problems with weak RF.

### 🔧 Changed

- **Cost sensors use Home Assistant's monetary handling.** `Counter A Cost`, `Counter B Cost`, and `Session Cost` now report with the `monetary` device class and the ISO unit `UAH`, so long-term cost statistics keep accumulating correctly across session and counter resets, and the Energy dashboard can pick them up as costs. Per-kWh tariffs continue to display `₴/kWh`. Upgrading may show a one-time "units changed" notice on the cost sensors.
- **`SOC Energy` and `Session Energy` use a valid sensor type.** `sensor.eveus_soc_energy` is now reported as stored battery energy and `sensor.eveus_session_energy` as a plain energy measurement, which clears an "impossible state class" warning at startup. Their history is unaffected.
- **Richer Device page.** The Devices view now shows the charger's real model, manufacturer, hardware version, and serial number from the firmware.
- **Fuller, safer diagnostics.** `Download diagnostics` now ships the full sanitized `/main` snapshot plus connection state. Identifying fields — serial number, station ID, LAN IP, firmware CRC, and anything whose name looks like an address, SSID, MAC, or token — are redacted automatically, so a future firmware field can't leak into a shared report.

### 🐛 Fixed

- **Corrupt or impossible readings show `unknown` instead of plausible-but-wrong values.** This now covers `Voltage`, `Current`, `Power`, the per-phase sensors, leakage current, battery voltage, WiFi signal, the tariff/rate sensors, switch and schedule states, and the charger clock. `Current Set` is additionally bounded by your charger model's maximum, so a setpoint that's impossible for your unit (for example 40 A on a 16 A charger) reads as `unknown`.
- **Misrouted or non-Eveus responses are refused.** A captive portal, proxy, or a partial payload missing the current setpoint is now rejected both when adding a charger and during polling, instead of briefly coming online with blank or garbage values.
- **SOC math is more faithful to your settings.** A `0%` `Charging Efficiency Loss` setting is now respected instead of being replaced with the default, and a corrupt (negative) session-energy reading makes the SOC sensors go `unknown` rather than faking your configured starting charge level.
- **Restored controls survive a restart while the charger is offline.** After a restart, `Charging Current`, the schedule time pickers, and the switches keep their restored value through the normal grace window instead of dropping straight to `unknown`/`off`.
- **`Input Entities Status` is more responsive and more available.** It updates immediately when you add or remove an optional SOC helper, and it stays available for troubleshooting even while the charger is unreachable.
- **Gentler, smarter connection handling.** Recovery after a long outage no longer snaps straight back to fast polling on a single fluke packet; transient `5xx`/`429`/network errors back off properly; and permanent command errors (`400`/`403`/`404`) fail fast instead of burning the retry budget, while a `401` starts reauthentication.
- **A paused-but-active session keeps refreshing on the fast cadence.** `binary_sensor.eveus_session_active`, the SOC sensors, and the charging-time estimates no longer slow to the idle cadence when a session pauses mid-charge.
- **`Time to Target SOC` asks for the one thing it needs.** It shows `Set Target SOC` when the core SOC helpers are set but `Target SOC` is not, and `Helpers Required` only when no helpers are configured — instead of leaving a stale ETA on screen.
- **`Connection Quality` no longer shows a false healthy state.** It reads `unknown` before the first successful poll and on internal error, rather than a misleading `100%`.
- **Corrupt stored configuration is caught at startup.** A malformed host, an unexpected transport scheme, or an invalid phase count now raises a clear repair notice or self-corrects, and adding two chargers at once can no longer assign them the same internal device number.

### 🔒 Privacy

- **Stored password is never echoed back.** Reauthentication and reconfigure no longer prefill or round-trip the saved password to the browser form.
- **Command-failure logs omit the charger address.** A failed control command now records only the error type, matching how polling failures are already logged.
- **Cleartext-credentials warning fires more often.** The plain-HTTP warning now also appears during reconfigure and reauthentication, not only on initial setup.

## 4.9.1 - 2026-05-22

### 🐛 Fixed
- **Time Zone**: picking a new offset no longer snaps back to the previous value before the next poll. `select.eveus_time_zone` now keeps the chosen value while the charger confirms it, and reverts immediately if the charger rejects the command.
- **Session Cost**: long-term statistics no longer treat the end-of-session reset as a meter rollback. The sensor is reported as a per-session measurement again.
- **Car Connected**: the binary sensor now reports `unknown` while the charger is in an error state instead of falsely reporting the vehicle as unplugged.
- **State of Charge**: SOC calculations now reject invalid inputs such as negative energy, invalid battery capacity, impossible SOC values, and impossible efficiency-loss values.
- **Measurements**: voltage, current, power, leakage current, per-phase readings, session energy, current setpoint, and connection quality now reject negative, non-finite, or boolean payload glitches instead of showing misleading values.
- **Setup and repair**: malformed URLs, invalid ports, embedded credentials, invalid phase values, and duplicate repaired hosts are rejected earlier and with clearer Home Assistant errors.
- **Commands**: if the charger rejects a control command with `401 Unauthorized`, Home Assistant now starts reauthentication instead of silently retrying with stale credentials.
- **Shutdown / restart**: cancelled refreshes now stop cleanly, so the integration reconnects normally on the next Home Assistant start.

### 🔒 Privacy
- Connection failures, migration messages, diagnostics titles, and setup errors no longer echo the configured charger host/IP.
- The integration avoids keeping the charger username and password as readable coordinator attributes.

### 🔧 Changed
- **Cost sensors** (`counter_a_cost`, `counter_b_cost`, `session_cost`) display the compact `₴` symbol again instead of the `UAH` code.
- **Connection Quality** attributes are exposed as chart-friendly numeric values instead of formatted strings.
- Integration brand icons were refreshed.

## 4.9.0 - 2026-05-17

Highlights:

### ✨ Schedules — writable from HA
- **`switch.eveus_schedule_1_enabled`** / **`switch.eveus_schedule_2_enabled`** — arm or disarm each on-device schedule slot.
- **`time.eveus_schedule_1_start` / `_stop`** and **`time.eveus_schedule_2_start` / `_stop`** — native HA time pickers for each slot's window.

### ✨ Adaptive mode — on/off control
- **`switch.eveus_adaptive_mode`** — toggle the charger's adaptive (voltage-sag throttle) feature.

### ✨ Clock & time zone
- **`select.eveus_time_zone`** — readable / writable time-zone offset, range `-12..+14`.
- **`button.eveus_sync_time`** — push HA's current time to the charger's clock.

### ✨ 3-phase support
- New `Phases` field (1 or 3) in setup and reconfigure. When `Phases = 3`, four extra sensors are exposed: Current Phase 2/3 and Voltage Phase 2/3. Existing 1-phase setups migrate transparently.

### ✨ Leakage current sensors
- **`sensor.eveus_leakage_current`** (mA) — live RCD reading.
- **`sensor.eveus_leakage_current_peak`** (mA) — peak-hold leakage value.

### ✨ Quick refresh
- **`button.eveus_force_refresh`** — trigger an immediate coordinator poll.

### 📊 Dashboard
- New `docs/dashboard.yaml` — drop-in Lovelace view exposing every Eveus capability, organized into logical sections (EV Battery, Now, Charging Controls, Session, Last 24 h, Adaptive, Schedule 1/2, Clock & Time Zone, Counters, Tariffs, Diagnostics). See README for install steps.

### 🐛 Fixes
- Starting / stopping a charging session now reflects within ~10–20 s instead of waiting up to a minute for the next idle poll.
- Connection Quality reports `unknown` on internal error instead of falsely showing `100%`.
- Diagnostics endpoint no longer raises when called before setup completes.
- Reconfigure / reauth / repair flows preserve `device_number` in multi-charger setups.

### 🔒 Security & UX
- Config-flow and reauth password fields are masked.
- Diagnostic dumps redact `host` and `unique_id` in addition to credentials.
- A warning is logged when the charger is configured over plain HTTP.

### ⚠️ Breaking
- `switch.eveus_reset_counter_a` removed in favor of `button.eveus_reset_counter_a` (proper one-shot reset). `button.eveus_reset_counter_b` mirrors it.

## 4.8.0 - 2026-05-16

### ⚠️ Breaking — entity platform change
- `switch.eveus_reset_counter_a` is removed and replaced by `button.eveus_reset_counter_a`, which models the one-shot reset action correctly. The unique-id is preserved, but the entity domain moves from `switch.` to `button.`. Update any dashboards or automations that referenced `switch.eveus_reset_counter_a`.

### ✨ New
- `button.eveus_reset_counter_b` — momentary reset for the second user-resettable energy counter. Mirrors Counter A.

### 🐛 Correctness fixes
- Reconfigure, reauth, and the "invalid_config" repair flow now preserve `device_number` when updating entry data. Previously a reconfigure on a multi-charger setup could reassign the number on reload, breaking entity unique-id stability.
- `Connection Quality` now reports `unknown` on internal error instead of falsely showing `100%` (Excellent).
- Diagnostics no longer raises when called before setup completes — it returns a partial payload instead.

### 🔒 Security
- Config-flow and reauth password fields are now rendered as masked password inputs instead of plain text.
- Diagnostic dumps now redact `host` and `unique_id` (in addition to credentials), so shared dumps no longer leak LAN topology.
- A warning is logged when the charger is configured over plain HTTP, calling out that Basic Auth credentials are sent in cleartext on every poll.

### 🏎 Performance
- Control entities (`Stop Charging`, `One Charge`, `Charging Current`) only update Home Assistant when their visible value or availability actually changes. Coordinator ticks no longer generate redundant state-change events for unchanged controls.

## 4.7.2 - 2026-05-16

Bugfix: `sensor.eveus_soc_energy` / `sensor.eveus_soc_percent` could still show `unknown` after 4.7.1 when `input_number.ev_target_soc` was missing or out-of-range — typical for the first few seconds after a HA reboot, before the input_number platform finishes loading.

- Fix: SOC %/kWh sensors no longer go `unknown` when the `input_number.ev_target_soc` helper is missing or out of range. Target SOC is only required by the ETA-class sensors (Time to Target SOC, Charging Finish Time); SOC %/kWh need only Initial SOC, Battery Capacity, and SOC Correction.

## 4.7.1 - 2026-05-16

Bugfix release covering two regressions surfaced after 4.6.0/4.7.0.

- Fix: `binary_sensor.eveus_car_connected` got stuck on the value from the very first fetch and never reflected later plug-in / plug-out transitions. Now correctly tracks every state change.
- Fix: `sensor.eveus_soc_energy` and `sensor.eveus_soc_percent` no longer go `unknown` when `sessionEnergy` is briefly missing from the payload (cold start before first poll, transient offline blip). A missing value is now treated as `0 kWh delivered`, so SOC reprojects from `input_number.ev_initial_soc` instead of going unknown.

## 4.7.0 - 2026-05-16

Minor release: five new diagnostic sensors expose the charger's adaptive (AI) mode and scheduled-charging slots.

- New diagnostic sensor `sensor.eveus_adaptive_charging` — `Active` / `Idle`. Indicates whether the charger is currently throttling current to maintain voltage under heavy load.
- New diagnostic sensor `sensor.eveus_adaptive_current_limit` — current cap (A) chosen by the adaptive throttle.
- New diagnostic sensor `sensor.eveus_adaptive_voltage_threshold` — voltage floor (V) that triggers throttling.
- New diagnostic sensors `sensor.eveus_schedule_1`, `sensor.eveus_schedule_2` — `Enabled` / `Disabled` with attributes `window` (HH:MM–HH:MM), `start`, `stop`, and optional `current_limit_a` / `energy_limit_kwh`.

## 4.6.0 - 2026-05-16

Minor release: source-of-truth fields from the charger now back SOC and Session Cost. The integration carried two synthetic accumulators (SOC baseline + session-cost integration) that the charger itself was already computing internally — they are gone.

- Change: `sensor.eveus_soc_energy` and `sensor.eveus_soc_percent` now use the charger-native `sessionEnergy` field instead of the per-sensor `IEM1` baseline. Removed `CachedSOCCalculator._energy_baseline` / `_baseline_initial_soc` / `restore_baseline()` and the `RestoreEntity` plumbing on `EVSocKwhSensor`. ~150 lines of state machinery deleted; the 4.5.1 baseline-survives-restart behavior is now structural — there is no baseline to lose
- Change: `sensor.eveus_session_cost` now reads `sessionMoney` directly. The charger integrates Δenergy × rate-at-the-time itself, so the stateful `SessionCostSensor` introduced in 4.5.2 is gone. Re-pricing on tariff change remains impossible by construction
- Behavior change for split charging: the charger starts a fresh session count on every plug-in, so SOC reprojects from `input_number.ev_initial_soc` after a plug-out/in cycle. If you split-charge across cycles, update Initial SOC (manually or via automation on `Car Connected`) before charging resumes. Continuous sessions and mid-session Initial SOC corrections work as before
- Removed state attributes `energy_baseline_kwh`, `baseline_initial_soc`, `accumulated_cost`, `last_session_energy` from the SOC and session-cost sensors. Any automation reading them must switch to the entity native value
- Entity IDs, units, and device classes are unchanged

## 4.5.2 - 2026-05-16

Patch release: Session Cost no longer retroactively re-prices the session when the tariff changes.

- Fix: `sensor.eveus_session_cost` was computed as `sessionEnergy × current_active_rate`, so when the active tariff switched mid-session (e.g. night→day at 07:00) the whole accumulated cost was instantly recalculated at the new rate. The sensor is now stateful: it integrates Δenergy × rate-at-the-time on each coordinator update and persists the running total via state attributes (`accumulated_cost`, `last_session_energy`) so it survives HA restarts. A new session is detected when `sessionEnergy` drops below its previous value
- Unchanged: the entity ID, unit (₴), and rounding remain the same; only the underlying math changed

## 4.5.1 - 2026-05-16

Patch release: SOC baseline survives HA restarts.

- Fix: The energy baseline used by `EVSocKwhSensor` / `EVSocPercentSensor` lived only in RAM, so restarting Home Assistant mid-session snapped SoC back to `initial_soc` (next IEM1 read became the new baseline → delivered energy = 0). The baseline now lives on the shared `CachedSOCCalculator` and is persisted as state attributes on `sensor.eveus_soc_energy` (`energy_baseline_kwh`, `baseline_initial_soc`); on restart it is restored via `RestoreEntity` before the first coordinator update, so SoC continues from where it was
- Refactor: Moved baseline state and the `_get_energy_charged` logic from `BaseEVHelperSensor` (per-sensor instance) to `CachedSOCCalculator` (shared per device) — every helper sensor on the device now agrees on the same baseline by construction
- Preserves existing helper-blip behavior: a transient `initial_soc=None` still does not reset the baseline; counter-A reset (IEM1 drops below baseline) still re-anchors as before
- Backwards compatible: pre-4.5.1 users without the saved attributes fall back to the old "first IEM1 read becomes baseline" behavior on the next session

## 4.5.0 - 2026-05-16

Three new automation-friendly sensors that replace the template boilerplate users typically write on top of Eveus, plus shared math and stronger tests.

- Add: `binary_sensor.eveus_car_connected` (`device_class: plug`) — true when a vehicle is electrically connected. Uses canonical device-state values ({Connected, Charging, Charge Complete, Paused}), not localized strings, so it stays stable across charger firmware label changes
- Add: `sensor.eveus_charging_finish_time` (`device_class: timestamp`) — absolute UTC ETA when the configured target SOC will be reached. Companion to the existing string-formatted `Time to Target SOC`; this one is what `device_class: timestamp` cards and "remind me 30 min before finish" automations consume directly. Minute-aligned so the state doesn't jitter on every poll. Returns unavailable when not charging, helpers missing, or target already reached
- Add: `sensor.eveus_session_cost` — running ₴-value of the current session = sessionEnergy × active rate. Returns unavailable (not 0) when the rate is unknown, so notifications never report a misleading "0 ₴"

## 4.4.1 - 2026-05-14

Patch release: SOC helper blip resilience and minor cleanups.

- Fix: SOC progress and session energy now survive transient unavailability of the SOC helpers. The energy baseline only updates on a real change in `input_number.ev_initial_soc`, not on a brief `None` read.

## 4.4.0 - 2026-05-14

Log hygiene, silent double-work elimination, and code correctness.

- Fix: Charging-ETA calculation failures and config-flow validation errors no longer generate ERROR entries in the HA log on every user typo — they are now logged at debug level only.
- Fix: Sensor platform setup no longer double-logs on exception.

## 4.3.0 - 2026-05-14

Bug fixes, statistics correctness, and resilience improvements.

- Fix: `Session Energy` and `SOC Energy` changed from `state_class=TOTAL` to `MEASUREMENT` — prevents broken long-term energy statistics in HA's energy dashboard
- Fix: `calculate_soc_percent` now always returns a numeric value on invalid input (was returning raw unvalidated argument)
- Fix: `format_duration` handles `None` and `NaN` inputs without raising `TypeError`
- Fix: Charging current setpoint now rounds instead of truncates (e.g. 15.99 A → 16 A, not 15 A)
- Fix: Offline polling cadence is preserved across first recovery (`_tune_update_interval` no longer resets to IDLE while `is_likely_offline` is True)
- Fix: `async_shutdown` now awaits cancelled post-command refresh tasks, eliminating "Task was destroyed but pending" log warnings on reload
- Fix: `configuration_url` in device info now uses the configured transport scheme (https chargers are now linked correctly in HA UI)
- Add: Sensor key uniqueness assertion in specification factory — duplicate sensor keys now fail fast at startup
- Add: `quality_scale` and `loggers` fields to `manifest.json`
- Improvement: Connection quality latency attribute rounded to 1 decimal to reduce spurious state-change writes

## 4.2.1 - 2026-05-13

Patch release for SOC helper baseline behavior.

### Fixed

- SOC helper baselines now remain anchored to the latest Initial SOC helper value across multiple charging sessions, so split charging while staying home continues to accumulate energy correctly.
- SOC helper baselines still reset when Initial SOC changes or when Counter A/IEM1 drops below the captured baseline, which handles explicit counter resets without treating old counter history as session energy.

## 4.2.0 - 2026-05-13

Security and correctness release for charger transport, command retries, force refresh, and SOC session math.

### Changed

- URL-style setup input now preserves `https://` and explicit ports. Runtime polling and commands use the stored scheme, so HTTPS chargers use TLS verification through Home Assistant's aiohttp session instead of being downgraded to HTTP.
- SOC helper sensors now calculate from the IEM1 delta captured at the start of the helper/session baseline instead of treating the charger's lifetime counter as session energy.

### Fixed

- Counter A reset (`rstEM1`) no longer auto-retries after ambiguous network failures, avoiding duplicate destructive reset requests.
- The Force Refresh button now bypasses offline polling backoff for the requested refresh instead of being blocked by the next scheduled retry time.
- SOC baselines reset when the initial SOC helper changes or when a new charging session starts.

## 4.1.1 - 2026-05-08

Maintenance release with a small hardening pass and dead-code removal. No behavior change for end users.

### Changed

- Command payloads are now built with `urllib.parse.urlencode` instead of f-string concatenation. The set of commands and values is unchanged; this just removes a footgun if a future command name or value ever contained a reserved character.
- Removed the redundant second helper-availability check inside `Time to Target SOC`. The first check (a few lines above) and the cached calculator already cover it.

### Removed

- Removed the unused `calculate_soc_kwh_cached` and `calculate_soc_percent_cached` wrappers from `utils.py`. Nothing in the integration or tests called them. Use `calculate_soc_kwh` / `calculate_soc_percent` directly.

## 4.1.0 - 2026-05-02

Version 4.1.0 is a reliability and responsiveness release. It focuses on faster control feedback, quieter offline handling, better recovery tools, and clearer diagnostics while keeping existing entities and dashboards compatible.

### Added

- Added a **Force Refresh** diagnostic button so you can manually ask the charger for fresh data without waiting for the next scheduled poll.
- Added reauthentication support, so rejected charger credentials can be updated from Home Assistant instead of removing and re-adding the integration.
- Added a Repair flow for rare invalid stored configuration data.
- Added command retries for brief Wi-Fi hiccups, reducing false "command rejected" errors when the charger or network drops a packet.

### Changed

- Charger data now refreshes faster after changing Charging Current or toggling Stop Charging / One Charge. The integration keeps the UI responsive immediately, then checks the charger again shortly after the command has had time to apply.
- Polling is now adaptive: faster while charging, slower while idle, and less noisy when a charger appears offline.
- Offline and reconnect behavior is smoother, with fewer unnecessary logs and more consistent availability reporting in Home Assistant.
- Device details such as firmware now fill in later if the charger was offline during Home Assistant startup.
- Optional SOC helper sensors now behave better when helpers are created later, when helper values are invalid, or when multiple chargers are configured.
- Sensor and control state handling is leaner, with less repeated work on every coordinator update.
- The integration metadata now identifies Eveus as a device integration.

### Fixed

- Fixed the System Time sensor showing the wrong local time in Kyiv and other time zones.
- Fixed control flicker caused by stale charger reads arriving while a command was still in progress.
- Fixed unload failures so Home Assistant can handle and report them correctly instead of leaving the entry stuck in a failed-unload state.
- Fixed coordinator health reporting during offline backoff so Home Assistant does not see a false healthy update.
- Fixed post-command refresh scheduling so delayed refreshes actually run on Home Assistant's event loop.
- Fixed optional SOC calculations so real zero values, such as `0 kWh` charged or `0 W` power, are not replaced by stale values.
- Fixed host normalization for URL-style and trailing-dot local host values.
- Fixed several edge cases around setup validation, migration, diagnostics, numeric validation, and cleanup during shutdown.

## 4.0.0 - 2026-04-28

Version 4.0.0 is a major modernization release focused on reliability, setup validation, diagnostics, and Home Assistant compatibility. Existing entity names and unique IDs remain intact, so dashboards and automations can continue working after the update.

### Added

- Added Home Assistant reconfigure support for updating charger IP address, credentials, and model after setup.
- Added downloadable diagnostics with sensitive fields redacted.

### Changed

- Replaced the custom polling loop with Home Assistant's `DataUpdateCoordinator`.
- Moved runtime objects to typed `ConfigEntry.runtime_data`, following current Home Assistant best practices.
- Improved setup validation so unreachable IP addresses, invalid credentials, malformed responses, and non-Eveus JSON responses fail before an integration entry is created.
- Normalized stored hosts from URL-style input such as `http://192.168.1.50/main` to `192.168.1.50`.
- Reworked entity setup to use coordinator-backed entities and entity descriptions while preserving unique IDs.
- Improved default device-page organization within Home Assistant's built-in buckets:
  - Controls remain under Configuration.
  - Device health/status entities remain under Diagnostic.
  - User-facing measurements, energy, cost, rate, and SOC values remain under Sensors.
- Updated the minimum supported Home Assistant version to 2024.4.

### Fixed

- Fixed Home Assistant translation validation for number entity state attributes.
- Fixed the previous behavior where an integration entry could be created for an unreachable or unrelated device.
- Fixed stale entity update plumbing by letting Home Assistant manage coordinator listeners.

## 3.0.3

Version 3.0.3 focused on bug fixes, clearer setup errors, and translation consistency.

### Fixed

- Fixed SOC Energy behavior when the calculated battery value is `0 kWh`.
- Fixed connectivity logging so offline charger detection is reported correctly.
- Updated English translations to clearly include 48A model support.
- Added missing setup error messages for invalid input and invalid device responses.

## 3.0.2

Version 3.0.2 improved timezone handling and removed an unnecessary dependency.

### Changed

- Migrated timezone handling from `pytz` to Python's built-in `zoneinfo`.
- Removed `pytz` from external dependencies.

### Fixed

- Fixed manifest version metadata.
- Added missing setup error messages for invalid input and invalid device responses.
- Removed an unused sensor helper.

## 3.0.1

### Changed

- Updated version metadata.

## 3.0.0

Version 3.0.0 added multi-charger support, 48A model support, safer restart behavior, and a large reliability refresh.

### Added

- Added support for multiple Eveus chargers in one Home Assistant instance.
- Added 48A charger model support.
- Added optimistic UI feedback for the current slider and charging switches.
- Added optional SOC helper support. The integration works without SOC helpers.

### Changed

- Improved restart safety so switches and current controls no longer send commands during Home Assistant startup.
- Improved offline handling so powered-off chargers back off quietly instead of flooding logs.
- Improved short Wi-Fi drop handling so entities avoid unnecessary unavailable flicker.
- Switched to Home Assistant's shared HTTP session.
- Simplified command handling with a lock-based direct send path.
- Reworked sensor creation with a factory pattern.
- Reduced code size while preserving existing functionality.

### Fixed

- Fixed authentication validation ordering for `401` responses.
- Fixed DST cache reuse for timezone calculations.
- Fixed sensor updates when only temperature, cost, or rate data changed.
- Fixed deterministic backoff timing.
- Fixed device number assignment race conditions during setup.

## 2.1.0

Version 2.1.0 focused on performance, connection resilience, and SOC responsiveness.

### Changed

- Improved SOC updates when helper values change.
- Optimized SOC calculations with caching.
- Reduced memory and CPU usage through simpler data structures and cached lookups.
- Improved connection persistence and retry behavior.
- Improved network recovery and connection monitoring.
- Consolidated duplicate code and streamlined sensor creation.

## 2.0.1

### Fixed

- Simplified Reset Counter A behavior for better reliability.

## 1.0

### Added

- Initial Eveus EV Charger integration release.
