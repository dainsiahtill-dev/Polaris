# OpenAI native message parity — 2026-10-01

Status: implementation and exact saved-request replay verified; live same-run
Director requalification pending. Not autonomous project completion.
Owner: KernelOne provider-native projection. Adapter consumes KernelOne; no reverse import.

## Exact-run evidence

Fresh isolated `L1-01`, `factory_9aa169804636`, MiMo `mimo-v2.6-pro`:
PM completed; CE completed after a bounded schema reconstruction. Director's
first request was rejected before HTTP with `physical_wire_messages_drift`.
`director.canonical_task_boundary_missing` is downstream, not the first cause.

The frozen Director request is snapshot `36fc1de468a8486e2acc06c3`.
Replaying its canonical payload through the actual projection and provider body
builder yields one differing body key: `messages`. Leading system content is
identical. User content is 20623 versus 20637 characters: two mid-conversation
system markers were lost by projection-side merge-before-conversion. No target
project was edited and no Provider request was needed for this reproduction.

## Minimal design

```text
Frozen semantic messages
  KernelOne build_openai_native_messages (normalize before adjacent merge)
    native request authority
    infrastructure provider_helpers thin consumer
  unchanged exact-wire qualification
  reservation / snapshot / physical dispatch
```

Keep the infrastructure helper's existing empty-input fallback and explicit
legacy all-system prompt behavior. Put role conversion and merging in one pure
KernelOne function. Anthropic normalization remains unchanged. No MiMo branch,
no weakened equality, no invented receipt, no change to Bench targets or judges.

## Verification

- RED: literal consecutive mid-system regression, full body parity for invoke,
  stream, and Responses paths; preserve input immutability.
- GREEN: OpenAI/Ollama normalization suites, native request projection suites,
  B3.5 real-drift rejection suites; Ruff/Mypy on changed production files.
- Replay this exact frozen payload and compare all body keys, without network.
- Restart only the owned isolated backend via Supervisor after terminal drain;
  preserve PM and CE and use the existing same-run Director retry when admitted.
  A successful HTTP request is not a completed project.

## Risks / boundaries

The CE first response exhausted 16384 output tokens; that is separate from this
pre-dispatch platform failure. Native tool execution, artifact/verifier receipts,
QA and `COMPLETED_VERIFIED` still require fresh/current physical evidence.
Full unattended plan P02/P03/P04 and L1-L12/N-batch qualification remain open.
Graph and Cell dependencies do not change; adapter already depends on KernelOne.

## Implemented evidence

Five new regressions were RED before production edit, then GREEN. Combined
native message/provider body, role request qualification, physical gate and
sync/stream executor suites: 366 passed, two existing API deprecation warnings.
M04 module gate passed (4.1s); Ruff and Mypy on the two production files passed.
Exact frozen Director payload replay passed the unchanged B3.5 wire validator;
appending forged content still raised `physical_wire_messages_drift`.

The larger gate exposed unrelated split damage in the physical gate tests:
missing shared fixtures and two decorators, plus patches aimed at the old
invoker facade. Restored original test-only helpers and exact parameter cases;
57 original test bodies remain present. Three patch targets were migrated to
their actual defining modules; assertions were not removed. A stream-order
test's exact expected metadata was extended to assert the existing argument
assembly evidence, verified in a pytest post-mortem breakpoint.

