# Captured write argument assignment alias

Status: candidate, not permission or Factory completion.

Dynamic exact-run evidence: `factory_9aa169804636`, TaskRuntime task13,
native request `28fb167824296d1f475d7c55` / call
`ffe19ce8c53d4172a61433a7844f7310`. A real native invocation contains
`content` (26889 characters) and `file=path` (20 characters), not `file`.
DEO rejects it with `deo_tool_normalization_failed`; a subsequent empty-write
repair times out at120s. The valid body is not evidence of an empty write.

Declare only the exact `file=path -> file` synonym in the existing write-file
ToolSpecRegistry. Consume it through the captured immutable spec, never by
splitting arbitrary `=` keys or rewriting code. When this synonym coexists
with a conflicting captured file alias/canonical value, reject ambiguity
before normalization. Equivalent literal duplicate values remain harmless.
Other tools and old snapshots without this alias must not acquire it.

Existing flow remains: raw native parser -> one captured ToolSpec -> aliases
and shape normalization -> authorization/path/before-state guard -> effect ->
receipt. No automatic replay of the failed batch or new grant. Tests exercise
real native parsing, conflict rejection, captured-snapshot isolation, body
identity and the real scoped writer in scratch storage. Generated target
files remain read-only to the engineering agent.

Output/timeout parity is an independent defect. Neither alias acceptance nor
correctly enforcing a7000 cap proves that this multi-file workload converges.
