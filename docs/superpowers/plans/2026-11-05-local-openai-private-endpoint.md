# Local OpenAI Endpoint Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; implement inline on main.

**Goal:** Support an explicitly authorized private model endpoint and switch MiniMax bindings.
**Architecture:** Extend the existing provider validator with an exact private IPv4 allowlist. Reuse existing config persistence, runtime bindings and transport.
**Tech Stack:** Python, ipaddress, urllib.parse, pytest, existing Polaris provider infrastructure.
**Spec:** docs/blueprints/LOCAL_OPENAI_PRIVATE_ENDPOINT_20261105.md

## Global Constraints

UTF-8. Preserve unrelated work and credentials. No service restarts. Default SSRF denials remain. No second runtime owner. No target-source edits.

## Review Focus

- A hostname merely containing the trusted address stays denied.
- Credentials and malformed ports cannot inherit private-host authority.
- Link-local and loopback stay denied even when listed.
- Absolute API/model paths cannot override the allowed destination.
- Invalid allowlist types cannot become truthy authorization.

### Task 1: Explicit private-host provider validation

Files: modify infrastructure/llm/providers/http_utils.py and openai_provider.py;
test polaris/tests/integration/llm/providers/test_openai_provider.py (all under src/backend/polaris).

- [x] Add parameterized validation tests for exact RFC 1918 literal HTTP/HTTPS access and Review Focus denials; run before implementation and retain failure output.
- [x] Add keyword-only allowed_private_hosts to validate_base_url_for_ssrf; require parsed private literal, HTTP/HTTPS, no userinfo, valid port. Validate and forward the provider list; check absolute api_path/models_path with the same validator.
- [x] Run related provider tests, Ruff, format and strict mypy. Recheck diff and default denial cases.
- [x] Persist exact allowlist using existing config owner, re-resolve every changed role and preserve backups.
- [x] Verify real provider text/stream/native tools and isolated engineering execution; audit final provider request and report any remaining acceptance limit.

## Execution evidence

- Initial authorization tests: 14 failed / 14 passed before implementation.
- Independent review found two malformed-allowlist cases; reproduced failures and fixed shared-boundary validation.
- Strict mypy exposed an existing enum/string mismatch in OpenAI thought chunks. A failing behavioral regression confirmed reasoning leaked as answer; use ChunkKind values and test complete/unfinished thinking plus flush.
- Final focused gates: 93 passed (provider plus bench-runner component tests). Ruff and strict mypy pass.
- Live CLI run `63d66dd0c5874908a76f56c04e0c857d`: accepted, checks passed, independent QA approved.
- Extended provider sweep from an earlier in-progress candidate: 344 passed / 19 failed, involving Gemini/Ollama/other dispatch tests. Those failures remain tracked in the external defect report; this is not full-suite acceptance.
- Model server rejects images (`At most 0 image(s) may be provided`); no model-vision capability claim.
- Separate user request now requires all 120 original L1-L12 cases; tracked under ~/.polaris/bench_runs/qwen38-all120-20261105/manifest.json.
