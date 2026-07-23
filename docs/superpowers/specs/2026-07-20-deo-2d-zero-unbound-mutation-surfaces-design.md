# DEO-2D Zero-Unbound Mutation Surfaces Design

## Status and authority

This design implements the already locked DEO-2 blueprint. User authorization
requires autonomous execution without another review pause. DEO-2D is the only
active bucket. DEO-3, DEO-4, pre-bench, Provider calls, and Bench remain
`not_schedulable`.

## Baseline

DEO-2C removed all 31 `executor_factory=DirectorToolExecutor` repair injections.
The remaining production inventory is seven direct constructors and seven
matching physical calls:

- three in `post_execution_repair_bridge.py`;
- three in `quality_gate.py`;
- one in `execution.py`.

The canonical physical path already exists:

```text
ToolBatchRuntime
  -> TaskRuntime sealed inventory / ready / claim
  -> directed-effect fence and current-policy revalidation
  -> roles.adapters directed_effect_mutation_port
  -> one DirectorToolExecutor
  -> guarded KernelOne compare-and-swap
  -> non-authoritative DEO-2C receipt
```

DEO-2D must remove every other physical mutation edge. Zero textual constructors
is not enough: unreachable private patch helpers, callback writers, direct
`execute_tool` calls, and `StrictOperationApplier.apply` calls are also unbound
surfaces.

## Considered approaches

### A. Allowlist existing legacy writers

Small diff, but leaves adapter callbacks as physical authorities. It defeats the
sealed inventory and per-call claim invariant. Rejected.

### B. Add grants to every legacy helper

Preserves old call shapes, but duplicates claim/fence/policy consumption across
many helpers and makes the adapter a second lifecycle owner. Rejected.

### C. Migrate every live repair to the deferred kernel boundary and delete dead writers

Chosen. Registered deterministic repairs project immutable effects through
`director.runtime`; roles.kernel schedules the one counted follow-up batch;
the adapter never writes synchronously. Raw model bodies and textual patch
protocols remain non-authoritative and fail closed.

## Detailed design

### Post-execution repair branches

The C++, Java accessor-alias, and Java test-dependency branches currently fall
back to local `DirectorToolExecutor` writers when no convergence verifier is
provided. They will always call `run_runtime_repair_with_director_tools`, require
an exact `TaskRuntimeExecutionAttemptIdentityV1`, set `max_rounds=1`, and return
the typed deferred request. Missing attempt identity produces the existing
`deo_deferred_repair_attempt_required` failure and zero effect.

### Quality-repair branches

Three direct-write fallbacks have different treatment:

1. A raw LLM file body is not an authoritative tool action. It will produce a
   typed blocked audit result and never become a write.
2. `ModuleNotFoundError` alias repair already maps to executable source tool
   `deterministic_python_package_shadow_bridge_repair`; it will use the central
   deferred bridge.
3. Missing `requirements.txt` is currently a coverage gap. The existing
   `deterministic_runtime_dependency_repair` source tool will gain a generic
   Python requirements-manifest rule. The plan may create only
   `requirements.txt`, only from explicitly named dependency evidence, and only
   through a `write_file` repair operation. Coverage, plan, receipt, and
   revalidation remain Director Runtime owned.

### Text patch executor

`DirectorPatchExecutor.execute_tools` already rejects textual tool protocols and
PATCH/Markdown fallbacks. Its old private application helpers are unreachable but
still physically callable and still instantiate a mutation-capable executor.
DEO-2D removes that executor field and the private physical helpers. Parsing and
blocked audit projection remain; physical patch application does not.

### Architecture fence

A repository AST test will enforce:

- exactly one production `DirectorToolExecutor(...)` construction, in
  `directed_effect_mutation_port.py`;
- exactly one production `.execute_tool(...)` physical call, in the same port;
- no `executor_factory` repair injection;
- no callback `writer`/`editor`/`deleter` passed to Director repair execution;
- no private patch helper capable of applying or writing project files;
- no raw quality fallback invoking a physical executor;
- dependency direction remains `director.runtime <- roles.kernel <- roles.adapters`.

Test doubles may model the contract but cannot bypass the same claim/fence
preconditions in production wiring.

## Failure semantics

- Missing/stale attempt: typed deferred failure, zero effect.
- Unsupported or unplannable source tool: existing public repair failure, zero effect.
- Raw/text patch output: protocol-disabled audit result, zero effect.
- Second repair round: `deo_multi_round_repair_requires_receipt_close`, zero effect.
- Effect after claim without durable receipt: remains DEO-3 recovery work; DEO-2D
  does not close the parent or claim terminal success.

## Verification

TDD covers coverage-gap-to-runtime-plan promotion, all seven removed direct
surfaces, exact deferred request projection, raw fallback denial, dead helper
removal, and the AST singleton fence. Closure requires full Director Runtime,
roles.adapters, roles.kernel, TaskRuntime, KernelOne, architecture, Ruff,
format, mypy, compileall, YAML/catalog, and diff gates plus independent
specification and quality/security review. Provider and Bench counts remain zero.

## Self-review

- No placeholder or deferred design decision remains.
- Scope is limited to DEO-2D mutation-surface closure.
- Durable authoritative receipts and later rounds remain DEO-3 only.
- No target-project behavior or business code enters Polaris.
