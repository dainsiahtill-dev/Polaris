# Native single-file argument transport — P05

Status: local implementation and exact nine-call replay verified; same-run live
Director requalification pending. Not project completion.

## Proven boundary

`factory_9aa169804636`, Director call `5e00312ddc5947cbb375b096b82c3428`:
HTTP 200, nine native `write_file` calls. Invoker reports
`text_tool_parser_attempted=false` and `text_fallback_requested=false`.
For all nine calls, reconstructing the original JSON argument string from the
persisted typed invocation reproduces its `raw_arguments_sha256` and decoded
hash exactly. Thus a fresh network replay is unnecessary to recover these
argument bytes; this is not proof of the complete original HTTP response.

The actual single-key argument object is `{"file/<relative-path>": "<body>"}`;
five keys carry the exact terminal protocol fragment `</parameter`. The native
schema requires string `file` and `content`. Current normalization keeps these
encoded keys, so DEO correctly refuses missing path binding before effects.

## Minimal design / owner

```text
Native argument bytes and immutable argument audit
  actual native JSON parser
  captured ToolSpecSnapshot
  snapshot-owned argument-envelope decoding
  canonical file/content (body unchanged)
  existing aliases / canonical normalizers
  unchanged DEO scope, JobToken, target-state and effect gates
```

Extend only the existing snapshot-owned argument-envelope decoder. No MiMo
provider branch, no business code, no new tool or execution authority.

Decode only when:

- canonical tool is `write_file`;
- its captured schema has exactly required string `file` and `content`, without
  additional required fields or alternative required groups;
- payload has exactly one key beginning with the canonical `file/` slot label;
- its value is a string, retained byte-for-byte;
- only one exact terminal `</parameter` or `</parameter>` fragment may be
  removed from the **argument key**, never from the body or an explicit path;
- decoded target is an unambiguous forward-slash relative path: no blank,
  absolute/drive, dot/traversal components, backslash, control/angle characters,
  or leading/trailing whitespace.

Canonical inputs remain identical; a second normalization is identical. Multiple
keys, wrong tool/schema, wrong value type, malformed delimiters and unsafe paths
remain unmodified, and existing execution guards continue to reject them.
An apparently valid relative path is not permission: outside-owned or protected
targets must still fail the same policy/JobToken gate.

## Verification

1. Literal positive regressions RED, negatives unchanged; no model/target writes.
2. Snapshot parser/normalizer tests prove content preservation, hash binding,
   canonical idempotence and custom schema isolation.
3. Replay all nine hash-recovered exact arguments through the real parser and
   captured owner normalizer; compare original/normalized body hashes.
4. Existing DEO scope and tool-classification parity regressions remain green.
5. Existing same-run Director retry only after gates; PM/CE preserved.
6. No current/fresh completion claim without actual effects, verifier and QA.

## Open risks

Repair timeout (120s versus primary observed621s), raw-response retention,
standalone workspace admission and global release failures remain separate.
This fix does not auto-replay a previously failed effect batch or bypass an
execution epoch. Full P05 transport matrix and unattended plan remain open.

## Implemented evidence

Four positive regressions RED before implementation; an additional custom-schema
mandatory-field test exposed a too-broad subset check and was independently
RED/GREEN tightened to the exact required set. Combined normalization,
classification, guard, tool-batch, adapter snapshot and dispatch-fence suites:
370 passed, two existing fork deprecation warnings. Ruff and Mypy on the
production owner passed. M03 PASS8.64s; M04 PASS4.17s.

All nine original raw and parsed argument hashes match; normalized body hashes
remain identical. No Provider call or target edit during replay. Physical scratch
tests confirm an admitted target writes literal XML unchanged while sibling,
outside, AGENTS and runtime targets remain refused by the existing bound gate.

