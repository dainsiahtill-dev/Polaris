# Engineering Event Selection Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans; follow the single interface migration below.

**Goal:** Avoid decoding irrelevant engineering events without changing authority, recovery, verification or model input.

**Architecture:** Add optional kind/role filtering to the existing Cell-internal store query and migrate four measured consumers. Default all-history behavior and the one SQLite owner stay unchanged.

**Tech Stack:** Existing Python, sqlite3, pytest, KernelOne/KFS; no dependency or schema change.

**Spec:** `docs/blueprints/POLARIS_ENGINEERING_EVENT_SELECTION_20261022.md`.

## Global Constraints

- Work directly on main; preserve unrelated changes, UTF-8; all temporary evidence below ~/.polaris.
- No target changes, native restart/replay, model/timeout/permissions changes or new ledger/controller.
- Keep all matching history and original Python identity comparisons. Do not optimize manifest or unrelated cold queries in this batch.

## Review Focus

- Empty filters still check original run and physical identity; they never widen a selection.
- SQLite affinity must not turn a string seq into a valid native turn.
- Duplicate calls and copied messages across frames must preserve visual ambiguity/current inputs.
- A fresh interpreter must read the original facts without replaying interrupted effects.
- Selected corrupt payload fails; excluded payload is not a claim of global journal health.

## Task 1: Store query and all four measured consumers

**Files:** `src/backend/polaris/cells/roles/runtime/internal/engineering_session.py`, `internal/engineering_loop.py`, `internal/engineering_verification.py`, `public/engineering_service.py`; create `tests/test_engineering_event_selection.py` under that Cell.

**Interface:** `load_events(session_id: str, *, kinds: tuple[str, ...] | None = None, role: str | None = None) -> list[dict[str, Any]]`. The four exact kind sets and semantics are in the spec.

- [x] Add real-store synthetic tests asserting default/full compatibility; filtering equals ordered Python selection; explicit empty kinds/role do not widen; unknown run and replaced DB reject even empty selection; selected invalid JSON fails. Run before production edits and preserve expected RED.
- [x] Add fresh-process reopen coverage and relevant duplicate/unknown-effect/QA regression cases using existing authorized store APIs. Keep fixture evidence distinct from real project facts.
- [x] Implement parameterized optional filtering through the original connection and run checks; migrate visual, native-turn, review-binding and pending-tool consumers only.
- [x] Run new tests and complete related Cell regression. Verify real selected events preserve all required payloads; no hidden limits or new authority.
- [x] Compare final implementation on frozen prefix3693; keep the earlier two-prefix prototype evidence separate, with full-method references, all failed attempts and byte hashes.
- [x] Run scoped Ruff/format/Mypy, required KernelOne release and catalog incremental checks; report historical debt separately. Update graph test declarations, the Cell README and evidence docs as needed; complete independent review. Main-branch integration is performed by ROOT and evidenced by Git.

This plan follows the already authorized bounded research/implementation sequence. It adds no runtime project gate. Fresh native delivery remains separate work.

检查点：四文件最小迁移、532 项相关回归、K1 ALL 与真实新接口输出／耗时比较完成；原严格目录 159 条旧债保持。最终提交见 Git 记录；不是平台完整验收。
