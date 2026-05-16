# Audit Findings v2 — Eveus Integration (graph-informed)

Graph: 830 nodes · 1501 edges · 70 communities  
God nodes: `EveusUpdater` (49 edges), `CachedSOCCalculator` (26), `EveusCurrentNumber` (26), `BaseEveusEntity` (24)  
Bridge nodes: `EveusSensorBase` (10 communities), `BaseEveusEntity` (11), `EveusUpdater` (7)

---

## HIGH — Bug or serious log noise

### [H-1] `utils.py:283` — `_LOGGER.error` in `calculate_remaining_time`

Every other error path in `utils.py` uses `_LOGGER.debug`. Line 283 uses `_LOGGER.error`:

```python
except Exception as err:
    _LOGGER.error("Error calculating remaining time: %s", err, exc_info=True)
    return "unavailable"
```

This is the only `.error` call in `utils.py`. Transient failures (e.g. helper cache miss during startup) generate ERROR-level noise in the HA log visible to users.

**Fix:** Change `_LOGGER.error` → `_LOGGER.debug` at `utils.py:283`.

---

### [H-2] `config_flow.py:284,287,327,330,381,384` — 6× `_LOGGER.error` on user validation failures

Config flow catches `InvalidInput` and `InvalidDevice` and logs at `.error` level:

```python
# line 284
_LOGGER.error("Invalid input: %s", str(err))
# line 287
_LOGGER.error("Invalid device: %s", str(err))
# ... same pattern at 327, 330, 381, 384
```

These are normal user events — wrong IP, wrong credentials, unrecognized device. HA's config flow UI already displays the failure to the user. An `.error` here means every typo in the setup form generates a permanent ERROR entry in the HA log. The convention elsewhere in the integration is `.debug` for validation failures.

**Fix:** Change all 6 occurrences to `_LOGGER.debug`.

---

## MEDIUM — Correctness concern or silent double-work

### [M-1] `sensor.py:62-64` — `_LOGGER.error` before `raise` = double ERROR log

```python
except Exception as err:
    _LOGGER.error("Error setting up sensors for %s: %s", entry.title, err, exc_info=True)
    raise
```

When an exception propagates out of `async_setup_entry`, HA's platform loader logs it again at ERROR level. This creates duplicate noise. Either log without re-raising (catch-and-handle) or re-raise without logging (let HA own the log entry).

**Fix:** Remove the `_LOGGER.error` line and just `raise`, or downgrade to `_LOGGER.debug` before raising.

---

### [M-2] `ev_sensors.py:291-292` — `are_helpers_available` called as cache-warming side effect in `_get_energy_charged`

```python
hass = getattr(self, "hass", None)
if hass is not None:
    self._soc_calculator.are_helpers_available(hass)  # return value discarded
initial_soc = self._soc_calculator.initial_soc
```

`are_helpers_available` is called here purely to update `initial_soc` in the cache. Its boolean return value is discarded. A method named `are_X_available` is a query; calling it for a write side-effect is confusing and breaks the principle of least surprise.

**Fix:** Either call the private `_update_input_cache(hass)` directly (cache warming is the intent), or add a named `_warm_cache(hass)` method.

Also: `getattr(self, "hass", None)` — `self.hass` is always set after `async_added_to_hass`; this guard is over-defensive and hides potential bugs. Replace with plain `self.hass`.

---

### [M-3] `ev_sensors.py:564` — `_check_inputs` calls `_update_extra_state_attributes` internally, but coordinator update calls it again

Graph edge INFERRED: `_update_extra_state_attributes` ↔ `_check_inputs`.

```python
# InputEntitiesStatusSensor._check_inputs
def _check_inputs(self) -> None:
    ...
    self._update_extra_state_attributes()  # line 564 and 572

# EveusSensorBase._handle_coordinator_update (inherited)
def _handle_coordinator_update(self) -> None:
    ...
    value_changed = self._update_native_value()  # calls _get_sensor_value → _check_inputs
    attributes_changed = self._update_extra_state_attributes()  # called AGAIN here
```

On every coordinator update where the cache has expired, attributes are computed twice:
1. From `_check_inputs()` (triggered by `_get_sensor_value()`)
2. From `_handle_coordinator_update()` directly

**Fix:** Remove the `self._update_extra_state_attributes()` calls from `_check_inputs`. Let `_handle_coordinator_update` own the attribute update cycle, which is already the correct separation of concerns.

---

### [M-4] `switch.py:201-246` — `BaseCounterSwitch.async_turn_off` not implemented

`BaseCounterSwitch` overrides `async_turn_on` to do nothing (read-only counter), but does NOT override `async_turn_off`. It falls through to `SwitchEntity.async_turn_off`, which calls `self.turn_off()` and raises `NotImplementedError` if that's not defined.

