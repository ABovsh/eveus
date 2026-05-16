# Eveus Integration — Audit Findings (v4.2.1)

Audit scope: `custom_components/eveus/` plus `manifest.json`, cross-referenced with `tests/`.
Three independent passes: correctness/bugs, code quality/duplication, efficiency.

This document is written for a downstream LLM (or engineer) with **no prior context**. Each finding lists exact file:line, root cause with the offending snippet quoted, a concrete fix, and how to verify. Apply in priority order at the bottom.

---

## 1. CORRECTNESS / BUGS

### [HIGH-1] `Session Energy` and `EVSocKwhSensor` use `state_class=TOTAL` without `last_reset` → broken long-term statistics
**Files:** `sensor_definitions.py:373` (Session Energy spec), `ev_sensors.py:317` (`EVSocKwhSensor._attr_state_class`)
**Category:** ha-api
**Symptom:** HA energy dashboard / long-term stats show wrong or zero values for these sensors. The charger's `sessionEnergy` resets to 0 each new session — that's `TOTAL_INCREASING` semantics, not `TOTAL`. With plain `TOTAL` HA expects monotonic deltas per `last_reset`, but `_attr_last_reset` is never set.
**Root cause:**
```python
# sensor_definitions.py:373
("Session Energy", get_session_energy, "mdi:...", SensorStateClass.TOTAL),
# ev_sensors.py:317
_attr_state_class = SensorStateClass.TOTAL   # EVSocKwhSensor
```
**Fix:** Change both to `SensorStateClass.MEASUREMENT` (they are running gauges, not lifetime totals). `Total Energy`, `Counter A/B Energy` remain `TOTAL_INCREASING` (correct).
**Test:** Extend `tests/test_metadata.py` / `tests/test_sensor_definitions.py` to assert state_class for each energy sensor.

---

### [HIGH-2] `calculate_soc_percent` returns unsanitized `initial_soc` on validation failure
**File:** `utils.py:228-232`
**Category:** validation
**Symptom:** Calling with bad input (e.g. string, NaN) returns the raw unvalidated argument; HA then receives a non-numeric value for a `%`-typed sensor.
**Root cause:**
```python
if inputs is None:
    return initial_soc or 0   # initial_soc may still be raw string/NaN
...
if battery_capacity <= 0:
    return initial_soc        # also raw, not normalized
```
**Fix:** Return `0.0` (or `None` if callers tolerate it) on validation failure. After successful validation, use only the normalized values.
**Test:** Add to `tests/test_utils.py`:
```python
assert calculate_soc_percent("bad", 80, 10, 0) != "bad"
```

---

### [HIGH-3] Offline backoff cadence cancelled by next success
**File:** `common_network.py:244-260` (`_tune_update_interval`) vs `:236` (`_record_failure`)
**Category:** polling / bug
**Symptom:** `_record_failure` correctly switches `update_interval` to `OFFLINE_UPDATE_INTERVAL`, but `_tune_update_interval` (called from `_record_success`) only picks `CHARGING_*` or `IDLE_*`. The first successful poll after a transient blip resets interval even while `is_likely_offline` is still true. Meanwhile `_next_poll_attempt` backoff causes the coordinator to wake on the short cadence only to bail out with `UpdateFailed`, producing log noise every 30 s.
**Fix:** In `_tune_update_interval`, branch on offline state first:
```python
def _tune_update_interval(self, data):
    if self.is_likely_offline:
        new = OFFLINE_UPDATE_INTERVAL
    elif self._is_charging(data):
        new = CHARGING_UPDATE_INTERVAL
    else:
        new = IDLE_UPDATE_INTERVAL
    self._set_update_interval(new)
```
Also drop the `_UPDATE_INTERVALS` dict pre-cache or assert uniqueness on import (it silently drops entries if any two constants in `const.py` are ever set equal).
**Test:** In `tests/test_common_network.py`, force `_consecutive_failures > 10` and `last_success_time` old, then call `_record_success` with a non-charging payload; assert `update_interval == OFFLINE_UPDATE_INTERVAL`.

---

### [HIGH-4] `format_duration` raises on `None` / NaN-derived ints in caller chain
**File:** `utils.py:150-166`
**Category:** validation
**Symptom:** `format_duration(None)` raises `TypeError` on `seconds <= 0` (comparing `None` to int in Py3). The internal `try` catches `(TypeError, ValueError)` — but `calculate_remaining_time` calls `format_duration(int(total_minutes * 60))` and `int(nan)` raises before reaching `format_duration`.
**Fix:**
```python
def format_duration(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "0m"
    if seconds <= 0:
        return "0m"
    ...
```
**Test:** `tests/test_utils.py` — add `assert format_duration(None) == "0m"`.

