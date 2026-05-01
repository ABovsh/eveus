# Changelog

## 4.0.1b6 - 2026-05-02

### Added

- Faster UI feedback after toggling a switch or changing the charging current: the coordinator now schedules a single delayed refresh 4 seconds after a successful command instead of refreshing immediately. The charger needs a few seconds to reflect a new state in its API, so the previous immediate refresh often returned stale data and users had to wait up to a full poll interval (30 s) to see the change.
- Rapid successive commands (e.g. toggling a switch off then back on) cancel and reschedule the pending refresh, so the refresh always fires 4 seconds after the most recent command. Combined with the existing entity-level optimistic state TTL this prevents stale-read flicker.
- The pending delayed refresh is cancelled on coordinator shutdown so it never fires against a torn-down config entry.

## 4.0.1 - 2026-04-29

Version 4.0.1 is a maintenance release focused on smoother upgrades, quieter normal operation, easier recovery, and more consistent entity behavior.

### Changed

- Improved upgrade handling for existing entries that were created with URL-style host values.
- Improved optional SOC calculations for setups with more than one charger.
- Improved Time to Target SOC behavior so it uses the same SOC calculation path as the other SOC sensors.
- Improved short offline/reconnect handling for sensors and controls.
- Improved offline handling so powered-off chargers stay quiet in normal Home Assistant logs.
- Improved optional SOC helper handling so missing helpers stay quiet in normal Home Assistant logs.
- Improved optional SOC helper sensors so they react when helper entities are created after the integration is already loaded.
- Improved optional SOC helper validation so out-of-range helper values are treated as invalid instead of producing misleading SOC estimates.
- Improved connection quality reporting so it reflects recent charger connectivity instead of lifetime history.
- Improved diagnostics with a clearer sanitized device snapshot for troubleshooting.
- Improved Home Assistant lifecycle handling for coordinator shutdown, entity availability updates, and helper listeners.
- Improved entity update efficiency by avoiding unnecessary state writes when values have not changed.
- Improved command throttling so repeated failed commands still respect the cooldown.
- Improved setup validation for chargers that return valid JSON with a nonstandard response content type.
- Improved stored device number cleanup for entries that somehow contain a string or invalid device number.
- Improved optimistic switch and number state reconciliation so property reads no longer mutate internal state.
- Improved sensor state handling so `native_value`, `is_on`, and control value reads are served from cached entity attributes without recomputing or mutating integration state.
- Improved sensor attribute handling so extra attributes are refreshed during coordinator/helper updates instead of rebuilt on every property access.
- Improved Reset Counter A safe-mode cleanup by using Home Assistant's cancellable delayed callback helper.
- Improved credential handling so passwords are preserved exactly as entered while usernames are still trimmed.
- Improved coordinator failure reporting after setup so Home Assistant can see failed refreshes while powered-off chargers remain supported at startup.
- Improved sensor specification and charger system-time caching to reduce repeated work on every poll.
- Improved coordinator cleanup so scheduled refresh handling is shut down correctly.
- Improved numeric validation so invalid values such as `nan` or `inf` are not exposed as sensor values.
- Added Home Assistant reauthentication support for updating credentials after the charger rejects the stored username or password.
- Added a Home Assistant Repair flow for rare invalid stored setup data.

### Fixed

- Fixed a migration issue that could run the same cleanup again after restart.
- Fixed possible SOC calculation mix-ups when multiple chargers are configured.
- Fixed stale sensor fallback behavior during short availability grace periods.
- Fixed hostname normalization for trailing-dot local hostnames such as `charger.local.`.
- Fixed optional SOC calculations so real zero values, such as `0 kWh` charged or `0 W` charging power, are not replaced by stale cached values.

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