Only `EveusResetCounterASwitch` (subclass) implements `async_turn_off`. In production, only the subclass is instantiated (`async_setup_entry` uses `EveusResetCounterASwitch`), so this is not a runtime bug — but it is a dangerous design gap that breaks the Liskov Substitution Principle.

**Fix:** Add `async def async_turn_off(self, **kwargs: Any) -> None: pass` to `BaseCounterSwitch`.

---

## LOW — Code smell, type annotation, or minor efficiency

### [L-1] `switch.py:86-110` — compatibility alias comment is misleading

```python
@property
def _optimistic_state(self) -> bool | None:
    """Compatibility alias for older tests and diagnostics."""
    return self._optimistic_value
```

Tests (`test_control_entities.py`) actively use `_optimistic_state`, `_optimistic_state_time`, and `_last_device_state`. They are not "older tests" — they are current. The comment misleads future readers into thinking the aliases could be removed.

**Fix:** Update docstrings to: `"Test-facing alias for the canonical _optimistic_value attribute."`.

---

### [L-2] `sensor_definitions.py:183-184` — counter cost sensors missing `_div100` — verify

```python
get_counter_a_cost = _make_value_getter("IEM1_money", precision=2)     # no _div100
get_counter_b_cost = _make_value_getter("IEM2_money", precision=2)     # no _div100
get_primary_rate_cost = _make_value_getter("tarif", precision=2, transform=_div100)  # ÷100
```

Rate values (`tarif`, `tarifAValue`, `tarifBValue`) are stored in cents and require `_div100`. Counter money values (`IEM1_money`, `IEM2_money`) do not use the transform. If the charger encodes those in cents too, displayed counter costs would be 100× too high.

**Action required:** Verify against charger API spec whether `IEM1_money` and `IEM2_money` are in cents or base unit. If cents, add `transform=_div100`.

---

### [L-3] `sensor_definitions.py:63` — `SensorSpec.state_class` typed as `Optional[str]` but receives `SensorStateClass` enum

```python
@dataclass(frozen=True)
class SensorSpec:
    state_class: Optional[str] = None  # receives SensorStateClass.MEASUREMENT etc.
```

Works at runtime (HA accepts both), but fails type checkers. The type annotation should reflect reality.

**Fix:** Change to `state_class: Optional[SensorStateClass | str] = None`.

---

### [L-4] `sensor.py:51` — `update_before_add=False` means sensors start as `None`

```python
async_add_entities(sensors, update_before_add=False)
```

With `False`, entities are added without an initial coordinator push, so all sensors display `None` until the next scheduled poll (up to 30s). With `True`, HA triggers a coordinator refresh before entities are surfaced in the UI.

The current setting is conservative (avoids startup latency), but creates a brief window where the UI shows all-unknown sensors. Whether this is intentional is unclear.

**Fix:** Consider `update_before_add=True` for better first-impression UX, or add a comment explaining why `False` is preferred here.

---

### [L-5] Graph: `CachedSOCCalculator` properties have ≤1 graph edge — untested in isolation

Properties `battery_capacity`, `soc_correction`, `target_soc`, `initial_soc` in `CachedSOCCalculator` are only tested indirectly through `TimeToTargetSocSensor`. The graph confirms they have no direct test edges.

**Fix:** Add unit tests that:
1. Build a `CachedSOCCalculator` with a warmed cache (via `_update_input_cache`)
2. Assert each property returns the expected cached value
3. Assert stale cache returns previous values (not None) per the TTL invariant

---

### [L-6] Graph: 268 isolated nodes (32%) — mostly docstrings, but 119 are code nodes

The 119 isolated code nodes include many properties/methods that are exercised only at runtime and not by any test with a direct assertion. Notable examples:
- `EveusUpdater.is_likely_offline` — tested indirectly through interval tests; no explicit property test
- `EveusUpdater.connection_quality` — tested through sensor; no direct dict-key assertion test
- `EveusUpdater.consecutive_failures` property on `CommandManager`

None of these are bugs, but they represent coverage blind spots that graph structure surfaced.

---

## Apply Order

1. H-1, H-2 — logging level fixes (one-liners, no behavior change)
2. M-1 — sensor.py double-log fix
3. M-4 — `BaseCounterSwitch.async_turn_off` stub
4. M-2 — `_get_energy_charged` cache warming refactor
5. M-3 — remove `_update_extra_state_attributes` call from `_check_inputs`
6. L-1 — comment fix
7. L-3 — type annotation fix
8. L-2 — verify counter cost transform (needs device API check before code change)
9. L-4 — decide on `update_before_add` setting
