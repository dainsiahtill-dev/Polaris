"""Backend freshness fingerprint resolver for factory-bench.

Prevents live reruns from silently validating against a stale backend process
(e.g. an old 49977 server started before the latest source-code fixes).

Design:
  1. ``compute_source_fingerprint()`` hashes key backend source directories to
     produce a deterministic source fingerprint for the *local* workspace.
  2. ``resolve_backend_fingerprint()`` queries the running backend's
     ``/v2/runtime/fingerprint`` endpoint (or ``/v2/health`` fallback) to
     obtain the backend's self-reported fingerprint.
  3. ``check_backend_freshness()`` compares the two and returns a structured
     gate result: ``fresh``, ``stale``, or ``unknown``.

The gate is fail-closed: any resolution error or missing fingerprint produces
``stale_backend_or_unknown``, which must block the bench from treating results
as real validation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path("/home/dains/Documents/polaris/src/backend")
_FINGERPRINT_SOURCES = (
    "polaris/delivery",
    "polaris/kernelone/runtime",
    "polaris/cells/factory",
    "polaris/cells/orchestration",
    "polaris/cells/director",
    "polaris/cells/roles",
    "polaris/domain/director",
    "scripts/factory_bench/run_factory_bench.py",
    "scripts/factory_bench/factory_http_client.py",
)
_FINGERPRINT_HASH_ALGO = "sha256"
_FINGERPRINT_TRUNCATE = 16


def compute_source_fingerprint(
    backend_root: Path | str | None = None,
    *,
    sources: tuple[str, ...] = _FINGERPRINT_SOURCES,
) -> str:
    """Compute a deterministic hash of key backend source files.

    Hashes the content of all ``*.py`` files under each source directory/file
    (sorted by relative path) and returns a truncated hex digest.  Returns ""
    if no source files are found (e.g. backend_root does not exist).
    """
    root = Path(backend_root) if backend_root else _BACKEND_ROOT
    if not root.is_dir():
        return ""

    h = hashlib.new(_FINGERPRINT_HASH_ALGO)
    file_count = 0

    for source in sorted(sources):
        candidate = root / source
        if candidate.is_file():
            try:
                content = candidate.read_bytes()
                h.update(source.encode("utf-8"))
                h.update(content)
                file_count += 1
            except OSError:
                continue
        elif candidate.is_dir():
            for py_file in sorted(candidate.rglob("*.py")):
                try:
                    rel = str(py_file.relative_to(root))
                    content = py_file.read_bytes()
                    h.update(rel.encode("utf-8"))
                    h.update(content)
                    file_count += 1
                except OSError:
                    continue

    if file_count == 0:
        return ""
    digest = h.hexdigest()
    return digest[:_FINGERPRINT_TRUNCATE]


def resolve_token_source(
    explicit: str | None = None,
    *,
    env_token: str | None = None,
    desktop_token: str | None = None,
    is_local: bool = False,
    default_local_token: str = "polaris-local-dev",
) -> str:
    """Determine the token source label for audit metadata.

    Returns a human-readable label like "env:FACTORY_BENCH_BACKEND_TOKEN",
    "desktop-backend.json", "default_local", or "none".
    """
    if (explicit or "").strip():
        return "arg:explicit"
    if (env_token or "").strip():
        return "env:FACTORY_BENCH_BACKEND_TOKEN"
    if (desktop_token or "").strip():
        return "desktop-backend.json"
    if is_local:
        return "default_local"
    return "none"


def _http_get_json(
    url: str,
    *,
    timeout_s: float = 5.0,
    token: str = "",
) -> dict[str, Any] | None:
    """GET a JSON endpoint. Returns parsed dict or None on any failure."""
    import urllib.error
    import urllib.request

    try:
        headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            return json.loads(raw)
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        _logger.debug("HTTP GET %s failed: %s", url, exc)
        return None


def resolve_backend_fingerprint(
    backend_url: str,
    *,
    token: str = "",
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Query the running backend for its self-reported fingerprint.

    Tries ``/v2/runtime/fingerprint`` first (purpose-built), then falls back
    to ``/v2/health`` (which may carry a ``fingerprint`` field in the future).

    Returns a dict with at least:
      - ``reachable``: bool — backend responded to any probe
      - ``fingerprint``: str — backend-reported fingerprint ("" if absent)
      - ``pid``: int | None — backend process PID (if reported)
      - ``startup_time``: str — backend startup timestamp (if reported)
      - ``source``: str — "runtime/fingerprint" or "health" or "unreachable"
    """
    base = backend_url.rstrip("/")

    # Try the purpose-built fingerprint endpoint first
    fp_url = f"{base}/v2/runtime/fingerprint"
    data = _http_get_json(fp_url, timeout_s=timeout_s, token=token)
    if isinstance(data, dict) and data.get("fingerprint"):
        return {
            "reachable": True,
            "fingerprint": str(data.get("fingerprint") or ""),
            "current_source_fingerprint": str(data.get("current_source_fingerprint") or ""),
            "stale_since_startup": bool(data.get("stale_since_startup", False)),
            "pid": data.get("pid"),
            "startup_time": str(data.get("startup_time") or ""),
            "workspace": str(data.get("workspace") or ""),
            "source": str(data.get("source") or "runtime/fingerprint"),
        }

    # Fallback: /v2/health may carry fingerprint info in the future
    health_url = f"{base}/v2/health"
    data = _http_get_json(health_url, timeout_s=timeout_s, token=token)
    if isinstance(data, dict):
        fp = str(data.get("fingerprint") or data.get("backend_fingerprint") or "")
        return {
            "reachable": True,
            "fingerprint": fp,
            "pid": data.get("pid"),
            "startup_time": str(data.get("timestamp") or ""),
            "workspace": "",
            "source": "health",
        }

    # Backend unreachable
    return {
        "reachable": False,
        "fingerprint": "",
        "pid": None,
        "startup_time": "",
        "workspace": "",
        "source": "unreachable",
    }