---

### [MEDIUM-1] `EveusCurrentNumber.async_set_native_value` truncates instead of rounding
**File:** `number.py:118`
**Category:** float
**Symptom:** `int(7.9) → 7`. If HA passes `15.99` (float math), user gets 15 A instead of 16 A.
**Fix:** `int_value = int(round(clamped_value))`
**Test:** Add test passing `15.99` to `async_set_native_value`; assert `_pending_value == 16`.

---

### [MEDIUM-2] `async_shutdown` cancels tasks without awaiting them
**File:** `common_network.py:199-202` (`EveusUpdater.async_shutdown`)
**Category:** lifecycle / leak
**Symptom:** Python may log "Task was destroyed but it is pending!" on integration reload.
**Fix:**
```python
async def async_shutdown(self):
    self._cancel_pending_refreshes()
    if self._post_command_refresh_tasks:
        await asyncio.gather(*self._post_command_refresh_tasks, return_exceptions=True)
    self._post_command_refresh_tasks.clear()
    await super().async_shutdown()
```
**Test:** Extend `tests/test_common_network.py` to schedule a refresh, then call `async_shutdown`; assert no pending tasks remain.

---

### [LOW-1] `configuration_url` hardcoded to `http://` ignores stored scheme
**File:** `utils.py:146` (`get_device_info`)
**Category:** bug (UX)
**Symptom:** Clicking "Visit device" on the HA device page opens plain HTTP for an HTTPS-configured charger.
**Fix:** Thread `scheme` through `get_device_info` (and `_build_device_info`), default `"http"`:
```python
"configuration_url": f"{scheme}://{host}",
```
Pass `scheme` from `EveusRuntimeData`.
**Test:** Extend `tests/test_utils.py::test_get_device_info_*` for the https case.

---

### [LOW-2] Currency sensors lack `device_class=MONETARY`
**File:** `sensor_definitions.py:449-458` (Counter A/B Cost)
**Symptom:** `TOTAL_INCREASING` + non-energy unit `₴` without monetary device_class produces odd dashboard behavior.
**Fix:** Add `device_class=SensorDeviceClass.MONETARY`.

---

### [LOW-3] No uniqueness guard on sensor specs
**File:** `sensor_definitions.py` (`create_sensor_specifications`)
**Symptom:** Duplicate name/key would silently collide on `unique_id`; HA shows only the first.
**Fix:** At factory end:
```python
keys = [s.key for s in result]
assert len(keys) == len(set(keys)), f"duplicate sensor keys: {keys}"
```
**Test:** Add metadata test asserting uniqueness.

---

## 2. CODE QUALITY / DUPLICATION

### [Q-HIGH-1] Five copies of `_handle_coordinator_update` skeleton
**Files:** `common_base.py:197-201, 343-351`, `ev_sensors.py:267-281`, `number.py:155-179`, `switch.py:179-198, 240-246`
**Problem:** Each entity reimplements the same sequence (`_maybe_finalize_device_info` → `_update_availability_state` → refresh → maybe write). Drift-prone.
**Refactor:** Template method on `BaseEveusEntity`:
```python
def _handle_coordinator_update(self):
    self._maybe_finalize_device_info()
    changed = self._update_availability_state()
    changed |= self._refresh_entity_state()   # subclass hook
    if changed:
        self.async_write_ha_state()
```
Subclasses override `_refresh_entity_state()` only.

### [Q-HIGH-2] `_resolve_state` / `_resolve_value` duplicated across number, switch, counter
**Files:** `number.py:93-109`, `switch.py:122-137, 225-232`
**Refactor:** Single `_resolve_controlled_value` on `OptimisticControlMixin` parameterized by `(state_key, parse_fn, fallback)`.

### [Q-HIGH-3] Triple-layered availability state
**Files:** `common_network.py:84,98,208-228`, `common_base.py:44-46,82-108`
**Problem:** `EveusUpdater._device_available` mirrors `last_update_success`; entity then keeps `_entity_available`, `_unavailable_since`, `_last_known_available`. Three sources of truth.
**Refactor:** Drop `_device_available`; expose `self.last_update_success` directly. Entity layer keeps only the grace-period timer.

