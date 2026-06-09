# DataSync Client

A small Python client for the DataSync service.

## Quick start

```python
from src.api import DataSyncClient

client = DataSyncClient(base_url="https://api.example.com", api_key="sk-123")
print(client.request("/v2/items"))
```

Construct a `DataSyncClient`, then issue requests. Requests time out after the
client's configured timeout. See `src/api.py` for the exact defaults and the
full constructor signature, and `docs/API.md` for the (older) reference manual.

The README intentionally stays vague about exact numbers so it never goes
stale — always read `src/api.py` for the authoritative configuration values.
