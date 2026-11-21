# ContextOS Layered Memory Architecture — 2026-11-22

## Goal

Keep long-running engineering work bounded without losing the facts needed for correct implementation and acceptance. Remote models provide lossy semantic compression; the host remains the authority for evidence, failures, acceptance and recovery.

## Layers

- **L0 Current workspace**: latest ten logical activities and all current-action dependencies remain complete.
- **L1 Node summaries**: old large FileCards and CommandCards fold with local or remote NodeSummary.
- **L2 EpochCards**: closed groups of ten activities beyond the fiftieth activity are asynchronously summarized into durable semantic memory.
- **Deterministic memory cards**: FailureClusterCard, InterfaceContractCard, RequirementTraceCard, ToolPatternCard, FileDiffCard and EvidenceLease are derived from journal facts without model inference.

## Invariants

1. Current actions, unresolved failures and acceptance dependencies are never discarded by summarization.
2. Every memory card keeps evidence references; summaries are not facts or acceptance evidence.
3. Epoch processing is asynchronous and idempotent. A failed remote summary falls back to deterministic memory and does not block the run.
4. Interface contracts, verification verdicts and UNKNOWN are host-owned and cannot be rewritten by a model.
5. Original snapshots remain content-addressed and recoverable.

## Data flow

`journal events -> deterministic memory cards -> EpochRuntime -> remote NodeSummary -> EpochCard journal event -> ContextProjectionManifest -> ContextOS node map`

The provider request compiler consumes current WorkingState and relevant memory cards rather than replaying all historical messages.