### [Q-HIGH-4] Stringly-typed device JSON keys scattered across 9 files
**Files:** `sensor_definitions.py:171-195,266`, `ev_sensors.py:285,385`, `number.py:77,100,166`, `switch.py:37-55,129,188,230`, `diagnostics.py:12-24`, `config_flow.py:121,140`, `utils.py:128-129`, `common_network.py:253`
**Refactor:** Add to `const.py`:
```python
class DK:  # device keys
    CURRENT_SET = "currentSet"
    EVSE_ENABLED = "evseEnabled"
    POWER = "powerMeas"
    SESSION_ENERGY = "sessionEnergy"
    IEM1 = "IEM1"
    VOLT_MEAS_1 = "voltMeas1"
    SESSION_TIME = "sessionTime"
    SYSTEM_TIME = "systemTime"
    TARIF = "tarif"
    TARIF_A_VALUE = "tarifAValue"
    TARIF_B_VALUE = "tarifBValue"
    ACTIVE_TARIF = "activeTarif"
    ONE_CHARGE = "oneCharge"
    RST_EM1 = "rstEM1"
    GROUND = "ground"
    STATE = "state"
    SUB_STATE = "subState"
    VER_FW_MAIN = "verFWMain"
```
Replace every raw string usage.

### [Q-HIGH-5] Magic numbers for device state values
**Files:** `sensor_definitions.py:214` (`state == 7`), `:222-226` (`value == 1/0`), `:266` (rate index `{0,1,2}`), `:286-289`
**Refactor:** Add `DEVICE_STATE_ERROR = 7`, `RATE_KEY_BY_INDEX = {0:"tarif", 1:"tarifAValue", 2:"tarifBValue"}`, `GROUND_STATES = {1:"Connected", 0:"Not Connected"}` to `const.py`.

### [Q-HIGH-6] Six legacy alias properties in `OptimisticControlMixin`
**File:** `switch.py:85-110`
**Problem:** `_optimistic_value` ↔ `_optimistic_state` etc., declared "for older tests" — no current callers.
**Refactor:** Delete; rename any straggler test references.

### [Q-MED-1] Five different rate-limit logging mechanisms
**Files:** `common_base.py:43,113,286`, `sensor_definitions.py:38,84,100-102`, `common_command.py`
**Refactor:** One `self._rate_log: RateLog` on `BaseEveusEntity` with per-key buckets. Drop the float versions.

### [Q-MED-2] Dead code to remove
- `common_base.py:115-122` `get_cached_data_value` — never called.
- `common_base.py:354-358` `EveusDiagnosticSensor` — no subclasses.
- `common.py` `EveusConnectionError` — never raised. Move `EveusError` to `const.py`, delete `common.py` (just a re-export shim).
- `common_network.py:73-74,210-211,224-226` `_success_count`/`_total_count` — written, never read.
- `__init__.py:39-46,174` `runtime_data.title` — set, never read.

### [Q-MED-3] Circular-ish import
**Files:** `__init__.py:28`, `config_flow.py:32`, `__init__.py:90` (lazy import), `__init__.py:169` (lazy import)
**Refactor:** Move `CONFIG_ENTRY_VERSION`, `EveusConfigEntry`, `EveusRuntimeData` to a new leaf `types.py`. Move `_split_host_and_scheme` to `utils.py`. Remove lazy imports.

### [Q-MED-4] `BaseCounterSwitch` is a switch impersonating a button
**File:** `switch.py:201-289`
**Problem:** `turn_on` is a no-op; only `turn_off` resets the counter. `_last_reset_time` written never read; `_reset_lock` serializes a user click already serial on the loop.
**Refactor:** Convert to `ButtonEntity`. Drop `_last_reset_time` and `_reset_lock`.

### [Q-MED-5] `SWITCH_DESCRIPTIONS[2]` positional indexing
**File:** `switch.py:31-56, 254, 303-306`
**Refactor:** Split into two named tuples (`OPTIMISTIC_SWITCH_DESCRIPTIONS`, `COUNTER_DESCRIPTIONS`) or key by `key` and look up by name.

### [Q-MED-6] `ENTITY_NAME` assignment is order-sensitive across many subclasses
**Files:** `sensor_definitions.py:80-81`, `number.py:65-67`, `switch.py:76-78,213-215`
**Refactor:** Accept `entity_name: str` as an explicit `__init__` parameter on `BaseEveusEntity`; drop the class-attribute contract.

