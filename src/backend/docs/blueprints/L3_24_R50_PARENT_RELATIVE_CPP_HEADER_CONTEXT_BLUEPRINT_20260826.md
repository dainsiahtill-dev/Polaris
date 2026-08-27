# L3-24 r50 parent-relative C++ header context blueprint

## Exact-run failure

`factory_9608917f89f8` materialized the C++ project and reached QA. Compilation
became green after a real Director edit, but five behavior/depth diagnostics
remained. Later repairs targeted `src/ink_ledger.cpp`; two edits changed or
invented declarations inconsistent with
`include/core_engine/ink_ledger.hpp`, so the candidate guard correctly rolled
them back. One intervening repair produced no write.

## Dynamic cause

Every final repair request had the correct Director identity, forced
`edit_file`, PM contract, Chief Engineer blueprint, target content, and failure
feedback. It also prohibited sibling reads while requiring preservation of the
existing header API. The source includes
`../include/core_engine/ink_ledger.hpp`, but
`_resolve_cpp_quoted_include` rejected every raw path containing `..` before
canonicalization. The read-only API block was therefore absent.

## Invariant

Quoted C/C++ includes may use parent-relative syntax. Polaris may project such
headers as read-only repair evidence only when canonical resolution remains
inside the same workspace. Absolute paths, traversal outside the workspace,
and symlink escapes remain denied. Mutation authority stays limited to the
existing CE/JobToken repair targets.

## Verification

- TDD RED reproduced missing parent-relative header declarations.
- Focused and full verifier-context tests passed.
- Exact r50 replay now projects the real `InkLedger` declarations.
- Workspace-escape regression remains fail-closed.
- Ruff and Mypy passed.
- Fresh isolated r51 remains required for live completion evidence.
