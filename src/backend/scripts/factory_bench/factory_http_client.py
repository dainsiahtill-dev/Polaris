from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_AFTER_S = 5.0


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


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    token: str = "",
    max_retries: int = DEFAULT_MAX_RETRIES,
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
            print(f"[factory-bench] backend POST failed: {url}: {exc}", file=sys.stderr, flush=True)
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
    return _http_post_json(f"{backend_url}/v2/factory/runs", payload, token=token)


def get_run_status(
    backend_url: str,
    run_id: str,
    token: str = "",
) -> dict[str, Any] | None:
    return _http_get_json(f"{backend_url}/v2/factory/runs/{run_id}", token=token)


def get_audit_bundle(
    backend_url: str,
    run_id: str,
    token: str = "",
) -> dict[str, Any] | None:
    return _http_get_json(f"{backend_url}/v2/factory/runs/{run_id}/audit-bundle", token=token)


def get_run_artifacts(
    backend_url: str,
    run_id: str,
    token: str = "",
) -> dict[str, Any] | None:
    return _http_get_json(f"{backend_url}/v2/factory/runs/{run_id}/artifacts", token=token)


def poll_run_until_terminal(
    backend_url: str,
    run_id: str,
    token: str = "",
    poll_interval_s: float = 5.0,
    timeout_s: float = 5400.0,
    on_status: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while True:
        status = get_run_status(backend_url, run_id, token=token)
        if status is not None and on_status is not None:
            on_status(status)
        if status is None:
            if time.monotonic() >= deadline:
                print(f"[factory-bench] poll timeout: {run_id}", file=sys.stderr, flush=True)
                return None
            time.sleep(poll_interval_s)
            continue
        current_status = status.get("status", "")
        if current_status in ("completed", "failed", "cancelled"):
            return status
        if time.monotonic() >= deadline:
            print(f"[factory-bench] poll timeout: {run_id}", file=sys.stderr, flush=True)
            return None
        time.sleep(poll_interval_s)
