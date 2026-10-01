# LLM connection-test result delivery repair

Status: scoped implementation and real UI connection flow verified; deep role qualification pending.
Classification: pattern. Owner: delivery HTTP/runtime.v2 transport. No new Cell or state owner.

## Dynamic evidence

Windows and WSL reach the existing MiMo Token Plan endpoint over TLS (HTTP 405 for
an unauthenticated GET). The actual saved-provider health POST passes in 11.239s.
The real Settings → Mimo → connection button produces a successful model POST in
2.478s and report `1dd903e1` is PASS, but the card stays running for 47.58s.

The publisher uses `hp.runtime.llm.test.<id>` and synthetic workspace `llm`.
The runtime.v2 subject builder has no `llm-test:<id>` mapping, so the bound socket
subscribes to `hp.runtime.<workspace-key>.llm-test:<id>`. These cannot match.

## Minimal design

```text
bound HTTP Settings.workspace
    -> existing KernelOne public resolve_storage_roots
    -> hp.runtime.<workspace-key>.llm.test.<test-id>
    -> existing JetStream/runtime.v2 workspace-bound consumer
    -> existing llm-test:<test-id> browser listener -> terminal card state
```

1. Add the scoped `llm-test:<id>` mapping in the existing subject builder.
2. Bind test events to the actual canonical workspace key/path, not `llm`.
3. Keep the current logical channel, report contract, credentials and model.
4. Reject wildcard/malformed test channels without widening factory/chat/bench access.

No HTTP polling, new realtime client, blanket firewall changes, target-project
edits, gate relaxation or main/Starwave port changes. Existing LLM evaluation owns
reports; transport only delivers those reports. Graph/catalog owners unchanged.
The existing runtime.projection workspace query was evaluated first: the HTTP
instance already has an authoritative bound Settings.workspace, so only the
KernelOne roots primitive that the WebSocket context itself uses is required.
Do not introduce a llm.control_plane → runtime.projection dependency merely to
re-resolve persisted settings. No new cross-Cell edge is needed.

## Verification

- RED/GREEN producer-consumer routing test with the real subject builder.
- Valid/dotted/long test ids; invalid wildcards; two workspace isolation.
- HTTP post-normalization ids must pass the actual subscriber grammar before a
  task starts; dotted empty tokens/truncation-invalid ids return INVALID_TEST_RUN_ID.
- Existing HTTP router and runtime.v2 protocol suites; scoped Ruff/Mypy.
- Restart only the requested isolated control instance, preserving its workspace.
- Real Playwright connection button: a fresh MiMo report arrives via WebSocket,
  card terminates without reload and without report HTTP polling.

Not covered: the initial unspecified CLI ConnectionRefused source, arbitrary
other machines, native tool execution, Bench/project completion or whole-repo certification.

## Result

47 related tests passed after final implementation; scoped Ruff/Mypy and diff
checks pass. Independent review caught HTTP/subscriber id mismatch; RED/GREEN
tests and pre-scheduling validation close it. A fresh real MiMo Settings test
finishes in6.07s with all five expected WS event types and no request failure,
refresh or report polling. Four selected role bindings survive isolated restart.

User explicitly requires deep-test qualification, not merely health connectivity.
That next qualification is separate; this result does not close the unattended
refactor plan or certify fresh Bench/project completion.