def check_backend_freshness(
    backend_url: str,
    *,
    token: str = "",
    timeout_s: float = 5.0,
    expected_fingerprint: str = "",
    backend_root: Path | str | None = None,
) -> dict[str, Any]:
    """Check whether the running backend matches the local source code.

    Returns a gate dict with:
      - ``gate``: "stale_backend_or_unknown"
      - ``ok``: True only when backend is reachable AND fingerprint matches
      - ``detail``: human-readable explanation
      - ``backend_url``: the URL that was probed
      - ``expected_fingerprint``: local source fingerprint
      - ``actual_fingerprint``: backend-reported fingerprint
      - ``backend_info``: raw backend fingerprint response
    """
    if not expected_fingerprint:
        expected_fingerprint = compute_source_fingerprint(backend_root)

    backend_info = resolve_backend_fingerprint(
        backend_url,
        token=token,
        timeout_s=timeout_s,
    )

    actual_fp = backend_info.get("fingerprint", "")
    reachable = bool(backend_info.get("reachable"))
    source = str(backend_info.get("source") or "")

    if not reachable:
        ok = False
        detail = f"backend unreachable at {backend_url}"
    elif not actual_fp:
        ok = False
        detail = "backend reachable but fingerprint missing (pre-R11 backend?)"
    elif source != "runtime/fingerprint:process_startup":
        ok = False
        detail = (
            "backend fingerprint endpoint uses legacy request-time semantics; "
            f"restart backend to load process-startup fingerprint support (source={source or 'unknown'})"
        )
    elif bool(backend_info.get("stale_since_startup")):
        ok = False
        detail = (
            "STALE backend: source changed after backend startup "
            f"startup={actual_fp} current={backend_info.get('current_source_fingerprint') or ''}"
        )
    elif not expected_fingerprint:
        ok = False
        detail = "local source fingerprint unavailable; cannot verify freshness"
    elif actual_fp == expected_fingerprint:
        ok = True
        detail = f"backend fresh (fingerprint={actual_fp})"
    else:
        ok = False
        detail = (
            f"STALE backend: expected={expected_fingerprint} "
            f"actual={actual_fp} (source={backend_info.get('source', 'unknown')})"
        )

    return {
        "gate": "stale_backend_or_unknown",
        "ok": ok,
        "detail": detail,
        "backend_url": backend_url,
        "expected_fingerprint": expected_fingerprint,
        "actual_fingerprint": actual_fp,
        "backend_info": backend_info,
    }


def build_run_backend_metadata(
    backend_url: str,
    *,
    token_source: str = "",
    workspace: str = "",
    expected_fingerprint: str = "",
    actual_fingerprint: str = "",
    backend_pid: int | None = None,
    backend_startup_time: str = "",
    fingerprint_source: str = "",
) -> dict[str, Any]:
    """Build the immutable backend metadata dict for run audit.

    This dict is written to both the run metadata and per-project audit
    so every result is traceable to the exact backend process.
    """
    return {
        "backend_base_url": backend_url,
        "token_source": token_source,
        "workspace": workspace,
        "expected_source_fingerprint": expected_fingerprint,
        "actual_backend_fingerprint": actual_fingerprint,
        "backend_pid": backend_pid,
        "backend_startup_time": backend_startup_time,
        "fingerprint_source": fingerprint_source,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
