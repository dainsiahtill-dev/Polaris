# Factory event capacity correction

Observed failure: a legitimate Director stage result could not be committed after
700 accumulated events reached 8,143,016 bytes. Five previous stage results alone
contained about 6.5 MiB of verification and repair metadata. The next result was
rejected by the 8 MiB cumulative event limit, before its outcome became durable.

Flow: Factory stage result -> transaction capacity preflight -> authoritative
FactoryStore append -> immutable hash chain -> checkpoint and run projection.
Factory pipeline owns this state; existing KernelOne locked-file and canonical
JSON capabilities continue to perform storage and integrity checks.

Correct the existing cumulative limit to 64 MiB, matching the run snapshot
capacity. Use the same bound for preflight, append, strict decode, and read-only
inspection. Keep existing events, hashes, sequence/CAS checks, record bounds, and
all delivery verification requirements. No new setting, service, evidence store,
or operator approval is needed.

Regression: three real 3 MiB stage records must preflight, append, and strictly
read back; altering their diagnostics must still fail hash verification. A small
test-specific bound proves inclusive capacity and rejection without mutation.
Run the event-chain, stage-persistence, and snapshot-store tests, then revalidate
the failed native run. This source fix does not constitute fresh Bench acceptance.

Residual limit: long runs remain finitely bounded at 64 MiB / 4096 records;
event segmentation is deferred until actual execution demonstrates that need.

Request inspection also exposed contradictory tool advertising: the Director
role prompt listed the broad profile whitelist while a repair request offered
only `edit_file`. The existing final decision-message builder now projects that
advertisement from current callable tools, before provider conversion. It does
not expand tools or change authorization. Regressions cover Chinese/English,
forced tool choice, preserved user quotations, and unchanged cached context.

Payload analysis identified the duplication source: every verifier result carried
all commands accumulated in its shared group. Eighteen result rows therefore
stored 171 command records per observation. Project only the issued command ID
into each command result, retaining input/output hashes, source binding, and
drain/disposal observations. The owning group's complete command history and
process lifecycle remain unchanged. Real isolated commands must prove both a
failed result stays failed and the full group still retains every record.