### [Q-MED-7] Nested ternary for connection-quality tier
**File:** `sensor_definitions.py:319-324`
**Refactor:**
```python
_QUALITY_TIERS = ((95,"Excellent"), (80,"Good"), (60,"Fair"), (30,"Poor"))
def _quality_label(p): return next((l for t,l in _QUALITY_TIERS if p>t), "Critical")
```

### [Q-MED-8] `EVSocKwhSensor` and `EVSocPercentSensor` byte-identical except for one method call
**File:** `ev_sensors.py:309-352`
**Refactor:** Parameterize by `calc_fn` (callable) or `calc_method_name: str`. Or merge into one class.

### [Q-MED-9] Two overlapping value-getter helpers in same file
**File:** `sensor_definitions.py:140-167` (`_get_data_value` vs `_make_value_getter`)
**Refactor:** Have `_make_value_getter` delegate to `_get_data_value(updater, key, float)` then apply `precision`/`transform`.

### [Q-LOW] Comment noise (banner comments restating code) — strip in `sensor.py`, `sensor_definitions.py`, `__init__.py`. Keep only intent-documenting comments (e.g. the `systemTime` non-shift explanation).

### [Q-LOW] Inconsistent log levels — `config_flow.py:284-290,327-333,381-387` (ERROR) vs `repairs.py:88` (DEBUG) for identical failure modes. Pick: unexpected = ERROR, user-input invalid = DEBUG.

### [Q-LOW] `STATE_CACHE_TTL` shared between unrelated concepts. Split into `SOC_INPUT_CACHE_TTL` and `INPUT_STATUS_CHECK_INTERVAL` in `const.py`.

### [Q-LOW] `_post_command_refresh_tasks` is a list but `POST_COMMAND_REFRESH_DELAYS = (5,)` has N=1; either shrink to `Task | None` or have tasks self-evict on completion.

### [Q-LOW] `EveusRefreshButton.available` is always True — inherits unused availability machinery from `BaseEveusEntity`. Either inherit a thinner base or accept with a one-line comment.

---

## 3. EFFICIENCY

### [E-HIGH-1] Control entities call `async_write_ha_state` every coordinator tick unconditionally
**Files:** `switch.py:179-198` (`BaseSwitchEntity`), `switch.py:240-246` (`BaseCounterSwitch`), `number.py:155-179`
**Cost:** With 3 switches + 1 number per charger, 4 no-op state writes every 30 s (charging) / 60 s (idle). Each triggers listener fan-out, recorder writes, MQTT, automation re-evaluation. `EveusSensorBase._handle_coordinator_update` (common_base.py:343-351) already gates on `changed`; the control entities lost the gate.
**Fix:**
```python
prev_state = self._attr_is_on
self._attr_is_on = self._resolve_state()
if prev_state != self._attr_is_on or availability_changed:
    self.async_write_ha_state()
```
Same pattern for `EveusCurrentNumber._attr_native_value`.

### [E-HIGH-2] EV sensors recompute baseline & call `are_helpers_available` per-sensor (×3) per tick
**Files:** `ev_sensors.py:211-212, 283-302, 321-329, 344-352, 379-406`
**Cost:** Each `EVSocKwhSensor`, `EVSocPercentSensor`, `TimeToTargetSocSensor` has its own `_energy_baseline`/`_baseline_initial_soc` and independently invokes the helper-state walk. They can briefly disagree on the baseline.
**Fix:** Move baseline tracking onto `CachedSOCCalculator` (one shared instance via runtime_data) or compute once on the coordinator and expose via `updater.processed`. Sensors become thin readers.

### [E-HIGH-3] `OptimizedEveusSensor._update_extra_state_attributes` builds new dict every tick with floating-point drift → state-change false positives
**File:** `sensor_definitions.py:116-133`
**Cost:** `connection_quality` attrs include `latency_avg` formatted with 2-decimal float drift; dict almost always differs tick-to-tick, forcing a write each tick even when nothing meaningful changed.
**Fix:** Snap latency to 1 decimal or 50 ms band; cache the computed attrs dict reference and reuse on no-change.

### [E-HIGH-4] `InputEntitiesStatusSensor._check_inputs` rebuilds attrs twice per tick
**File:** `ev_sensors.py:535-573`
**Cost:** Calls `_update_extra_state_attributes()` inline (L564, L573); base `_handle_coordinator_update` then calls it again. Two full rebuilds (iterating `REQUIRED_INPUTS`, generating nested help text) every tick. This is the most expensive sensor in the integration.
**Fix:** Remove inline `_update_extra_state_attributes()` calls inside `_check_inputs`; let the framework rebuild attrs once.

