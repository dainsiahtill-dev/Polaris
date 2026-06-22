from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from typing import Any, Callable

import websockets

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_AFTER_S = 5.0
FACTORY_EVENT_TAIL = 80
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "canceled"}


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    if exc.code != 429:
        return None
    raw = None
    headers = getattr(exc, "headers", None)
    if headers is not None:
        raw = headers.get("Retry-After")
    try:
        delay = float(raw) if raw is not None else 1.0
    except (TypeError, ValueError):
        delay = 1.0
    return min(max(delay, 0.0), MAX_RETRY_AFTER_S)


def _http_error_payload(exc: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        raw = ""
    parsed: Any = None
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
    return {
        "status": int(getattr(exc, "code", 0) or 0),
        "reason": str(getattr(exc, "reason", "") or ""),
        "body": raw,
        "json": parsed if isinstance(parsed, dict) else None,
    }


def _append_query_params(url: str, params: Mapping[str, str]) -> str:
    clean_params = [(key, value) for key, value in params.items() if value]
    if not clean_params:
        return url
    parsed = urllib.parse.urlsplit(url)
    existing = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = urllib.parse.urlencode(
        [*existing, *clean_params],
        quote_via=urllib.parse.quote,
    )
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _runtime_ws_url(backend_url: str, *, token: str = "", workspace: str = "") -> str:
    parsed = urllib.parse.urlparse(backend_url.rstrip("/"))
    if not parsed.netloc:
        raise ValueError(f"invalid backend url: {backend_url!r}")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query: dict[str, str] = {"protocol": "runtime.v2"}
    if token:
        query["token"] = token
    if workspace:
        query["workspace"] = workspace
    query_text = urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    return f"{scheme}://{parsed.netloc}/v2/ws/runtime?{query_text}"


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _factory_event_payload(message: Mapping[str, Any], run_id: str) -> tuple[int, dict[str, Any]] | None:
    if str(message.get("type") or "").strip().upper() != "EVENT":
        return None
    event = _as_mapping(message.get("event"))
    channel = str(event.get("channel") or "").strip()
    if channel not in {"event.factory", f"event.factory:{run_id}"}:
        return None
    payload = _as_mapping(event.get("payload"))
    if not payload:
        return None
    event_run_id = str(event.get("run_id") or payload.get("run_id") or "").strip()
    if event_run_id and event_run_id != run_id:
        return None
    payload.setdefault("run_id", run_id)
    try:
        cursor = int(message.get("cursor") or event.get("cursor") or 0)
    except (TypeError, ValueError):
        cursor = 0
    return cursor, payload


def _status_from_factory_event(
    run_id: str,
    payload: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = dict(previous or {})
    status["run_id"] = run_id
    event_type = str(payload.get("type") or payload.get("kind") or "").strip().lower()
    status["event_type"] = event_type
    status["event_payload"] = dict(payload)
    stage = str(payload.get("stage") or payload.get("phase") or "").strip()
    if stage:
        status["phase"] = stage
        status["current_stage"] = stage

    result = _as_mapping(payload.get("result"))
    result_status = str(result.get("status") or "").strip().lower()

    if event_type in {"started", "stage_started", "stage_heartbeat", "resumed", "metadata_updated"}:
        status["status"] = "running"
    elif event_type == "paused":
        status["status"] = "paused"
    elif event_type == "stage_completed":
        if result_status in {"failed", "cancelled", "canceled"}:
            status["status"] = "cancelled" if result_status in {"cancelled", "canceled"} else "failed"
        elif str(status.get("status") or "").strip().lower() not in TERMINAL_RUN_STATUSES:
            status["status"] = "running"
    elif event_type in {"completed", "failed", "cancelled", "canceled"}:
        status["status"] = "cancelled" if event_type == "canceled" else event_type

    if "status" not in status:
        status["status"] = "running"
    return status


async def _ack_runtime_cursor(ws: Any, cursor: int) -> None:
    if cursor <= 0:
        return
    await ws.send(
        json.dumps(
            {
                "type": "ACK",
                "protocol": "runtime.v2",
                "cursor": cursor,
            },
            ensure_ascii=False,
        )
    )


async def _subscribe_factory_events(ws: Any, *, run_id: str, workspace: str) -> None:
    await ws.send(
        json.dumps(
            {
                "type": "SUBSCRIBE",
                "protocol": "runtime.v2",
                "client_id": f"factory-bench-{uuid.uuid4().hex[:10]}",
                "channels": ["event.factory", f"event.factory:{run_id}"],
                "cursor": 0,
                "tail": FACTORY_EVENT_TAIL,
                "workspace": workspace,
            },
            ensure_ascii=False,
        )
    )
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        try:
            message = json.loads(str(raw))
        except json.JSONDecodeError:
            continue
        if not isinstance(message, Mapping):
            continue
        msg_type = str(message.get("type") or "").strip().upper()
        if msg_type == "SUBSCRIBED":
            return
        if msg_type == "ERROR":
            payload = _as_mapping(message.get("payload"))
            raise RuntimeError(str(payload.get("error") or payload.get("code") or "runtime.v2 subscribe failed"))


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    token: str = "",
    max_retries: int = DEFAULT_MAX_RETRIES,
    return_errors: bool = False,
) -> dict[str, Any] | None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    attempts = max(1, max_retries + 1)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8") or "{}"
            break
        except urllib.error.HTTPError as exc:
            delay = _retry_after_seconds(exc)
            if delay is not None and attempt < attempts - 1:
                print(
                    f"[factory-bench] backend POST rate-limited: {url}; retrying in {delay:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
                continue
            error_payload = _http_error_payload(exc)
            print(
                f"[factory-bench] backend POST failed: {url}: {exc}; body={error_payload.get('body') or ''}",
                file=sys.stderr,
                flush=True,
            )
            if return_errors:
                return {"_http_error": error_payload}
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"[factory-bench] backend POST failed: {url}: {exc}", file=sys.stderr, flush=True)
            return None
    else:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _http_get_json(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    token: str = "",
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any] | None:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    attempts = max(1, max_retries + 1)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8") or "{}"
            break
        except urllib.error.HTTPError as exc:
            delay = _retry_after_seconds(exc)
            if delay is not None and attempt < attempts - 1:
                print(
                    f"[factory-bench] backend GET rate-limited: {url}; retrying in {delay:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
                continue
            print(f"[factory-bench] backend GET failed: {url}: {exc}", file=sys.stderr, flush=True)
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"[factory-bench] backend GET failed: {url}: {exc}", file=sys.stderr, flush=True)
            return None
    else:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def start_factory_run(
    backend_url: str,
    payload: dict[str, Any],
    token: str = "",
) -> dict[str, Any] | None:
    return _http_post_json(f"{backend_url}/v2/factory/runs", payload, token=token, return_errors=True)


def get_run_status(
    backend_url: str,
    run_id: str,
    token: str = "",
    workspace: str = "",
) -> dict[str, Any] | None:
    url = _append_query_params(f"{backend_url}/v2/factory/runs/{run_id}", {"workspace": workspace})
    return _http_get_json(url, token=token)


def cancel_factory_run(
    backend_url: str,
    run_id: str,
    *,
    reason: str = "",
    token: str = "",
    workspace: str = "",
) -> dict[str, Any] | None:
    payload = {
        "action": "cancel",
        "reason": reason or "factory-bench cancelled run",
    }
    url = _append_query_params(f"{backend_url}/v2/factory/runs/{run_id}/control", {"workspace": workspace})
    return _http_post_json(url, payload, token=token)


def get_audit_bundle(
    backend_url: str,
    run_id: str,
    token: str = "",
    workspace: str = "",
) -> dict[str, Any] | None:
    url = _append_query_params(f"{backend_url}/v2/factory/runs/{run_id}/audit-bundle", {"workspace": workspace})
    return _http_get_json(url, token=token)


def get_run_artifacts(
    backend_url: str,
    run_id: str,
    token: str = "",
    workspace: str = "",
) -> dict[str, Any] | None:
    url = _append_query_params(f"{backend_url}/v2/factory/runs/{run_id}/artifacts", {"workspace": workspace})
    return _http_get_json(url, token=token)


def wait_run_until_terminal(
    backend_url: str,
    run_id: str,
    token: str = "",
    workspace: str = "",
    timeout_s: float = 5400.0,
    on_status: Callable[[dict[str, Any]], None] | None = None,
    initial_status: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        return asyncio.run(
            _wait_run_until_terminal_async(
                backend_url,
                run_id,
                token=token,
                workspace=workspace,
                timeout_s=timeout_s,
                on_status=on_status,
                initial_status=initial_status,
            )
        )
    except RuntimeError as exc:
        print(f"[factory-bench] event wait failed: {run_id}: {exc}", file=sys.stderr, flush=True)
        return None


async def _wait_run_until_terminal_async(
    backend_url: str,
    run_id: str,
    *,
    token: str = "",
    workspace: str = "",
    timeout_s: float = 5400.0,
    on_status: Callable[[dict[str, Any]], None] | None = None,
    initial_status: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout_s)
    latest_status: dict[str, Any] = {"run_id": run_id, **dict(initial_status or {})}
    latest_status.setdefault("status", "running")
    if on_status is not None:
        on_status(latest_status)

    ws_url = _runtime_ws_url(backend_url, token=token, workspace=workspace)
    try:
        async with websockets.connect(ws_url, ping_interval=30) as ws:
            await _subscribe_factory_events(ws, run_id=run_id, workspace=workspace)
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    print(f"[factory-bench] event wait timeout: {run_id}", file=sys.stderr, flush=True)
                    return None
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                try:
                    message = json.loads(str(raw))
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, Mapping):
                    continue
                event_payload = _factory_event_payload(message, run_id)
                if event_payload is None:
                    continue
                cursor, payload = event_payload
                await _ack_runtime_cursor(ws, cursor)
                latest_status = _status_from_factory_event(run_id, payload, latest_status)
                if on_status is not None:
                    on_status(latest_status)
                current_status = str(latest_status.get("status") or "").strip().lower()
                if current_status in TERMINAL_RUN_STATUSES:
                    if current_status == "canceled":
                        latest_status["status"] = "cancelled"
                    return latest_status
    except asyncio.TimeoutError:
        print(f"[factory-bench] event wait timeout: {run_id}", file=sys.stderr, flush=True)
        return None
    except (OSError, ValueError, RuntimeError, websockets.exceptions.WebSocketException) as exc:
        print(f"[factory-bench] runtime.v2 event wait failed: {run_id}: {exc}", file=sys.stderr, flush=True)
        return None
