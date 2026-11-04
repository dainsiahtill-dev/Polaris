# Explicit private OpenAI-compatible endpoints

User goal: replace unavailable MiniMax bindings with an operator-configured LAN model.
The server advertises `qwen3.8-27b` and `max_model_len=262144`.

## Topology and ownership

Host LLM JSON -> existing config store -> runtime role binding -> OpenAIProvider
-> existing HTTP transport -> operator-selected LAN service.

No new Cell, engineering owner, storage root, retry loop or product state.
The affected owner is the existing infrastructure provider adapter; the
llm_pipeline and control_plane config ownership remain unchanged. Catalog,
subgraphs, schemas and pipeline require no ownership changes or new descriptors.

## Failure and minimal design

`OpenAIProvider.validate_config` rejects every private HTTP endpoint. Add the
optional provider field `allowed_private_hosts`, an exact list of RFC 1918 IPv4
literals. The shared URL validator gets an optional keyword of the same name.
An exception applies only when the parsed hostname is an exact allowlisted
literal, the URL uses HTTP/HTTPS, has a valid port, and contains no credentials.
DNS names, loopback, link-local/metadata, public HTTP, invalid schemes and invalid
ports do not gain an exemption. Defaults remain unchanged. Validate any absolute
api_path/models_path against the same policy to prevent bypassing the selected
host through an alternate action URL. This is explicit operator configuration;
it never goes into model prompts as authority.

## Execution and validation

Write provider validation regressions first and observe the expected LAN denial.
Implement the bounded URL exception and provider propagation. Run Ruff, format,
strict mypy and provider regressions. Enable only the exact LAN address in the
host configs, preserving unrelated credentials, providers and roles. Validate
real text, streaming and native tool-result round trips, then an isolated
engineering run and final provider request audit when the runtime permits it.

Configuration backups and machine-readable evidence live under ~/.polaris/audits/.
No generated target source may be manually repaired. A small live run does not
prove 256K load capacity or full platform acceptance.

## Complexity and risks

Allowlist membership is O(n) time and O(1) auxiliary space; n is host list size.
No additional DNS lookups for an authorized literal. The option authorizes a
provider endpoint, not tool network access. Transport redirects and the existing
public-host DNS policy are unchanged; this change does not claim to harden those
separate paths. LAN service performance and long-context quality need later tests.

## Adjacent adapter defect confirmed during verification

Strict mypy identified enum/string comparisons in OpenAI inline thinking and
flush handling. Real parser output uses ChunkKind.THINKING/TEXT; strings never
match. A failing regression confirmed thought text leaked into visible answer
chunks. Correct these three comparisons and retain behavior tests for complete
and unfinished inline thoughts and visible answer flush. No wire schema or
provider-native reasoning protocol changes.