### [E-MED-1] Manifest missing modern fields
**File:** `manifest.json`
**Fix:** Add `"quality_scale": "silver"` and `"loggers": ["custom_components.eveus"]`.

### [E-MED-2] `_on_input_changed` fan-out fires per BaseEVHelperSensor on every helper slider tick
**File:** `ev_sensors.py:242-260`
**Fix:** Subscribe once on the coordinator (or via shared `CachedSOCCalculator`) and dispatch a single signal to all EV sensors. Or compare event `new_state` to last cached value before propagating.

### [E-MED-3] `_build_device_info` runs ~30× during setup, then per-tick × per-entity until firmware appears
**Files:** `common_base.py:55, 124-131, 139-173`
**Fix:** Build device_info once in `async_setup_entry` and stash on `EveusRuntimeData`; entities reference it. Register a single coordinator listener for firmware-finalize.

### [E-MED-4] Per-entity `_get_data_value` walks on every tick across ~25 sensors
**Files:** `sensor_definitions.py:140-167`
**Fix:** After successful poll, coordinator populates `processed: dict[str, Any]` (key → cleaned float/int/str). Sensors read directly. Cuts ~25 redundant `float`/`isfinite`/`round` calls per tick.

### [E-MED-5] `connection_quality` dict rebuilt twice per tick (value + attrs sensors)
**Files:** `common_network.py:100-121`, `sensor_definitions.py:298-329`
**Fix:** Cache on coordinator; invalidate inside `_record_success`/`_record_failure`.

### [E-LOW] `InputEntitiesStatusSensor` puts multi-line YAML help text into attributes (recorder persists). Strip to entity IDs only; expose help via service/`repairs`.

### [E-LOW] `aiohttp.ClientTimeout` constructed every poll/command — promote to module-level constant (`common_network.py:282`, `common_command.py:94`).

### [E-LOW] `RateLog` eviction is O(N) per insert past `max_keys` — swap for `OrderedDict` + `popitem(last=False)` if ever scaled (`utils.py:41-44`).

---

## 4. APPLY ORDER (recommended)

Phase 1 — correctness (ship first):
1. **HIGH-1** `state_class` fixes (`sensor_definitions.py:373`, `ev_sensors.py:317`)
2. **HIGH-2** `calculate_soc_percent` sanitize (`utils.py:228-232`)
3. **HIGH-3** Offline cadence preservation in `_tune_update_interval` (`common_network.py:244-260`)
4. **HIGH-4** `format_duration` None-safety (`utils.py:150`)
5. **MEDIUM-1** round vs truncate current setpoint (`number.py:118`)
6. **MEDIUM-2** `async_shutdown` awaits cancelled tasks (`common_network.py:199-202`)
7. **LOW-1** `configuration_url` scheme propagation (`utils.py:146`)
8. **LOW-2/3** monetary device_class, sensor-key uniqueness assertion

Phase 2 — efficiency wins (high user impact):
9. **E-HIGH-1** Gate `async_write_ha_state` in control entities
10. **E-HIGH-2** Shared SOC/energy baseline on `CachedSOCCalculator`/coordinator
11. **E-HIGH-3** Snap floating attrs to stable buckets
12. **E-HIGH-4** Remove duplicate `_update_extra_state_attributes` calls in `_check_inputs`
13. **E-MED-1** Manifest hygiene
14. **E-MED-4** Coordinator-side `processed` dict

Phase 3 — refactors (touch many files; do behind tests):
15. **Q-HIGH-1** Template-method `_handle_coordinator_update`
16. **Q-HIGH-2** Unified `_resolve_controlled_value`
17. **Q-HIGH-3** Drop duplicated availability state
18. **Q-HIGH-4** `DK` constants for device JSON keys
19. **Q-HIGH-5** Symbolic constants for state/rate/ground
20. **Q-HIGH-6** Delete legacy alias properties
21. **Q-MED-2** Sweep dead code (`EveusConnectionError`, `EveusDiagnosticSensor`, `get_cached_data_value`, `_success_count`, `runtime_data.title`)
22. **Q-MED-3** Break `__init__`↔`config_flow` cycle via `types.py`
23. **Q-MED-4** `BaseCounterSwitch` → `ButtonEntity`
24. **Q-MED-5..9** Remaining quality items

Each phase should pass `pytest tests/` before moving on. Bump `manifest.json` version and add a `CHANGELOG.md` entry per the project's release convention.
