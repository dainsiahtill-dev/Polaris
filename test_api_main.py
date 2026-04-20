"""Test the api.main create_app with role_chat routes."""
import sys
sys.path.insert(0, 'src/backend')

from fastapi.testclient import TestClient
from api.main import create_app
from config import Settings

# Create settings
settings = Settings()
settings.workspace = "."
settings.ramdisk_root = ""

# Create app using api.main.create_app (the one used by server.py)
app = create_app(settings=settings)
client = TestClient(app)

# Test routes
test_cases = [
    ("GET", "/v2/role/chat/ping"),
    ("GET", "/v2/role/pm/chat/status"),
    ("GET", "/v2/role/architect/chat/status"),
    ("GET", "/v2/role/chat/roles"),
    ("GET", "/v2/pm/chat/ping"),
    ("GET", "/v2/pm/chat/status"),
]

print("\n=== api.main.create_app Route Test ===\n")
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
        elif status in [307, 308]:
            print(f"→ {status} REDIRECT: {method} {path}")
        elif status == 401:
            print(f"🔒 401 UNAUTHORIZED: {method} {path}")
        else:
            print(f"? {status} RESPONSE: {method} {path}")
            print(f"  Body: {response.text[:200]}")
    except Exception as e:
        print(f"✗ ERROR: {method} {path}: {e}")

print("\n\n=== Registered V2 Routes ===\n")
routes = []
for route in app.routes:
    if hasattr(route, 'path') and hasattr(route, 'methods'):
        path = route.path
        if '/v2/' in path:
            methods = list(route.methods) if route.methods else []
            methods = [m for m in methods if m != 'HEAD']
            if methods:
                routes.append((methods, path))

for methods, path in sorted(routes, key=lambda x: x[1]):
    print(f"  {methods} {path}")
