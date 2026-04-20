"""Test auth behavior with empty token."""
import sys
sys.path.insert(0, 'src/backend')

from fastapi.testclient import TestClient
from app.main import create_app
from app.state import AppState, Auth, Settings

# Test with empty token
print("\n=== Test with empty token (auth disabled) ===\n")
settings = Settings()
settings.workspace = "."
settings.ramdisk_root = ""

state = AppState(settings=settings)
auth = Auth(token="")  # Empty token = auth disabled

app = create_app(state=state, auth=auth, cors_origins=["*"])
client = TestClient(app)

response = client.get("/v2/role/pm/chat/status")
print(f"Status: {response.status_code}")
print(f"Response: {response.text[:500]}")

# Test with token
print("\n=== Test with token (auth enabled) ===\n")
auth2 = Auth(token="test-token")
state2 = AppState(settings=settings)

app2 = create_app(state=state2, auth=auth2, cors_origins=["*"])
client2 = TestClient(app2)

# Without auth header
response2 = client2.get("/v2/role/pm/chat/status")
print(f"Without auth header: {response2.status_code}")

# With auth header
response3 = client2.get("/v2/role/pm/chat/status", headers={"Authorization": "Bearer test-token"})
print(f"With auth header: {response3.status_code}")
print(f"Response: {response3.text[:500]}")
