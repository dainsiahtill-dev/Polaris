# DataSync Client API Reference

> Last reviewed: 2023-11-02 (NOTE: this document has not been updated since the v1 rewrite)

## `DataSyncClient`

The `DataSyncClient` is the primary entry point for talking to the DataSync
service. Construct it once and reuse it across requests.

### Configuration

| Setting        | Default | Description                                  |
| -------------- | ------- | -------------------------------------------- |
| `timeout`      | `30`    | Per-request socket timeout, in **seconds**.  |
| `retries`      | `3`     | Number of automatic retries on 5xx.          |
| `base_url`     | (none)  | Service root URL.                            |

The default request timeout is **30 seconds**. If a call takes longer than the
configured timeout the client raises `DataSyncTimeout`.

### Constructing the client

`DataSyncClient.__init__` requires **5** parameters. All five are mandatory and
the constructor will raise `TypeError` if any are omitted:

1. `base_url` — the service root URL.
2. `api_key` — your secret API key.
3. `region` — the deployment region (e.g. `us-east-1`).
4. `tenant_id` — the calling tenant's identifier.
5. `signing_secret` — HMAC secret used to sign every request.

```python
# Documented (v1) construction pattern — REQUIRES 5 arguments
client = DataSyncClient(
    base_url="https://api.example.com",
    api_key="sk-123",
    region="us-east-1",
    tenant_id="acme",
    signing_secret="shhh",
)
```

### Notes

- `region`, `tenant_id`, and `signing_secret` were introduced in v1 and are
  required for request signing.
- The `30` second default timeout was chosen for slow batch endpoints.
