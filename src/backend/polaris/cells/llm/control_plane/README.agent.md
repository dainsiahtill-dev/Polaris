# LLM Control Plane Cell

## Objective
Provide a unified, multi-provider LLM control plane. It abstracts away the complexity of different LLM providers (OpenAI, Anthropic, Gemini, etc.), implements rate limiting, token tracking, fallback mechanisms, and standardized streaming/thinking parsing.

## Boundaries & Constraints
- **State Ownership**: LLMConfigStore owns persisted model configuration through KernelFileSystem; other provider/runtime state remains governed by cell.yaml.
- **Dependencies**: The current production dependency list is cell.yaml; verification dependencies are separate.
- **Effects Allowed**: Outbound network requests to LLM provider APIs.

## Public Contracts
- Multi-provider unified routing and execution.
- Token and performance tracking for AI requests.
- `CheckLlmModelCapabilityQueryV1` / `LlmModelCapabilityResultV1` is the
  public preflight for role capabilities that require explicit model features
  such as `image_input`. Callers must not infer image support from prompt text
  or model names inside their own Cell.

## Configuration core and optional advisor

`LLMConfigService` provides deterministic configuration operations. `LLMConfig` and `LLMConfigStore` retain their original module identities. `validate_llm_provider_cfg` is the public alias for configuration validation. The optional `HRAgent` moved to `polaris.cells.roles.runtime.public.hr_advisor`; the former low-level Agent aliases are retired.

Default global configuration selection is unchanged. Physical storage inference uses the nearest complete native path component, with `.polaris/runtime` belonging to the metadata parent. This does not authorize a path: KernelOne still rejects unsupported legacy workspace/runtime writes. Nested private KFS save/cold-read and scoped checks have evidence; Windows execution and Native delivery remain unverified.
