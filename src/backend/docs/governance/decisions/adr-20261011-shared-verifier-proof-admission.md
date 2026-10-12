# Shared verifier command proof rejection

Status: implementation authorized by the unattended delivery goal; verification pending.

Fresh Factory `factory_8c6b73a85e95` exposed differing admission semantics: PM validated typed argv/cwd but allowed help/version queries, while CE rejected the already-committed command. CE cannot rewrite PM-owned verification authority, so this failure must be surfaced before PM commitment.

Move existing pure CE proof rejection into KernelOne quality and delegate both PM and CE to it. Preserve command/hash owners, structured diagnostics, current revision budgets and independent real verification. Reject the additional exact JavaScript exit no-op observed in the same native plan. This is platform-level command policy, with no Bench or project semantics.

Rejected alternatives: duplicating regexes in PM, removing CE validation, replacing the generated project's commands by hand, or treating introspection/no-op exits as real evidence. All would preserve or conceal the authority mismatch.

PM automatic correction must be verified through the existing adapter/revision path and a fresh isolated run. Until then source tests do not establish unattended delivery.
