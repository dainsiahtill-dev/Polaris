# engineering_tools sandbox argv keyword passing

Date: 2026-11-09. Task: ENGINEERING-TOOLS-SANDBOX-ARGV-KWARGS. Classification: one_off.

## Problem and evidence

`EngineeringCommandRuntime._process` passed `capture_output_root` to
`build_workspace_sandbox_argv` through a conditional dict unpack:

```python
**({"capture_output_root": capture_output_root} if capture_output_root is not None else {})
```

mypy types that conditional expression as `dict[str, Path]` and cannot match the
`capture_output_root` key back to the `capture_output_root: Path | None` parameter,
so it reported `engineering_tools.py:471: Argument 8 ... incompatible type
"**dict[str, Path]"; expected "CommandCacheMount | None"`. The call appears twice
(the no-cache and the command-cache branches). This is a static-analysis false
positive; the runtime keyword binding was always correct.

## Architecture and responsibilities

Owner remains `roles.runtime`. `build_workspace_sandbox_argv` already declares
`capture_output_root: Path | None = None`, so passing the value directly is
behaviorally identical to the conditional unpack: an explicit `None` is treated
the same as an omitted argument because the builder branches on
`capture_output_root is not None`. No contract, effect, sandbox policy, or wire
change.

## Assumptions and pre-mortem

- Passing `capture_output_root=None` explicitly is identical to omitting it (the
  builder only acts when it is not `None`).
- Risk zone: the command-cache branch must keep passing `command_cache=command_cache`
  unchanged; only the `capture_output_root` passing style changes.

## Complexity and rollback

Removes two dict allocations on the command path: O(1) time/space reduction.
Roll back by restoring the conditional unpack; no behavior, persistence, or wire
change.

## Verification plan

- `mypy` on `engineering_tools.py`: one error to zero.
- `ruff check` + `ruff format --check` on the file.
- Command-execution regression covering both `_process` call sites:
  `test_engineering_command_cache.py`, `test_engineering_command_cache_authority.py`,
  `test_engineering_command_contract.py`, `test_engineering_command_timeout.py`,
  `test_engineering_primitives.py`.

## Results

- RED: `engineering_tools.py:471` mypy false positive reproduced on pristine HEAD.
- GREEN: mypy clean on the file; ruff check/format pass.
- Regression: 63 passed across the command-execution suites (both call sites).
- Limitations: static-shape correction only; full repository pytest not run here.
