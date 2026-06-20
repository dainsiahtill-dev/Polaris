# TS-7 typing-fix (test-only): mypy --strict errors across 3 contract-test files

kind=typing-fix effort=medium

# TS-7 Typing-Fix Blueprint (test-only, mypy --strict)

## Scope
Three contract-test files, 117 `mypy --strict` errors, all suites currently pytest-green. Test-only: no production edits. Fixes are proper annotations / typed locals / in-body narrowing — NO `type: ignore`, NO `Any`.

| File | Errors | Codes |
|---|---|---|
| polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py | 84 | arg-type (80 contravariance + 4 SimpleNamespace) |
| polaris/tests/kernelone/contracts/test_master_types.py | 22 | var-annotated x12, comparison-overlap x10 |
| polaris/kernelone/context/tests/test_strategy_contracts.py | 11 | misc(read-only) x4, arg-type x4, index x3 |

## Root causes
**(A1) Protocol contravariance (80).** Ports in `roles/runtime/internal/capability/deps.py` declare `def m(self, x: object) -> object`. Fakes declare concrete types (`command: ReserveBudgetCommandV1`). A narrower param is not assignable to an `object` param, so every `execute_role_capability_invocation(..., budget_guard_service=FakeBudgetGuardService())` fails. `FakeRoleProfileService.get_profile(self, query: object) -> object` (line 267) is already correct — the template. CYCLE-15's `FakeArchitectDesignService.run_boundary_design` uses `Mapping[str, object]` kwargs that match `ArchitectDesignPort` and does not error.

**(A2) SimpleNamespace inner payloads (4).** `FakeCognitiveRuntimeCommitService` returns real `*ResultV1` whose inner field is typed `ChangeSetValidationResult|None` etc., but passes `SimpleNamespace(...)`. Real inner types live in `polaris/domain/cognitive_runtime/models.py`.

**(B1) Unbound generics (12).** `Envelope(Generic[T])`, `EffectTracker(Generic[T])` — bare construction leaves `T` unbound. Plus one untyped dict literal (line 97).

**(B2) str-Enum comparison-overlap (10).** `SubsystemStatus`/`ScheduleKind` are `(str, Enum)`; mypy narrows member-vs-string-literal to non-overlapping.

**(C1) Frozen-dataclass mutation tests (4).** `metadata.description = "modified"` inside `pytest.raises` — static read-only write.

**(C2) Heterogeneous dict literal (7).** Round-trip dict `d` infers `dict[str, Collection[str]]`, so `d['profile_id']` (-> StrategyProfile) and `d['metadata']['description']` mistype.

## Ordered atomic-green plan
1. **master_types comparison-overlap** -> compare `.value` (`SubsystemStatus.HEALTHY.value == "healthy"`). Behavior identical (str-Enum member == its value).
2. **master_types var-annotated** -> `env: Envelope[object] = Envelope()`; `EffectTracker[None]("op-1")` (matches class docstring); `d: dict[str, object] = {...}` (line 97).
3. **strategy read-only misc** -> `setattr(obj, "field", value)` inside the `pytest.raises` (still raises FrozenInstanceError at runtime).
4. **strategy arg-type/index** -> add a `TypedDict` (`_MetadataDict`, `_ProfileDict`) mirroring `StrategyProfile`/`ProfileMetadata` field types and annotate `d`.
5. **role-runtime A1** -> per Fake, widen each consumed method param to `object` and narrow first-line with `assert isinstance(command, ConcreteT)`; keep concrete return type. One Fake per atomic step.
6. **role-runtime A2** -> build real `ChangeSetValidationResult`/`RuntimeReceipt`/`ContextHandoffPack`/`HandoffRehydration` from `polaris.domain.cognitive_runtime.models`, supplying all required fields and preserving the values production reads.
7. **final gate** -> `mypy --strict` = 0 on all three; full pytest passes; diff regenerated `descriptor.pack.json` for zero drift.

## Guardrails
- No production edits; ports stay `object` (Any-free seam).
- descriptor.pack.json records only class/method NAMES + docstrings -> annotation changes are safe; never rename/add/remove a Fake class or public method.
- Do NOT add a real `architect.design` import (CYCLE-15 edge removal; cell-dependency gate counts test imports). Leave `FakeArchitectDesignService` untouched.
- `domain` is the shared layer (delivery->application->domain->kernelone); importing it from a roles.runtime test is direction-legal and not a cross-cell edge.
- Use `assert isinstance(...)` for narrowing (real runtime guard) rather than `cast` (avoids Any-laundering).
- No §8 business code present in these test files.

## Effort: medium (Step 5 is ~13 Fakes / ~21 methods of mechanical widen+assert; Steps 1-4 + 6 are small).