# DEO-2D Zero-Unbound Mutation Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every remaining repair mutation path that can write without the canonical TaskRuntime claim, fence, policy revalidation, and roles.kernel batch boundary.

**Architecture:** `director.runtime` projects immutable repair effects, roles.kernel alone admits and schedules them, and `directed_effect_mutation_port.py` remains the only physical `DirectorToolExecutor` composition root. Legacy raw-body and textual patch writers fail closed; live deterministic repairs return typed deferred requests.

**Tech Stack:** Python 3.12, Director Runtime repair kernel, roles.kernel DEO lifecycle, AST architecture tests, pytest, Ruff, mypy.

---

## Scope lock

- DEO-2D owns the remaining seven direct mutation surfaces and repository-wide zero-unbound-surface proof.
- DEO-3 alone owns durable receipt commit, recovery, parent close, later rounds, and terminal admission.
- Provider calls, Bench, target-project edits, hidden batches, allowlists for legacy writers, and new adapter-owned mutation authority are forbidden.

## File map

- `polaris/tests/architecture/test_deo_2d_zero_unbound_mutation_surfaces.py`: exact AST inventory and singleton physical consumer.
- `polaris/cells/roles/adapters/internal/director/post_execution_repair_bridge.py`: remove three direct writer branches.
- `polaris/cells/roles/adapters/internal/director/quality_gate.py`: replace three direct quality writes with deferred repair or blocked evidence.
- `polaris/cells/roles/adapters/internal/director/execution.py`: delete unreachable physical patch helpers and executor construction.
- `polaris/cells/director/runtime/internal/repair_kernel/generic_hygiene_syntax.py`: requirements manifest plan under existing runtime dependency source tool.
- `polaris/cells/director/runtime/internal/repair_kernel/registry.py`: executable coverage rule for explicit requirements evidence.
- `polaris/cells/director/runtime/tests/test_repair_kernel_precise_edit_and_coverage.py`: coverage RED/GREEN.
- `polaris/cells/director/runtime/tests/test_repair_kernel_contract.py`: plan/effect projection tests.
- `polaris/cells/roles/adapters/tests/test_director_repair_writers.py`: deferred post-repair and zero-executor tests.
- `polaris/cells/roles/adapters/tests/test_director_adapter_pure.py`: raw/text fallback denial and patch-helper removal.

### Task 1: Freeze governance and prove the baseline RED

- [x] **Step 1: Open DEO-2D verification metadata**

Create the verification card, mark DEO-2D active in the blueprint/ledger, and keep every downstream bucket `not_schedulable`.

- [x] **Step 2: Write the failing AST architecture test**

The test must assert the current seven constructors/calls fail the desired singleton invariant:

```python
assert constructor_sites == {"directed_effect_mutation_port.py": 1}
assert execute_sites == {"directed_effect_mutation_port.py": 1}
assert executor_factory_sites == []
assert direct_repair_writer_callbacks == []
assert physical_patch_helper_sites == []
```

- [x] **Step 3: Verify RED**

Run:

```bash
rtk proxy env PYTHONPATH=. python -m pytest polaris/tests/architecture/test_deo_2d_zero_unbound_mutation_surfaces.py -q
```

Expected: FAIL listing `post_execution_repair_bridge.py`, `quality_gate.py`, and `execution.py` as offenders.

### Task 2: Remove the three post-execution direct writers

- [x] **Step 1: Add failing adapter tests**

For C++, Java accessor alias, and Java test dependency, use a constructor spy and assert each function returns one `DeferredDirectorRepairRequestV1`, preserves the exact source tool/attempt/allowed paths, and performs zero write.

- [x] **Step 2: Verify RED**

Run the three selected tests. Expected: the constructor spy fires in each legacy branch.

- [x] **Step 3: Implement the minimal migration**

Delete each local executor/writer and call:

```python
run_runtime_repair_with_director_tools(
    adapter,
    workspace_path=workspace_path,
    task_id=task_id,
    source_tool=source_tool,
    execution_attempt=execution_attempt,
    base_files=base_files,
    allowed_paths=tuple(base_files),
    advisor_notes=advisor_notes,
    use_editor=False,
    convergence_verifier=convergence_verifier,
    max_rounds=1,
)
```

Propagate `execution_attempt` through Java callers. Do not infer or mint an attempt.

- [x] **Step 4: Verify GREEN**

Run post-execution schedule/bridge tests and the architecture test. Expected: three constructor/call offenders removed.

### Task 3: Promote explicit requirements evidence into Director Runtime coverage

- [x] **Step 1: Write the coverage RED test**

Use `query_director_repair_coverage` with:

