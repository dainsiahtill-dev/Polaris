# ContextOS Thread Model Documentation

**Document Version**: 1.0.0
**Created**: 2026-04-11
**Status**: Complete
**Scope**: `polaris/kernelone/context/context_os/`

---

## 1. Overview

This document describes the thread model and concurrency characteristics of ContextOS (`StateFirstContextOS`), the canonical state-first session context engine. It provides guidance for future maintainers on what is thread-safe, what requires external synchronization, and how to extend safely.

---

## 2. Thread Safety Classification

### 2.1 Thread-Safe Components

The following components are **inherently thread-safe** due to their design:

| Component | Location | Reason |
|-----------|----------|--------|
| All Model Classes | `models.py` | `@dataclass(frozen=True, slots=True)` - immutable |
| `ContextOSSnapshot` | `models.py:804` | Frozen dataclass with immutable fields |
| `ContextOSProjection` | `models.py:862` | Frozen dataclass, `compress()` returns new instance |
| `TranscriptEvent` | `models.py:180` | Frozen dataclass |
| `ArtifactRecord` | `models.py:230` | Frozen dataclass |
| `EpisodeCard` | `models.py:498` | Frozen dataclass |
| `BudgetPlan` | `models.py:569` | Frozen dataclass with `validate_invariants()` |
| `StateFirstContextOSPolicy` | `models.py:958` | Frozen dataclass with `from_env()` factory |
| `DialogActClassifier` | `classifier.py:30` | Stateless - only class constant `_USE_FULLMATCH` |
| Module Constants | `runtime.py:61-63` | `MAX_INLINE_CHARS`, `MAX_STUB_CHARS` - immutable |

### 2.2 NOT Thread-Safe Components

| Component | Location | Risk |
|-----------|----------|------|
| `StateFirstContextOS` instance | `runtime.py:128` | Concurrent `project()` calls cause data races |
| `_StateAccumulator` | `helpers.py:235` | Mutable list and dict (`_entries`, `_last_by_path`) |
| Instance attributes | `runtime.py:146-161` | `_dialog_act_classifier`, `_resolved_context_window` |

---

## 3. Data Flow Diagram

```
                    Input                    StateFirstContextOS                 Output
    +-------------------------------------+     +---------------------------+    +------------------+
    | messages: list[dict[str, Any]]       | --> | project(                  | -> | ContextOSProjection|
    | existing_snapshot: ContextOSSnapshot| --> |   messages=...,           |    |   .snapshot      |
    +-------------------------------------+     |   existing_snapshot=...,   |    |   .active_window |
                                               | )                         |    |   .run_card      |
                                               +---------------------------+    +------------------+
                                                           |
                    +---------------------------------------+--------------------------------+
                    |                                       |                                |
                    v                                       v                                v
         +------------------+                   +---------------------+           +------------------+
         | _merge_transcript |                   | _canonicalize_and   |           | _plan_budget     |
         | (line 679)       |                   | _offload (line 825)|           | (line 1199)      |
         +------------------+                   +---------------------+           +------------------+
                    |                                       |                                |
                    v                                       v                                v
         +------------------+                   +---------------------+           +------------------+
         | Returns tuple of |                   | Returns:           |           | Returns:        |
         | TranscriptEvent  |                   | - transcript tuple |           | BudgetPlan      |
         | (immutable)      |                   | - artifacts tuple  |           | (frozen)        |
         +------------------+                   | - PendingFollowUp   |           +------------------+
                                                +---------------------+
                                                            |
                    +---------------------------------------+--------------------------------+
                    |                                       |                                |
                    v                                       v                                v
         +------------------+                   +---------------------+           +------------------+
         | _patch_working_  |                   | _seal_closed_      |           | _collect_active_ |
         | state (line 1052)|                   | episodes (line 1361)|           | window (line1253)|
         +------------------+                   +---------------------+           +------------------+
                    |                                       |                                |
                    v                                       v                                v
         +------------------+                   +---------------------+           +------------------+
         | Returns:         |                   | Returns:           |           | Returns:         |
         | WorkingState     |                   | episode_store      |           | active_window    |
         | (frozen fields)  |                   | (tuple of frozen)  |           | (tuple of frozen)|
         +------------------+                   +---------------------+           +------------------+
```

