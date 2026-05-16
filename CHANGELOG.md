# Changelog

## 4.8.0-rc.1 - 2026-05-16

Pre-release covering an extensive Codex review pass plus a new entity. Behavior-changing items are listed under ⚠️.

### ⚠️ Breaking — entity platform change
- `switch.eveus_reset_counter_a` is removed. It is replaced by `button.eveus_reset_counter_a`, which models the one-shot reset action correctly (HA switch semantics imply togglable binary state — a counter reset is not that). The unique-id (`eveus_reset_counter_a`) is preserved, so the new button inherits the entity registry slot — but the entity domain moves from `switch.` to `button.`. Update any dashboards/automations that referenced `switch.eveus_reset_counter_a`.

### ✨ New
- `button.eveus_reset_counter_b` — momentary reset for the second user-resettable energy counter (`rstEM2` / `IEM2`). Mirrors Counter A; appears under the existing Eveus device.

### 🐛 Correctness fixes
- Reconfigure, reauth, and the "invalid_config" repair flow now preserve `device_number` (and other integration-owned keys) when updating `entry.data`. Previously a reconfigure on a multi-charger setup could reassign the number on reload, breaking entity unique-id stability and the device registry mapping.
- `get_connection_quality` now returns `None` on internal error instead of `100`. A calculation failure no longer masquerades as "Excellent".
- Diagnostics no longer raises when called before setup completes — it returns a partial payload with `setup.ready = False` instead.

### 🔒 Security
- `diagnostics.py` now redacts `host` and `unique_id` (in addition to `username`/`password`). Diagnostic dumps shared publicly no longer leak LAN topology.
- The config flow logs a `WARNING` when the charger is configured over plain HTTP, calling out that Basic Auth credentials are sent in cleartext on every poll. Default scheme is unchanged.

### 🏎 Performance
- Control entities (`Stop Charging`, `One Charge`, `Charging Current`) only call `async_write_ha_state()` when their visible value or availability actually changes. Coordinator ticks no longer generate redundant `state_changed` events for unchanged controls.

### 🧹 Internals & cleanups
- `BaseEVHelperSensor._handle_coordinator_update` now calls `_maybe_finalize_device_info()`, so first-firmware-after-boot updates reach SOC/ETA sensors like every other entity type.
- Removed unused `_device_info` cache from `ConfigFlow`.
- Removed unused `_success_count` / `_total_count` counters from `EveusUpdater`.
- Replaced an `assert` for duplicate sensor keys with an explicit `RuntimeError` (assertions are stripped under `python -O`).

### 🧪 Tests
- Repair-flow test locks in `device_number` preservation across an invalid-config repair.
- Diagnostics test covers the missing-`runtime_data` path.
- Reset-counter tests rewritten against the new button platform.
- Setup test asserts the new button list (`Force Refresh`, `Reset Counter A`, `Reset Counter B`).

## 4.7.2 - 2026-05-16

Bugfix: `sensor.eveus_soc_energy` / `sensor.eveus_soc_percent` could still show `unknown` after 4.7.1 when `input_number.ev_target_soc` was missing or out-of-range — typical for the first few seconds after a HA reboot, before the input_number platform finishes loading.

- Fix: `CachedSOCCalculator._update_input_cache` no longer treats `target_soc` as required. SOC calculations need only Initial SOC, Battery Capacity, and SOC Correction. Target SOC is consumed exclusively by the ETA-class sensors (Time to Target SOC, Charging Finish Time), which already degrade gracefully when it is None. Effect: a startup race or a deleted/invalid Target SOC helper no longer hides SOC %/kWh
- Test: extended `test_soc_calculator_reports_missing_and_invalid_helpers` to lock in the new contract — missing/out-of-range Target SOC keeps SOC working and returns Initial SOC; missing/out-of-range Battery Capacity (a true required input) still disables SOC

## 4.7.1 - 2026-05-16

Bugfix release covering two regressions surfaced after 4.6.0/4.7.0.

- Fix: `binary_sensor.eveus_car_connected` got stuck on the value from the very first fetch and never reflected later plug-in / plug-out transitions. `_handle_coordinator_update` recomputed `previous_state = self.is_on` after the coordinator had already swapped in the new payload, so the comparison always equalled the current value and `async_write_ha_state()` was never called. Now tracks the last value actually pushed to HA in a dedicated instance attribute
- Fix: `sensor.eveus_soc_energy` and `sensor.eveus_soc_percent` showed `unknown` whenever `sessionEnergy` was missing from the payload (cold start before first poll, brief offline blip). Now treat a missing `sessionEnergy` as 0 delivered, so SOC reprojects from `input_number.ev_initial_soc` instead of going unknown. Cached-last-value fallback is no longer needed and was removed
- Tests: regression case for the binary-sensor write path (in-place coordinator data swap, Charging → Standby), and updated SOC sensor test to lock in the Initial-SOC fallback

## 4.7.0 - 2026-05-16

