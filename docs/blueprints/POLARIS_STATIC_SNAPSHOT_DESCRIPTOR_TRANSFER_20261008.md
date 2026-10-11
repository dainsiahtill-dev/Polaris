# Workspace snapshot directory descriptor transfer

POL-50; mode=implementation; owner=Director tools engineer; reviewer=Root.

The original `WorkspaceReadSnapshot` remains the sole reader. The bounded
change owns a pending child directory from successful descriptor acquisition
until the parent is closed and the child becomes the current directory.

```text
quality consumer -> WorkspaceReadSnapshot -> absolute ancestor traversal
                                        -> nested no-follow opener
pending child -> checked transfer -> current directory -> caller/cleanup
```

The absolute-root and nested opener currently leave a child unowned at their
first line after `os.open`. The unchanged reviewer probe, SHA-256
`ca5e7e1a3cbb8d460a719c1cc63a2fd0882c326aee40d2d88860901bfe4dc37d`,
reproduced both leaks against the actual module. Each propagated interrupt left
one fixture-owned directory descriptor; probe cleanup restored the FD set.

Both traversals track `child: int | None` inside an acquisition `try/finally`.
One tuple assignment swaps the current-directory owner and the pending-child
owner. Inner cleanup closes the child on failure before transfer, or the old
parent after successful transfer; outer cleanup owns the current directory.
No extra ancestor descriptors are retained, preserving constant descriptor
pressure and existing identity checks. The preliminary close-return assignment
passed runtime tests but failed mypy; that attempt and diagnostic remain in RAW.

Assumptions: actual open uses no-follow; acquisition returning an FD binds its
owner; normal `os.close` either returns `None` or raises; no other thread closes
this reader's private FDs. Trace injection must observe a real child descriptor
and a separate parent, must fire once, and must propagate `KeyboardInterrupt`.
The tests cover Python line-event interruptions; arbitrary opcode/syscall
interruptions and failures of the cleanup syscalls are not a proved guarantee.

Pre-mortem: stale fixed line numbers could skip injection; child could be closed
twice after transfer; cleanup could mask interrupt; changing identities or cache
semantics could invent missing evidence. Semantic FD witnesses, exception object
identity, full before/after FD maps, normal trace controls, and original tests
address those risks.

Only the two original reader routines, one new test, this blueprint, and its
verification card are writable. No Cell/public contract, runtime root, repair
binding, platform effect, receipt, graph, model or authorization changes occur.
KernelOne is reused; no second reader or new cross-Cell dependency is introduced.
Time remains O(path depth); space and simultaneously open directories remain O(1).

Verification: original probe RED, new semantic contract RED, minimal repair,
new contract GREEN; immutable probe replay with old assertions retained. Replay
92 frontier, 195 components, six-file 288 consumers under long/short fixture
roots, two calibrations under both roots, exact archived probes, Ruff/format,
scoped mypy and normalized six-error strict debt. Full T05/native/Factory/fresh
Bench acceptance is excluded and remains with Root. Evidence is retained in
`.superpowers/sdd/2026-10-06-paperclip-repair-frontier/static-snapshot-descriptor-transfer-v1/`.

Final bounded results: 15 new contracts; 92 frontier; 195 components; long and
short 288 consumers; long and short two-case calibrations passed. Original
19/55/55 probes, root/ancestor/reviewer package cases, three helper and four
effect cases replayed; outside content reads and scanner effects remain zero.
Ruff, format and scoped mypy passed. Exact confirmation retains 4 PASS/1 FAIL;
strict retains the same normalized six diagnostics. Unmodified old FD probe
exits 1 because its former root line no longer fires; this is not GREEN.
Root owns independent acceptance and natural drain. See REPORT.json for argv,
exit codes, risks and the precise authority boundary.
