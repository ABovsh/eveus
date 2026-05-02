# Changelog

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
