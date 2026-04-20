"""Test the full application startup and routing."""
import sys
sys.path.insert(0, 'src/backend')

from fastapi.testclient import TestClient
from app.main import create_app
from app.state import AppState, Auth, Settings

# Create settings
settings = Settings()
settings.workspace = "."
settings.ramdisk_root = ""

# Create state and auth
state = AppState(settings=settings)
auth = Auth(token="test-token")

# Create app
app = create_app(state=state, auth=auth, cors_origins=["*"])

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

print("\n=== Full App Route Matching Test ===\n")
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
            # Print response preview
            try:
                data = response.json()
                print(f"  Response: {str(data)[:200]}")
            except:
                print(f"  Response: {response.text[:200]}")
        elif status in [307, 308]:
            print(f"→ {status} REDIRECT: {method} {path}")
        elif status == 401:
            print(f"🔒 401 UNAUTHORIZED: {method} {path}")
        else:
            print(f"? {status} RESPONSE: {method} {path}")
            print(f"  Body: {response.text[:300]}")
    except Exception as e:
        import traceback
        print(f"✗ ERROR: {method} {path}: {e}")
        traceback.print_exc()

print("\n\n=== All Registered Routes (Full App) ===\n")
routes = []
for route in app.routes:
    if hasattr(route, 'path') and hasattr(route, 'methods'):
        methods = list(route.methods) if route.methods else []
        # Skip default HEAD routes
        methods = [m for m in methods if m != 'HEAD']
        if methods:
            routes.append((methods, route.path))

# Sort and print
for methods, path in sorted(routes, key=lambda x: x[1]):
    print(f"  {methods} {path}")
