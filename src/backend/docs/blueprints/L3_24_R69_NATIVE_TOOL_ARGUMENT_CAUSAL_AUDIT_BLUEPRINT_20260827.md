# L3-24 r69 Native Tool Argument Causal Audit Blueprint

Status: implemented; fresh exact-run evidence pending.

## Symptom

Factory run `factory_9c8fc3cc8669` materialized C++ files whose native
`write_file` arguments already contained systematic angle-bracket corruption:
`std::optional<Algorithm>` became `std::optionalAlgorithm`, includes lost
delimiters, and XML-like closing tags appeared at file tails. Six of six C++
translation units failed syntax checks.

## Exact-run evidence boundary

- The corrupted value existed in TaskRuntime's native tool-call `arguments`
  before Director policy and `write_file` execution.
- Effect receipt hashes matched the corrupted argument and disk content; the
  write adapter did not mutate it further.
- The retained lifecycle envelope recorded only hashes, not raw provider
  fragments. Therefore whether corruption originated in provider output or in
  stream aggregation was not provable after workspace cleanup.
- Root cause remains `root_cause_unproven`; static inspection is insufficient.

## Generic platform change

`StreamExecutor` now emits an opt-in `llm_tool_call/arguments_assembled` debug
event after native streamed JSON becomes an executable tool call. Evidence is
privacy-bounded and contains:

- provider, tool name, call id, target path;
- raw streamed argument length and SHA-256;
- decoded argument SHA-256;
- content length and SHA-256;
- `<`, `>`, and `</` counts;
- stream assembly counters.

No source text or secret-bearing raw arguments are copied into telemetry.

## Verification

The regression replays fragmented Anthropic `input_json_delta` containing C++
includes and template syntax and proves exact content preservation plus audit
emission. Ruff, format check, targeted pytest, and mypy pass.

## Fresh-run decision rule

On the next isolated run, compare the debug event with the native tool-call
argument/effect receipt:

1. malformed raw stream hash/profile and identical decoded/effect content:
   upstream provider-native output defect;
2. sound raw stream profile but malformed decoded/effect content: Polaris
   accumulator/decoder defect;
3. sound decoded content but malformed effect hash/disk: tool gateway/write
   adapter defect.

Only the proven branch may authorize a platform fix. Generated project files
remain read-only evidence.

