"""Test actual route matching in FastAPI."""
import sys
sys.path.insert(0, 'src/backend')

from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock

# Create test app with full setup
app = FastAPI()

# Mock state
mock_state = MagicMock()
mock_state.settings.workspace = "."
mock_state.settings.ramdisk_root = ""
mock_state.pm.process = None
mock_state.pm.mode = None
mock_state.pm.started_at = None

mock_auth = MagicMock()
mock_auth.check.return_value = True

app.state.app_state = mock_state
app.state.auth = mock_auth

# Import and include routers
from app.routers import role_chat, pm_chat

app.include_router(role_chat.router)
app.include_router(pm_chat.router)

# Create test client
client = TestClient(app)

# Test various endpoints
test_cases = [
    ("GET", "/v2/role/chat/ping"),
    ("GET", "/v2/role/pm/chat/status"),
    ("GET", "/v2/role/architect/chat/status"),
    ("GET", "/v2/role/chat/roles"),
    ("GET", "/v2/pm/chat/ping"),
    ("GET", "/v2/pm/chat/status"),
]

print("\n=== Route Matching Test ===\n")
for method, path in test_cases:
    try:
        if method == "GET":
            response = client.get(path)
        else:
            response = client.post(path)

        status = response.status_code
        if status == 404:
            print(f"✗ 404 NOT FOUND: {method} {path}")
        elif status == 200:
            print(f"✓ 200 OK: {method} {path}")
        elif status == 307 or status == 308:
            print(f"→ {status} REDIRECT: {method} {path}")
        else:
            print(f"? {status} RESPONSE: {method} {path}")
            print(f"  Body preview: {response.text[:300]}")
    except Exception as e:
        print(f"✗ ERROR: {method} {path}: {e}")

print("\n\n=== All Registered Routes ===\n")
for route in app.routes:
    if hasattr(route, 'path'):
        methods = getattr(route, 'methods', set())
        if methods:
            print(f"  {list(methods)} {route.path}")
