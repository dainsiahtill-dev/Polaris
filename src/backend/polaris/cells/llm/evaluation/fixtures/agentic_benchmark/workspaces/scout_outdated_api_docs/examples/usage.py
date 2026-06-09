"""Example copied from the old docs/API.md — DOES NOT WORK against v2.

Running this raises ``TypeError: __init__() got an unexpected keyword argument
'region'`` because the v2 ``DataSyncClient`` constructor no longer accepts
``region``, ``tenant_id`` or ``signing_secret``. The example is preserved here
to show the failing v1 pattern.
"""

from src.api import DataSyncClient

# BROKEN: passes 5 args following the outdated docs/API.md pattern.
client = DataSyncClient(
    base_url="https://api.example.com",
    api_key="sk-123",
    region="us-east-1",
    tenant_id="acme",
    signing_secret="shhh",
)

print(client.request("/v1/items"))
