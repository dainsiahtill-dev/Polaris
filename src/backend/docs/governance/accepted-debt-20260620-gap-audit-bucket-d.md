# Accepted-Debt Register — Gap Audit 2026-06-20 (Bucket D)

**Status:** ACCEPTED (no code change). **Source:** 24-agent remaining-gaps audit (2026-06-20),
adversarial-verification phase. **Scope:** `src/backend/polaris`.

The 2026-06-20 gap audit surfaced 48 confirmed gaps. The "全量解决" (resolve-all) campaign
fixed Bucket A (15 safe/file-local gaps) and is staging Buckets B (import cycles) and C
(large structural refactors). This register records **Bucket D: findings that adversarial
re-verification proved are NOT defects** — items whose correct resolution is a documented
acceptance, because changing the code would be a no-op, would re-break a working path, or
would violate an explicit project rule. Each entry states *why it is not fixed* with the
evidence the skeptical verifier reproduced.

| ID | Location | Disposition | Why accepted (verified) |
|----|----------|-------------|--------------------------|
| **TS-3** | `cells/director/tasking/internal/code_generation_engine.py:319-321,370-371` | Accept (no `# type: ignore`) | The reported `ForbiddenFilePatternRule`/`ForbiddenPathRule` mismatch is a **mypy loop-variable-reuse false alarm**; runtime was verified safe (`.pattern` is read on the correct concrete type). Masking it with `# type: ignore` is banned by the quality gates, and a real annotation change is not warranted. Revisit only if a clean structural annotation emerges. |
| **DC-2** | `cells/roles/adapters/internal/workflow_adapter.py` (308 LOC) + `workflow_node.py` (153 LOC) | Accept (do NOT delete) | Survey claimed a "461-LOC dead subsystem". Verifier reproduced: `WorkflowRoleAdapter` is **live, exercised by ~12 tests, and contract-pinned**. Only thin re-export wrappers are unused; deleting the modules would break live wiring + tests. |
| **ASYNC-2** | `cells/qa/audit_verdict/internal/qa_service.py:249` (`audit_task`) | Accept | Survey claimed sync `Path.read_text()` blocks the live event loop. Verifier reproduced: `audit_task` runs on a **throwaway worker loop**, not the shared live request loop, so the read does not block live traffic. Not a real concurrency defect. |
| **§8 residue** | `cells/roles/kernel/internal/transaction/bootstrap_followup.py:826-918` (`_synthesize_deterministic_dag_service_content`) + dispatcher :307 | Accept (rule-protected) | A real §8 embedded-business-synthesizer pattern, but it is a **retry-path scaffold**, which the §8 attribution rubric explicitly says is KEPT (not deleted on unit-pass), and it is **locked GREEN by 40+ existing tests**. Removing it would violate the rubric and break the suite. See [[embedded-business-synthesizers-s8]] for the scope boundary (the in-scope §8 cleanup was `execute_method.py`, which is clean). |
| **DC-3 / 1a** | `cells/roles/adapters/public/service.py:87-90` (`register_all_adapters`, alias `ln`) | Accept (cannot cleanly remove) | The function is a guaranteed no-op, but it has **4 live call sites + 4+ test mocks + an orchestration sibling**. The real wiring is the module-level `configure_orchestration_role_adapter_factory` side-effect. Removing `register_all_adapters` breaks the mocks without changing behavior — net negative. |
| **Seam loose-ends** | `cells/roles/runtime/public/capability_commands.py`; `internal/capability/registry_default.py`; `public/service.py:214,259,260` | Resolved/clean | Capability-dispatch deferred loose ends (the `handlers` DI kwarg, `service.py` lossless re-export shims, registry-vs-oracle parity) are **already closed** — fitness tests 6/6, `registry == oracle`. Nothing to do. |

## Notes

- This register is a **disposition record**, not a governance gate. It does not suppress any
  check; it documents engineering decisions so a future reader does not "re-fix" a non-defect.
- TS-3 stays under watch: if the strict-mypy surface around `code_generation_engine.py` is
  refactored for another reason, fold in a clean annotation then (never a `# type: ignore`).
- Bucket A fixes (the 15 resolved gaps incl. the only live correctness bug, TS-1) and the
  Bucket B/C plan are tracked in the campaign memory `backend-gap-audit-20260620`.
