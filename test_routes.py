"""Test script to diagnose AI Dialogue router registration issues."""
import sys
sys.path.insert(0, 'src/backend')

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import the routers
from app.routers import role_chat, pm_chat

# Create test app
app = FastAPI(title="Test App")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Include routers
app.include_router(role_chat.router)
app.include_router(pm_chat.router)

# Print all registered routes
print("\n=== Registered Routes ===")
for route in app.routes:
    if hasattr(route, 'methods') and hasattr(route, 'path'):
        methods = list(route.methods) if route.methods else ['NO_METHODS']
        print(f"{methods} {route.path}")

print("\n=== Role Chat Router Routes ===")
for route in role_chat.router.routes:
    if hasattr(route, 'methods') and hasattr(route, 'path'):
        methods = list(route.methods) if route.methods else ['NO_METHODS']
        print(f"{methods} {route.path}")

print("\n=== PM Chat Router Routes ===")
for route in pm_chat.router.routes:
    if hasattr(route, 'methods') and hasattr(route, 'path'):
        methods = list(route.methods) if route.methods else ['NO_METHODS']
        print(f"{methods} {route.path}")

# Test if specific paths are registered
test_paths = [
    "/v2/role/chat/ping",
    "/v2/role/pm/chat/status",
    "/v2/role/architect/chat/status",
    "/v2/pm/chat/ping",
    "/v2/pm/chat/status",
]

print("\n=== Path Lookup Test ===")
for path in test_paths:
    found = False
    for route in app.routes:
        if hasattr(route, 'path') and route.path == path:
            found = True
            break
    status = "✓ FOUND" if found else "✗ NOT FOUND"
    print(f"{status}: {path}")
