"""Static guard for Polaris realtime single-rail transport."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
FRONTEND_SRC = REPO_ROOT / "src" / "frontend" / "src"
BACKEND_ROUTERS = REPO_ROOT / "src" / "backend" / "polaris" / "delivery" / "http" / "routers"

FRONTEND_FORBIDDEN = (
    "EventSource",
    "text/event-stream",
    "useWebSocketWithFallback",
    "useRuntimeWebSocketWithFallback",
    "useCourtWebSocketWithFallback",
    "fallbackInterval",
    "maxFallbackAttempts",
    "pollInterval",
    "longPolling",
    "long-poll",
    "ReadableStream",
    "getReader(",
)

BACKEND_ROUTER_FORBIDDEN = (
    "StreamingResponse",
    "text/event-stream",
)

UI_TIMER_ALLOWLIST = {
    FRONTEND_SRC / "app" / "components" / "EnhancedNotificationManager.tsx",
    FRONTEND_SRC / "app" / "components" / "LlmRuntimeOverlay.tsx",
    FRONTEND_SRC / "app" / "components" / "ai-dialogue" / "ManusStyleStatusIndicator.tsx",
    FRONTEND_SRC / "app" / "components" / "RealTimeStatusBar.tsx",
}


def _product_frontend_files() -> list[Path]:
    return [
        path
        for path in FRONTEND_SRC.rglob("*")
        if path.is_file()
        and path.suffix in {".ts", ".tsx"}
        and "__tests__" not in path.parts
        and ".test." not in path.name
    ]


def test_frontend_realtime_uses_runtime_transport_single_entrypoint() -> None:
    """Product frontend code must not restore SSE, stream readers, or polling fallback."""

    findings: list[str] = []
    websocket_hits: list[Path] = []
    timer_hits: list[Path] = []

    for path in _product_frontend_files():
        text = path.read_text(encoding="utf-8")
        for token in FRONTEND_FORBIDDEN:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")
        if "new WebSocket" in text:
            websocket_hits.append(path)
        if "setInterval(" in text:
            timer_hits.append(path)

    assert findings == []
    assert websocket_hits == [FRONTEND_SRC / "api.ts"]
    assert set(timer_hits).issubset(UI_TIMER_ALLOWLIST)


def test_backend_http_routers_do_not_expose_sse_streams() -> None:
    """Legacy stream routes must fail closed instead of returning SSE responses."""

    findings: list[str] = []
    for path in BACKEND_ROUTERS.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in BACKEND_ROUTER_FORBIDDEN:
            if token in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {token!r}")

    assert findings == []