Minor release: five new diagnostic sensors expose the charger's adaptive (AI) mode and scheduled-charging slots. Adds a firmware-drift test backed by a real `/main` snapshot. Cleans up a dead `try/except` in sensor setup.

- New diagnostic sensor `sensor.eveus_adaptive_charging` — "Active" / "Idle" from `aiStatus`. Indicates whether the charger is currently throttling current to maintain voltage under heavy load
- New diagnostic sensor `sensor.eveus_adaptive_current_limit` — current cap chosen by the AI throttle (A), from `aiModecurrent`
- New diagnostic sensor `sensor.eveus_adaptive_voltage_threshold` — voltage floor that triggers throttling (V), from `aiVoltage`
- New diagnostic sensors `sensor.eveus_schedule_1`, `sensor.eveus_schedule_2` — "Enabled" / "Disabled" with attributes `window` (HH:MM–HH:MM), `start`, `stop`, and optional `current_limit_a` / `energy_limit_kwh`. Mirrors the charger's `sh1*` / `sh2*` slot config
- New test `tests/test_real_payload_schema.py` — runs every value getter against `tests/fixtures/real_main_response.json` (captured from a live Eveus Pro 1P 2024, FW GRM070A-R3.05.2). Catches firmware schema drift that synthetic dict-based unit tests cannot see
- Cleanup: removed `try: ... except Exception: raise` no-op wrapper in `sensor.py:async_setup_entry`

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
- Refactor: Extracted `calculate_remaining_seconds` and `_remaining_seconds_or_state` in `utils.py` so the Time-to-Target string sensor and the new Finish-Time timestamp sensor share a single source of truth for charging-ETA math
- Refactor: Hoisted shared input-resolution into `BaseEVHelperSensor._resolve_remaining_inputs` so both Time-to-Target and Finish-Time sensors collect helpers in the same way — no drift possible
- Tests: 29 new behavior tests covering truth tables for `calculate_remaining_seconds`, Session Cost edge cases (offline / no rate / missing energy / zero energy / Rate 2 active), Finish-Time sensor (active charging / not charging / helpers missing / target reached / jitter resistance / device_class), and Car-Connected binary sensor (all 8 device-state values / unavailable / unparseable input / unique_id convention)
- Tests: Tightened `test_sensor_specification_factory_exposes_expected_entities` from `>= 20` to exact count (26) so silent additions/removals fail the build
- Tests: Added `test_value_getters_reject_nan_and_inf` — regression guard so `float("nan")`/`float("inf")` payloads do not enter HA long-term statistics or downstream cost/ETA calculations

## 4.4.1 - 2026-05-14

Patch release: SOC helper blip resilience and minor cleanups.

- Fix: Energy baseline in `_get_energy_charged` no longer resets when SOC helpers briefly become unavailable — session energy and SOC progress survive transient `None` reads, and the baseline only updates on a real change in `initial_soc`
- Cleanup: Simplified redundant ternary in `EVSocKwhSensor._get_sensor_value` and `EVSocPercentSensor._get_sensor_value` — `_cached_value` already holds the post-update value
- Cleanup: Consolidated three sequential `except X: raise` clauses in `async_setup_entry` into a single tuple-form re-raise
- Add: Regression test for energy baseline survival across helper unavailability

## 4.4.0 - 2026-05-14

Log hygiene, silent double-work elimination, and code correctness.

- Fix: `calculate_remaining_time` now logs at debug level instead of error — transient calculation failures no longer generate ERROR entries in the HA log
- Fix: Config flow validation failures (InvalidInput, InvalidDevice) for setup, reconfigure, and reauth steps now log at debug level — wrong IP or credentials no longer pollute the HA log on every user typo
- Fix: `sensor.py` platform setup no longer double-logs on exception — HA's platform loader already captures the traceback
- Fix: `BaseCounterSwitch.async_turn_off` now implemented as a no-op, matching `async_turn_on` — prevents `NotImplementedError` if the base class is ever used directly
- Fix: `_get_energy_charged` now calls `_update_input_cache` directly instead of `are_helpers_available` discarding the return value — removes side-effecting query call
- Fix: `InputEntitiesStatusSensor._check_inputs` no longer calls `_update_extra_state_attributes` internally — the coordinator update cycle owns attribute updates, preventing double computation per poll
- Fix: Compatibility alias docstrings in `BaseSwitchEntity` corrected — aliases are actively used in current tests, not "older" ones
- Fix: `SensorSpec.state_class` type annotation corrected to `Optional[SensorStateClass | str]`
- Add: Tests for `CachedSOCCalculator` properties (`battery_capacity`, `initial_soc`, `soc_correction`, `target_soc`) and cache invalidation behavior
- Add: Test verifying `connection_quality` exposes all expected dict keys
- Add: Test verifying `is_likely_offline` requires both failure count and time thresholds simultaneously
- Add: Test verifying `BaseCounterSwitch.async_turn_off` does not raise

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
- Added a dedicated automated test suite covering config validation, coordinator polling, diagnostics, command payloads, sensor mappings, entity IDs, and utility calculations.

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