---

## 4. Shared Mutable State Analysis

### 4.1 StateFirstContextOS Instance Attributes

**Location**: `runtime.py:146-161`

```python
class StateFirstContextOS:
    def __init__(self, ...):
        self.policy = policy or StateFirstContextOSPolicy()           # [1]
        self.domain_adapter = domain_adapter or ...                  # [2]
        self._provider_id = str(provider_id or "").strip()           # [3]
        self._model = str(model or "").strip()                        # [3]
        self._workspace = str(workspace or ".").strip()               # [3]
        self._resolved_context_window: int | None = None              # [4]
        self._dialog_act_classifier: DialogActClassifier | None = None # [5]
        self._cleanup_lock: asyncio.Lock | None = None                 # [6]
```

| Index | Attribute | Mutability | Thread Safety |
|-------|-----------|------------|---------------|
| [1] | `policy` | Set once, never modified | Safe (frozen) |
| [2] | `domain_adapter` | Set once, never modified | Safe (assuming adapter is thread-safe) |
| [3] | `_provider_id`, `_model`, `_workspace` | Set once, never modified | Safe (immutable strings) |
| [4] | `_resolved_context_window` | Lazy cache, written once | Safe under single-thread, unsafe for concurrent `project()` |
| [5] | `_dialog_act_classifier` | Can be set to `None` during `cleanup()` | **UNSAFE** - race between classifier access and cleanup |
| [6] | `_cleanup_lock` | Lazy `asyncio.Lock` | Safe for async cleanup coordination |

### 4.2 Critical Section: cleanup() Method

**Location**: `runtime.py:169-181`

```python
async def cleanup(self) -> None:
    async with self._get_cleanup_lock():    # Line 178
        self._dialog_act_classifier = None  # Line 180 - WRITE
        logger.debug("...")

@property
def dialog_act_classifier(self) -> DialogActClassifier:  # Line 257
    if self._dialog_act_classifier is None:              # Line 259 - READ
        self._dialog_act_classifier = DialogActClassifier()  # Line 260 - WRITE
    return self._dialog_act_classifier
```

**Race Condition Window**:
- Between reading `_dialog_act_classifier` (line 259) and writing it (line 260)
- If `cleanup()` runs concurrently on another task, classifier can be set to `None` mid-access

---

## 5. Thread Safety Invariants

The following invariants MUST be maintained:

### 5.1 Required Invariants

```
INV-1: At most one thread/task may call project() on a given StateFirstContextOS instance at any time

INV-2: cleanup() or close() must be called before discarding a StateFirstContextOS instance

INV-3: After cleanup()/close() returns, the instance must not be used for projection operations

INV-4: All ContextOSSnapshot objects are immutable - never modify after creation

INV-5: DialogActClassifier.classify() is stateless and thread-safe
```

### 5.2 BudgetPlan Invariant

**Location**: `models.py:585-607`

```python
def validate_invariants(self) -> None:
    """Validate BudgetPlan invariants."""
    if self.expected_next_input_tokens > self.model_context_window:
        raise BudgetExceededError(...)
```

This is validated during `project()` at `runtime.py:293-294`:

```python
budget_plan = self._plan_budget(transcript, artifacts)
budget_plan.validate_invariants()  # Raises if violated
```

---

## 6. Known Race Condition Windows

### 6.1 Primary: Concurrent project() Calls

**Risk**: HIGH

```
Thread A: project() starts -> _merge_transcript -> ...
Thread B: project() starts -> _merge_transcript -> ...
Result: Non-deterministic transcript merging, potential sequence gaps
```

**Mitigation**: External synchronization required. Use a lock at the caller level.

### 6.2 Secondary: dialog_act_classifier Access vs cleanup()

**Risk**: MEDIUM

```
Task A: project() -> _canonicalize_and_offload() -> classify() [line 859-860]
Task B: cleanup() -> async with _get_cleanup_lock() -> _dialog_act_classifier = None [line 180]
Result: AttributeError: 'NoneType' object has no attribute 'classify'
```

**Mitigation**: The `cleanup_lock` (lines 163-167) protects this, but the `dialog_act_classifier` property (lines 257-261) does not use the lock for lazy initialization.

