# Empty tool surface recovery fence

Status: RED reproduced; implementation pending. Owner: roles.kernel, master P05.

Exact dynamic test `test_no_tool_definitions_suppresses_decoded_tool_batch`:
empty exposed definitions, user explicitly requests no tools, native repo_tree is
suppressed, then R134 recovery restores it. Actual executor seam called twice,
LLM three times; final guard rejects only after dispatch. No physical file effect
was proven by this AsyncMock fixture. Source and test match HEAD before this fix.

Bounded fix: the shared native recovery function receives the actual captured
tool-surface-presence boolean from both nonstream and streaming owners. Check it
before any executable-batch fast path/recovery. With an empty surface, no native
or decoded batch may execute. Existing explicit non-write text-only suppression
may finish as a proposal with its anomaly preserved. Native writes still fail
closed; trusted private non-executable structured-result transport remains valid.
This boolean is not a capability or a second state owner; it only denies recovery.
Do not alter aliases, grant/scope, retry budget, or tool dispatch permission.

Verification: preserve original final-answer/zero-dispatch assertions; RED on
zero-dispatch first, then GREEN. Direct no-surface read/write/existing-batch
guards, streaming caller parity, structured-result transport, old R134 recovery
and canonical receipt suites. M03/M04 are sealed and affected: this repair is a
reviewed hardening/unseal candidate until those gates and live qualification pass.
No claim that a no-surface guard alone solves subset exposure or all bootstrap
scope paths. Failed-tool quality/adaptive success projection is a separate defect.