```text
requirements.txt must exist at project root; required dependencies: requests
```

Assert the current report is uncovered before implementation, then define the target assertions: known rule matched, executable runtime plan matched, source tool `deterministic_runtime_dependency_repair`.

- [x] **Step 2: Write the plan RED test**

Plan the existing runtime dependency source tool with empty `requirements.txt` absent from `base_files`. Assert one `write_file` effect targeting only `requirements.txt`, `exists_before=False`, and content `requests\n`.

- [x] **Step 3: Verify RED**

Run both selected Director Runtime tests. Expected: coverage unmatched and no repair plan.

- [x] **Step 4: Implement minimal registry and planner support**

Register one executable generic Python requirements-manifest rule. Extend `build_runtime_dependency_plan` to parse only explicit dependency names from the diagnostic and emit one `RepairOperation(kind="write_file", path="requirements.txt", content=...)`. Reject ambiguous names, options, URLs, paths, markers, and diagnostics without explicit dependency evidence.

- [x] **Step 5: Verify GREEN and negative cases**

Prove valid names plan; malformed/ambiguous evidence does not plan; package.json behavior remains unchanged; public Plan/Run still reject unsupported source tools.

### Task 4: Remove the three quality-gate direct writes

- [x] **Step 1: Add failing quality tests**

Assert raw single-target bodies return `raw_single_target_body_not_authoritative` with zero physical executor construction. Assert missing requirements and module alias evidence return typed deferred requests under the exact TaskRuntime attempt.

- [x] **Step 2: Verify RED**

Run selected quality tests. Expected: current direct executor path writes or the constructor spy fires.

- [x] **Step 3: Implement deferred/blocked projections**

Remove all three `DirectorToolExecutor` calls. Raw bodies become blocked audit evidence. Requirements use `deterministic_runtime_dependency_repair`; module aliases use `deterministic_python_package_shadow_bridge_repair`; both call the central deferred bridge with an exact attempt extracted from the typed authority in context.

- [x] **Step 4: Verify GREEN**

Run quality, materialization boundary, adapter repair, and deferred follow-up suites. Expected: zero direct write and one visible deferred batch when plannable.

### Task 5: Delete the unreachable physical PATCH executor

- [x] **Step 1: Write failing denial/structure tests**

Assert `DirectorPatchExecutor` has no `_tool_executor`, `_execute_patch_file_format`, `_apply_protocol_operations`, or `_apply_single_patch`; textual/Markdown/PATCH input still returns existing protocol-disabled evidence.

- [x] **Step 2: Verify RED**

Run selected patch executor tests. Expected: physical fields/helpers still exist.

- [x] **Step 3: Delete physical helpers and constructor**

Keep only extraction, normalization, and blocked audit projection. Remove `StrictOperationApplier`, physical file application, realtime mutation broadcasts from the fallback, and the `DirectorToolExecutor` import/construction.

- [x] **Step 4: Verify GREEN**

Run all patch executor and realtime-event tests. Update legacy tests to assert fail-closed behavior, never physical writes.

### Task 6: Prove zero unbound surfaces

- [x] **Step 1: Run the AST fence**

Expected exact inventory: one constructor and one physical call, both in `directed_effect_mutation_port.py`; zero factories/callback writers/private patch applicators.

- [x] **Step 2: Run behavioral proof**

Cover missing/stale/cross-attempt authority, raw output, unknown repair, second round, claimed rollback, and real composition receipt. Every denial must show zero physical effect.

- [x] **Step 3: Run broad gates**

Run full Director Runtime, roles.adapters, roles.kernel, TaskRuntime, KernelOne guarded FS/tool execution, architecture, Ruff, format, mypy, compileall, YAML/catalog hard-fail, public import smoke, and `git diff --check`. Provider/Bench counts remain zero.

- [x] **Step 4: Independent reviews**

Require `PASS/PASS` with no Critical/Important findings and explicit confirmation that DEO-3 ownership was not implemented or weakened.

### Task 7: Close only DEO-2D

- [x] **Step 1: Synchronize metadata**

Close DEO-2D in blueprint, ledger, Cell metadata, graph catalog, verification card, and `memory/MEMORY.md` with fresh evidence.

- [x] **Step 2: Schedule DEO-3 only**

DEO-3 becomes the sole next bucket. DEO-4, pre-bench, Provider, and Bench remain `not_schedulable`.

## Self-review

- Blueprint sections 10 and 11 map to Tasks 1-7.
- No `TBD`, `TODO`, unqualified future work, or adapter authority exists.
- The only new repair behavior is generic, coverage-first, Director Runtime owned.
- DEO-3 durable settlement remains out of scope.