### 6.3 Tertiary: _resolved_context_window Cache

**Risk**: LOW (acceptable under INV-1)

```
Thread A: project() -> resolved_context_window [line 217-254] -> cache miss -> compute -> write
Thread B: project() -> resolved_context_window -> cache hit (before A writes)
Result: Thread B uses stale policy default instead of computed value
```

**Note**: This is acceptable because the resolved window is only used for budget calculations and doesn't affect correctness of the core algorithm.

---

## 7. Extension Guidelines

### 7.1 Adding New Instance Attributes

When adding new instance attributes to `StateFirstContextOS`:

1. **Prefer immutable types** (strings, tuples, frozen dataclasses)
2. **If mutable, document thread safety requirements**
3. **Add to the instance attribute table in Section 4.1**

```python
# GOOD: Immutable string
self._session_id = str(session_id or "").strip()

# BAD: Mutable list without protection
self._event_buffer: list[TranscriptEvent] = []  # DANGER: needs lock
```

### 7.2 Adding New Model Classes

All new model classes should be frozen dataclasses:

```python
# GOOD
@dataclass(frozen=True, slots=True)
class NewModel:
    field1: str
    field2: tuple[str, ...]

# AVOID unless absolutely necessary
@dataclass
class MutableModel:  # Document thread safety requirements
    field1: list[str]  # Must be protected by external lock
```

### 7.3 Adding New Methods to StateFirstContextOS

1. Document whether the method is thread-safe
2. If it modifies instance state, ensure thread safety or document the requirement
3. Add to the appropriate section in this document

```python
def new_method(self, ...) -> ...:
    """Thread safety: NOT thread-safe - requires external synchronization.

    Raises:
        RuntimeError: If called concurrently with project()
    """
```

---

## 8. Usage Patterns

### 8.1 Safe: Single-Threaded Usage

```python
# Single thread, single task - SAFE
os = StateFirstContextOS(policy=policy)
projection = os.project(messages=messages)
```

### 8.2 Safe: Async Context Manager

```python
# Using context manager for proper cleanup - SAFE
async with StateFirstContextOS(policy=policy) as os:
    projection = os.project(messages=messages)
# cleanup() called automatically
```

### 8.3 Safe: Explicit Cleanup

```python
# Explicit async cleanup - SAFE
os = StateFirstContextOS(policy=policy)
try:
    projection = os.project(messages=messages)
finally:
    await os.cleanup()
```

### 8.4 UNSAFE: Concurrent project() Calls

```python
# UNSAFE - Data race
async def concurrent_project(os, messages1, messages2):
    # NEVER do this - causes non-deterministic transcript merging
    results = await asyncio.gather(
        os.project(messages=messages1),
        os.project(messages=messages2)
    )
```

### 8.5 SAFE: Protected Concurrent Access

```python
# SAFE - External lock protects concurrent access
lock = asyncio.Lock()

async def safe_concurrent_project(os, messages1, messages2):
    async with lock:
        # Only one project() runs at a time
        projection1 = os.project(messages=messages1)
    async with lock:
        projection2 = os.project(messages=messages2)
    return projection1, projection2
```

---

## 9. Summary

| Category | Status | Notes |
|----------|--------|-------|
| Model classes (frozen dataclasses) | SAFE | Immutable, inherently thread-safe |
| DialogActClassifier | SAFE | Stateless, no mutable state |
| StateFirstContextOS.project() | NOT THREAD-SAFE | Requires external synchronization |
| StateFirstContextOS.cleanup() | SAFE | Uses internal asyncio.Lock |
| _StateAccumulator | NOT THREAD-SAFE | Internal class, used within single project() |
| Module constants | SAFE | Immutable |

**Key Rule**: A single `StateFirstContextOS` instance must not have `project()` called concurrently from multiple threads/tasks. All other components are thread-safe by design.

---

## 10. References

- Main runtime: `polaris/kernelone/context/context_os/runtime.py`
- Models: `polaris/kernelone/context/context_os/models.py`
- Classifier: `polaris/kernelone/context/context_os/classifier.py`
- Helpers: `polaris/kernelone/context/context_os/helpers.py`
