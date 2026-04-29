# Changelog

## 4.0.1 - 2026-04-29

Version 4.0.1 is a maintenance release focused on smoother upgrades, optional SOC helper reliability, and more consistent entity behavior.

### Changed

- Improved upgrade handling for existing entries that were created with URL-style host values.
- Improved optional SOC calculations for setups with more than one charger.
- Improved Time to Target SOC behavior so it uses the same SOC calculation path as the other SOC sensors.
- Improved short offline/reconnect handling for sensors and controls.
- Improved offline handling so powered-off chargers stay quiet in normal Home Assistant logs.

### Fixed

- Fixed a migration issue that could run the same cleanup again after restart.
- Fixed possible SOC calculation mix-ups when multiple chargers are configured.
- Fixed stale sensor fallback behavior during short availability grace periods.
- Fixed hostname normalization for trailing-dot local hostnames such as `charger.local.`.

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

- Added multi-device support.
- Added 48A model support.
- Improved restart safety for switches and number controls.
- Added optimistic UI behavior for controls.
- Improved offline handling and connection quality reporting.
- Refactored the codebase while preserving existing functionality.
- Fixed multiple setup, network, billing, SOC, and translation issues.
