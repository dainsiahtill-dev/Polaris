# Deadline controls remain outside the model prompt

Owner: KernelOne shared control-plane prompt taxonomy; roles.kernel is its consumer.
Pattern fix under approved unattended plan, no new public API, state or effect.

Exact Director final-request snapshot cf84c6e74db319e2c7ca7b8e system message1 leaks
factory_run_deadline_epoch_seconds, factory_run_deadline_source and
factory_run_timeout_seconds. Existing ContextOS audit correctly reports
control_plane_isolated=false. Actual ContextOverrideProcessor→audit replay
reproduces the same leakage without mutating the runtime input.

Existing shared taxonomy omits these keys; the renderer filters normalized names
then renders remaining scalar key:value rows. Add the three proven deadline
controls to that taxonomy, reuse existing top-level/nested/normalized filtering.
Keep actual runtime deadline fields, calculations, authority and audit strict.
Do not add a competing denylist or excuse the failed audit.

Verification: exact three-field RED; sanitized renderer output passes unchanged
audit, unsanitized input still fails; normalized variants/nested containers,
non-control data preservation and original input equality. Caller/kernel audit
regressions, Ruff/Mypy, independent review. Static key inclusion is not sufficient;
actual renderer behavior must be tested. Observed Director only; shared-role
impact is architectural inference, not evidence of every role having leaked.
