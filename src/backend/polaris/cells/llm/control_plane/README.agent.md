# LLM Control Plane Cell

## Objective
Provide a unified, multi-provider LLM control plane. It abstracts away the complexity of different LLM providers (OpenAI, Anthropic, Gemini, etc.), implements rate limiting, token tracking, fallback mechanisms, and standardized streaming/thinking parsing.

## Boundaries & Constraints
- **State Ownership**: None. Purely functional control plane.
- **Dependencies**: None.
- **Effects Allowed**: Outbound network requests to LLM provider APIs.

## Public Contracts
- Multi-provider unified routing and execution.
- Token and performance tracking for AI requests.
- `CheckLlmModelCapabilityQueryV1` / `LlmModelCapabilityResultV1` is the
  public preflight for role capabilities that require explicit model features
  such as `image_input`. Callers must not infer image support from prompt text
  or model names inside their own Cell.
