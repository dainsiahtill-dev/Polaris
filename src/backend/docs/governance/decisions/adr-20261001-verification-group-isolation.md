# Verification groups run on source-bound disposable workspaces

Status: accepted for implementation under the unattended master plan; not deployed.

Evidence: exact failed tsc candidate created three unowned outputs surviving the
source-only guard rollback. Cargo already isolates a true copy; the other command
path executes live and the broker sandbox's input overlays do not remove its RW
source mount.

Decision: source-bound disposable group, shared build/test/start copy, read-only
dependencies/toolchain, no source RW mount/network or output promotion. Existing
receipt authorities own success; the staging primitive hashes inputs but never
mints authority. Physical process-tree cancellation/drain is required before
disposal, and restored sources receive new actual verification.

Alternatives: live-output deletion lacks independent creation receipts/CAS and can
erase foreign work; per-command copies break build/test continuity; widening
Director scope grants writes to undeclared compiler outputs. None accepted.

Consequences: unavailable isolation is an explicit blocker; unknown stale outputs
are not copied as source; timing/path mapping and dependency identities require
tests. This architecture is accepted, but implementation, integration, live and
fresh L1-L12/N-batch qualification remain open.

Repair-epoch refinement: current artifact receipts remain current-JobToken/policy
bound. The broker adds a separate read-only, owner-sealed source baseline query
over authenticated historical receipts and matching live bytes. It cannot mint
write/verification authority, satisfy completion, or become a current receipt.
No missing database/key creation on that query path. This closes the observed
six-of-ten baseline gap without relaxing final delivery authority.

Spec: ../../blueprints/POLARIS_VERIFICATION_GROUP_ISOLATION_20261001.md
Plan: ../../../../../docs/superpowers/plans/2026-10-01-verification-group-isolation.md
