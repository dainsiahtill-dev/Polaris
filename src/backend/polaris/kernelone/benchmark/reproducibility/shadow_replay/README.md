# Chronos Mirror: ShadowReplay

Non-invasive HTTP recording and deterministic replay for reproducible testing.

## Quick Start

```python
import httpx
from polaris.kernelone.benchmark.reproducibility.shadow_replay import ShadowReplay

async def test_llm_call():
    async with ShadowReplay(cassette_id="my-test", mode="both") as replay:
        # All httpx.AsyncClient calls are intercepted
        response = await httpx.AsyncClient().post(
            "https://api.openai.com/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        )
        result = response.json()

        # Second call replays the recorded response
        response2 = await httpx.AsyncClient().post(
            "https://api.openai.com/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        )

        assert response.json() == response2.json()
```

## Modes

| Mode | Description |
|------|-------------|
| `record` | Only record new responses, fail if exists |
| `replay` | Only replay existing recordings, fail if missing |
| `both` | Record new and replay existing (default) |

## Cassette Storage

Cassettes are stored as JSON-Lines files in the cassette directory:

```
{cassette_dir}/
└── {cassette_id}.jsonl
```

Each line is a JSON object:
- First line: Cassette header (metadata)
- Subsequent lines: Individual request-response entries

## Cassette Format

```json
{
  "cassette_id": "my-test",
  "created_at": "2026-04-04T12:00:00Z",
  "mode": "both",
  "version": "1.0",
  "entries": [
    {
      "sequence": 0,
      "timestamp": "2026-04-04T12:00:00.123Z",
      "request": {
        "method": "POST",
        "url": "https://api.openai.com/v1/chat/completions",
        "headers": {"Authorization": "[REDACTED]", "Content-Type": "application/json"},
        "body_hash": "a1b2c3d4...",
        "body_preview": "{\"model\": \"gpt-4\", \"messages\": [...]}"
      },
      "response": {
        "status_code": 200,
        "headers": {"Content-Type": "application/json"},
        "body_hash": "e5f6g7h8...",
        "body_preview": "{\"choices\": [{\"message\": {\"content\": \"Hello\"}}]}",
        "tokens_used": 50
      },
      "latency_ms": 250.5
    }
  ],
  "sanitized": true
}
```

## API Reference

### ShadowReplay

```python
async with ShadowReplay(
    cassette_id="task-123",  # Unique cassette identifier
    mode="both",              # "record" | "replay" | "both"
    cassette_dir="/tmp/cassettes",  # Optional, defaults to temp
    strict=True,              # Raise error on missing replay entry
    auto_save=True,           # Save after each entry
) as replay:
    # HTTP interception active
    ...

# Cassette auto-saved on exit
```

### Cassette

```python
from polaris.kernelone.benchmark.reproducibility.shadow_replay import Cassette

cassette = Cassette(
    cassette_id="my-cassette",
    cassette_dir="/tmp/cassettes",
    mode="both",
)

# Load existing cassette
cassette.load()

# Find matching entry
entry = cassette.find_entry(
    method="POST",
    url="https://api.openai.com/v1/chat/completions",
    body_hash="a1b2c3d4...",
)

# Add new entry
cassette.add_entry(request, response, latency_ms=250.5)

# Save to disk
cassette.save()

# Check existence
cassette.exists()
```

## pytest Integration

```python
import pytest
from polaris.kernelone.benchmark.reproducibility.shadow_replay import ShadowReplay

@pytest.mark.shadow_replay
async def test_with_replay(shadow_replay_fixture):
    """Test using shadow_replay_fixture for automatic cleanup."""
    async with shadow_replay_fixture as replay:
        result = await httpx.AsyncClient().post(url, json=data)
        assert result.status_code == 200
```

## Advanced Usage

### Manual Interceptor

```python
from polaris.kernelone.benchmark.reproducibility.shadow_replay import ShadowReplay
from polaris.kernelone.benchmark.reproducibility.shadow_replay.http_intercept import HTTPExchange
import httpx

async def custom_interceptor(exchange: HTTPExchange) -> httpx.Response:
    """Custom interception logic."""
    if "api-key" in exchange.headers.get("Authorization", ""):
        return MockResponse(403, {}, b'{"error": "Invalid API key"}')
    # Fall through to normal recording/replay
    return await default_interceptor(exchange)

async with ShadowReplay(cassette_id="custom") as replay:
    set_interceptor(custom_interceptor)
    ...
```

### Combined with Existing VCR

```python
from polaris.kernelone.benchmark.reproducibility.vcr import CacheReplay
from polaris.kernelone.benchmark.reproducibility.shadow_replay import ShadowReplay

# ShadowReplay for HTTP-level interception
# CacheReplay for LLM response caching
# They complement each other
```

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  ShadowReplay(cassette_id="task-123", mode="both")                  │
│      │                                                               │
│      ├── __aenter__:                                                 │
│      │     ├── Load/Create Cassette                                  │
│      │     ├── Patch httpx.AsyncClient.send()                        │
│      │     └── Register interceptor (record/replay/combined)         │
│      │                                                               │
│      ├── __aexit__:                                                  │
│      │     ├── Save Cassette                                         │
│      │     └── Restore original send()                                │
│      │                                                               │
│      └── Context: session-level, non-invasive                        │
└──────────────────────────────────────────────────────────────────────┘
```

## Differences from CacheReplay

| Aspect | CacheReplay | ShadowReplay |
|--------|-------------|--------------|
| Scope | Function-level (decorator) | Session-level (context manager) |
| HTTP Details | Response only | Full request/response |
| Interception | Requires `@replay` decorator | Transparent to business code |
| Format | JSON | JSON-Lines |

## Design Principles

1. **Zero Business Code Changes**: `with ShadowReplay(...)` is all you need
2. **Full HTTP Fidelity**: Headers, status codes, timing all captured
3. **Sensitive Data Protection**: API keys/tokens automatically redacted
4. **Fail-Fast in Replay**: Missing recordings raise errors immediately
5. **Ordered Playback**: Sequence numbers ensure deterministic replay
