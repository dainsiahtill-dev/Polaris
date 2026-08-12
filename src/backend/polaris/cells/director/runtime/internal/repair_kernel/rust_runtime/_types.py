from __future__ import annotations

from dataclasses import dataclass

from ..contracts import (
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairExecutionResult,
    RepairPlan,
)
from ..policy_gate import PolicyDecision


@dataclass(frozen=True)
class RustCrateImportRewritePlanning:
    """Planning result for Rust local crate import rewrite repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustCrateImportPlanning:
    """Planning result for the Rust crate import repair source tool."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustCrateImportRewriteRun:
    """Execution result for Rust local crate import rewrite repair."""

    planning: RustCrateImportRewritePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustCrateImportRun:
    """Execution result for the Rust crate import repair source tool."""

    planning: RustCrateImportPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustDependencyPlanning:
    """Planning result for Rust dependency repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustDependencyRun:
    """Execution result for Rust dependency repair."""

    planning: RustDependencyPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustMissingBinaryEntrypointPlanning:
    """Planning result for Rust missing binary entrypoint repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustMissingBinaryEntrypointRun:
    """Execution result for Rust missing binary entrypoint repair."""

    planning: RustMissingBinaryEntrypointPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustMissingLibTargetPlanning:
    """Planning result for the narrow Rust missing lib target repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustMissingLibTargetRun:
    """Execution result for the narrow Rust missing lib target repair."""

    planning: RustMissingLibTargetPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustLibRootFacadePlanning:
    """Planning result for the narrow Rust lib-root facade repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustLibRootFacadeRun:
    """Execution result for the narrow Rust lib-root facade repair."""

    planning: RustLibRootFacadePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustDuplicateModuleFilePlanning:
    """Planning result for Rust duplicate module file repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustDuplicateModuleFileRun:
    """Execution result for Rust duplicate module file repair."""

    planning: RustDuplicateModuleFilePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustMissingModuleFilePlanning:
    """Planning result for Rust missing module file repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustMissingModuleFileRun:
    """Execution result for Rust missing module file repair."""

    planning: RustMissingModuleFilePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustIncompatibleCopyDerivePlanning:
    """Planning result for Rust incompatible Copy derive repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustIncompatibleCopyDeriveRun:
    """Execution result for Rust incompatible Copy derive repair."""

    planning: RustIncompatibleCopyDerivePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustMethodSelfSignaturePlanning:
    """Planning result for Rust method self-signature repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustMethodSelfSignatureRun:
    """Execution result for Rust method self-signature repair."""

    planning: RustMethodSelfSignaturePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustLineSuggestionPlanning:
    """Planning result for Rust compiler line-suggestion repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustLineSuggestionRun:
    """Execution result for Rust compiler line-suggestion repair."""

    planning: RustLineSuggestionPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustFieldRenameSuggestionPlanning:
    """Planning result for Rust field rename suggestion repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustFieldRenameSuggestionRun:
    """Execution result for Rust field rename suggestion repair."""

    planning: RustFieldRenameSuggestionPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustWrongCratePathPlanning:
    """Planning result for Rust wrong crate path repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustWrongCratePathRun:
    """Execution result for Rust wrong crate path repair."""

    planning: RustWrongCratePathPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustSerdeDerivePlanning:
    """Planning result for Rust serde derive repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustSerdeDeriveRun:
    """Execution result for Rust serde derive repair."""

    planning: RustSerdeDerivePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustMissingTraitDerivePlanning:
    """Planning result for Rust missing trait derive repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustMissingTraitDeriveRun:
    """Execution result for Rust missing trait derive repair."""

    planning: RustMissingTraitDerivePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustTraitImportPlanning:
    """Planning result for Rust trait import repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustTraitImportRun:
    """Execution result for Rust trait import repair."""

    planning: RustTraitImportPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustUnusedImportPlanning:
    """Planning result for Rust unused import repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustUnusedImportRun:
    """Execution result for Rust unused import repair."""

    planning: RustUnusedImportPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustUnresolvedPubUsePlanning:
    """Planning result for Rust unresolved public re-export repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustUnresolvedPubUseRun:
    """Execution result for Rust unresolved public re-export repair."""

    planning: RustUnresolvedPubUsePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustStructLiteralMissingFieldPlanning:
    """Planning result for Rust generated struct literal missing-field repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustStructLiteralMissingFieldRun:
    """Execution result for Rust generated struct literal missing-field repair."""

    planning: RustStructLiteralMissingFieldPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RustMissingFieldsPlanning:
    """Planning result for explicit-type Rust missing-field declaration repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustMissingFieldsRun:
    """Execution result for explicit-type Rust missing-field declaration repair."""

    planning: RustMissingFieldsPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None
