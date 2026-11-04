# Sync provider transport test alignment

## Observed failure and root cause

The extended provider suite reported19 failures/344passes. Gemini, Ollama and
three synchronous physical-dispatch sentinels patched requests.post, but the
existing provider helper now calls bounded_http_post, which implements one
bounded aiohttp exchange and returns a buffered requests.Response. Old mocks
never intercept the actual transport and tests contact real/fake-key endpoints.
The bound dispatch envelope correctly declares aiohttp.ClientSession.post.

## Existing architecture and minimal change

Provider invoke -> _blocking_http_post -> existing bound physical dispatch port
-> _raw_blocking_http_post -> bounded_http_post -> aiohttp transport.

Update only the three affected test modules to replace the external send at
_http_pooling.bounded_http_post. Keep provider parsing, the physical dispatch
port boundary and exactly-one-send/body/model comparisons real. The recording
port itself does not prove immutability; independent native-capture tests cover
the actual frozen wire boundary. Update
only the obsolete transport-kind expectation to match actual production
transport. No product fallback, no provider implementation change, no test
removal and no relaxed response/usage/error/count assertions.

The KernelOne/native HTTP timeout and capture suites remain independent tests
of the real bounded transport. No Cell ownership, public contract, state,
graph, descriptor, permission or runtime effect changes; catalog/subgraphs,
fitness rules and pipeline require no boundary updates.

## Verification

The original extended sweep is the RED evidence. Run the entire provider
integration directory after updating these interception points. Also locate
and run the bounded HTTP/native physical capture tests to ensure transport
behavior remains checked independently. Review the exact diff and verify
UTF-8, Ruff/format, and appropriate test/type checks. All benchmark target
sources remain untouched by the supervising agent.

## Complexity and limitations

Test-only interception changes, O(1) per fake send; no runtime complexity change.
This does not establish external provider readiness or all120 Bench acceptance.

Strict mypy also exposed incomplete async recorder test doubles. A test-only
base implements undeclared protocol branches by raising AssertionError; concrete
recorders retain only their expected success paths. No production protocol was
weakened and an unexpected branch fails immediately. Initial aligned provider
sweep375passed; independent real HTTP deadline/native capture52passed.
