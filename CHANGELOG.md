# Changelog

## 4.9.1-rc.3 - 2026-05-19

### 🐛 Fixed
- **Counter A/B Cost**: now declared as monetary sensors with the `UAH` currency code, so Home Assistant accepts them in long-term statistics and energy/cost dashboards.
- **Measurements**: voltage, current, power, leak current, and per-phase readings reject negative and non-finite firmware payloads instead of charting them as real values.
- **Session Energy / SOC**: negative `sessionEnergy` readings are dropped before they feed SOC math, so a charger glitch can't push the SOC sensor backwards.
- **Current setpoint**: out-of-range firmware echoes below the 7 A minimum are filtered out instead of being shown as the active setpoint.
- **Connection Quality**: NaN / infinity / boolean values from the metrics pipeline no longer surface as a misleading 100% reading — the sensor goes unknown instead.
- **Numeric safety**: boolean values arriving in numeric fields are no longer silently converted to 0.0 / 1.0.

## 4.9.1-rc.2 - 2026-05-18

### 🐛 Fixed
- **Session Cost**: long-term statistics no longer break — `sensor.eveus_session_cost` now reports as a measurement instead of a totalled value, so the per-session reset is no longer treated as a meter-rollover.
- **Setup form**: choosing a model and phases no longer crashes when an older stored phase value is invalid; the form falls back to the default and continues.
- **Diagnostics**: the integration's title in the diagnostics dump no longer echoes the configured host alongside the redacted entry data.
- **Logs**: a malformed legacy host stored on the entry is no longer printed verbatim on migration; only the entry identifier appears.

### 🔧 Changed
- **Config flow** rejects malformed input earlier and more clearly: URLs that embed credentials (`http://user:pass@host`), usernames containing `:`, and bare IPv6 inputs are now validated before any network call.
- **Connection Quality** attributes (`connection_quality`, `latency_avg`) are exposed as quantized numeric values instead of formatted strings, so dashboards can chart them and the sensor no longer emits a state change every poll from sub-percent latency drift.

## 4.9.1-rc.1 - 2026-05-18

### 🐛 Fixed
- **Time Zone**: picking a new offset no longer snaps back to the previous value for ~30 s before the next poll. `select.eveus_time_zone` now holds the chosen value during the brief window between the command being acknowledged and the charger reporting the new `timeZone` field — matching the responsive feel of `switch.eveus_stop_charging`, `number.eveus_charging_current`, and the schedule time entities. If the charger rejects the write, the select reverts to the device value immediately.

## 4.9.0 - 2026-05-17

Stable promotion of the 4.9.0-rc series. Highlights:

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

## 4.9.0-rc.5 - 2026-05-17

### ✨ New entities
- **`switch.eveus_schedule_1_enabled`** / **`switch.eveus_schedule_2_enabled`** — arm or disarm the charger's two on-device schedule slots directly from HA.
- **`time.eveus_schedule_1_start`** / **`time.eveus_schedule_1_stop`** and **`time.eveus_schedule_2_start`** / **`time.eveus_schedule_2_stop`** — native HA time pickers for each slot's start/stop window. Changes apply directly on the charger; no more digging into the device's web UI to shift the night-tariff window.

### 📊 Dashboard
- New `docs/dashboard.yaml` — drop-in Lovelace view that exposes every Eveus capability (status, controls, schedules, live charts, session totals, diagnostics). See README for screenshot and install steps.

## 4.9.0-rc.4 - 2026-05-17

### 🐛 Fixed
- Starting a charging session (Stop Charging off + One Charge on) no longer takes up to a minute to show as charging. `sensor.eveus_current` and the charging-state entities now reflect the new session within ~10–20 s instead of waiting for the next 60 s idle poll. Stopping a session also reflects faster.

## 4.9.0-rc.3 - 2026-05-17

### ✨ New
- **`switch.eveus_adaptive_mode`** — on/off control of the charger's adaptive (AI) mode. Pairs with the existing `Adaptive Charging` diagnostic sensor.

### 🔧 Changed
- `Sync Time` and `Time Zone` are now enabled by default.
- `Force Refresh` moved from the Diagnostic to the Config section in the device UI.

## 4.9.0-rc.2 - 2026-05-17

### ✨ New entities
- **`sensor.eveus_leakage_current`** (mA) — live RCD reading. `0` is normal; non-zero indicates a ground-fault leak.
- **`sensor.eveus_leakage_current_peak`** (mA) — peak-hold leakage value.

### ✨ 3-phase support
- New `Phases` field (1 or 3) in the setup and reconfigure dialogs, defaults to `1`.
- When `Phases = 3`, four additional sensors are exposed: `Current Phase 2`, `Current Phase 3`, `Voltage Phase 2`, `Voltage Phase 3`.
- Existing 1-phase setups are migrated transparently — no action required.

## 4.9.0-rc.1 - 2026-05-17

### ✨ New entities
- **`button.eveus_sync_time`** — pushes the host's current time to the charger's clock.
- **`select.eveus_time_zone`** — readable / writable time-zone offset, range `-12..+14`.

## 4.8.0 - 2026-05-16

Promotion of `4.8.0-rc.2` to a stable release. See `4.8.0-rc.1` and `4.8.0-rc.2` notes for the full change list.

## 4.8.0-rc.2 - 2026-05-16

### 🔒 Security / UX
- Config-flow and reauth password fields are now rendered as masked password inputs instead of plain text.

## 4.8.0-rc.1 - 2026-05-16

### ⚠️ Breaking — entity platform change
- `switch.eveus_reset_counter_a` is removed and replaced by `button.eveus_reset_counter_a`, which models the one-shot reset action correctly. The unique-id is preserved, but the entity domain moves from `switch.` to `button.`. Update any dashboards or automations that referenced `switch.eveus_reset_counter_a`.

### ✨ New
- `button.eveus_reset_counter_b` — momentary reset for the second user-resettable energy counter. Mirrors Counter A.

### 🐛 Correctness fixes
- Reconfigure, reauth, and the "invalid_config" repair flow now preserve `device_number` when updating entry data. Previously a reconfigure on a multi-charger setup could reassign the number on reload, breaking entity unique-id stability.
- `Connection Quality` now reports `unknown` on internal error instead of falsely showing `100%` (Excellent).
- Diagnostics no longer raises when called before setup completes — it returns a partial payload instead.

### 🔒 Security
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
