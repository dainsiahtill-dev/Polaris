# POL-19 TS5110 owned config closure

mode: implementation. Owner: 修复内核与语言适配工程师. Root owns independent acceptance.
Activated by issue_status_changed; POL-31 read-only API observation: done, approved.
External engineering evidence only; no runtime or project completion authority.

## Pre-fix assumptions and evidence

Public normalization produces `typescript_ts5110` and the exact diagnostic path.
The real coverage query returns `known_rule_matched=false`. Existing
`_build_typescript_tsconfig_lib_plan` reads only root tsconfig and has no TS5110
condition. Registry rule `typescript.tsconfig_lib` does not list TS5110.
Earlier diagnostic normalization is already dirty and must remain untouched.

## Architecture and boundaries

Public diagnostic / coverage / Plan / materialization probe consume the existing
runtime registry and tsconfig_lib dispatcher. A private TS5110 branch validates
canonical config identity and strict JSON, then emits a module-only `json_set`.
Existing PatchComposer, policy gate, executor, hash checks and revalidation remain
the sole downstream path. No new public facade, source_tool, legacy host or stub.

Only product edits: registry/_rules_typescript.py and
typescript_syntax/config_scaffold.py under director.runtime repair_kernel.
Only new test: director.runtime/tests/test_ts5110_owned_config_frontier.py.
Only documentation edits: this blueprint and the named verification card.
Evidence/checkpoints stay in the authorized ts5110-owned-config directory.
Generated targets, other dirty work, contracts, services, Factory, QA and Paperclip
write APIs are forbidden. No dependency installation, provider/network call,
branch, worktree, stage, commit, subagent or service operation.

## Pre-mortem

Do not infer root from pathless compiler output. Do not use stale raw path in
place of typed canonical identity. Reject nonconfig paths, ambiguous paths,
missing configs, inherited options, extends/references, malformed/JSONC/duplicate
JSON keys, contradictory diagnostics/options, already-correct config and stale
physical content. Exact per-config diagnostics cannot authorize another owner.
Preserve strict, lib, target and every other option. Mixed unrelated diagnostics
must not trigger extra config changes under this TS5110 branch.

## Verification plan

RED: root and nested NodeNext/Node16 raw and typed public normalization, coverage,
Plan and materialization plan-probe. Negative cases exercise fail-closed behavior.
GREEN: same tests, unit-injected writer through existing policy gate and physical
hash check, real local tsc on private authored fixtures, revalidation and receipt
hashes. Unit receipts do not prove production ProjectArtifactReceipt authority.
Regression: new frontier, existing config tests, contract 1-7, every frontier,
C++ normalization. Use existing CPython 3.13 venv, no bytecode/pytest cache,
private basetemp, UTF-8 and RTK. Record selector counts and actual exit codes.
Ruff/mypy/format/diff checks report baseline debt; no whole-file formatting.
Whole-project, release and fresh isolated validation remain separate Root work.

## Complexity and rollback

Linear scan of diagnostics and JSON payloads, with one serialized patch per exact
config; memory linear in supplied configs. Rollback uses existing executor.
Root can remove only this bounded diff; no automatic rollback or git mutation.

## Implementation checkpoint and required scope decision

The exact standalone config branch now closes public coverage and planning and
emits only module json_set with a before hash. NodeNext/Node16 root/nested private
compiler probes execute through the existing policy gate and unit-injected writer,
then revalidate with tsc 5.9.3 (exit 2 before, exit 0 after). No production receipt.

Required ambiguity refusal is incomplete. Public Plan accepts two input keys
`tsconfig.json` and `./tsconfig.json` with different content in either insertion
order. `typescript_runtime.py:573` normalizes before the config planner, and its
`_normalize_base_files` dictionary comprehension at line 1248 silently collapses
keys. Run also pre-normalizes at line 1188. This information cannot be recovered
from the authorized config_scaffold branch. Two failing frontier assertions are
retained; no xfail, skip or weaker assertion.

Root/CTO must designate one owner and explicitly extend precise product scope to
`src/backend/polaris/cells/director/runtime/internal/repair_kernel/typescript_runtime.py`
for collision detection before loss of raw input identity on Plan and Run (raw
and typed routes), preserving existing no-plan/public error semantics. Inspect
the later normalization in typescript_syntax/dispatch.py before deciding whether
that file needs separate authorization. Neither file is modified here.
Release and fresh isolated verification must wait for this closure and independent
review; the current change is not accepted or all-green.
