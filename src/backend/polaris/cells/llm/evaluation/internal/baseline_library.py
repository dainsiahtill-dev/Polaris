"""Baseline library fetcher for external agentic-eval references.

This module fetches public benchmark reference materials (e.g. BFCL, ToolBench)
into workspace-local runtime storage so teams can keep a reproducible local
baseline catalog.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

_BASELINE_SOURCE_CATALOG: dict[str, dict[str, Any]] = {
    "bfcl": {
        "display_name": "Berkeley Function Calling Leaderboard (BFCL)",
        "homepage": "https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard",
        "files": (
            {
                "path": "README.md",
                "url": "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/README.md",
            },
            {
                "path": "TEST_CATEGORIES.md",
                "url": "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/TEST_CATEGORIES.md",
            },
            {
                "path": "SUPPORTED_MODELS.md",
                "url": "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/SUPPORTED_MODELS.md",
            },
            {
                "path": "CHANGELOG.md",
                "url": "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/CHANGELOG.md",
            },
        ),
    },
    "toolbench": {
        "display_name": "ToolBench",
        "homepage": "https://github.com/OpenBMB/ToolBench",
        "files": (
            {
                "path": "README.md",
                "url": "https://raw.githubusercontent.com/OpenBMB/ToolBench/master/README.md",
            },
            {
                "path": "README_ZH.md",
                "url": "https://raw.githubusercontent.com/OpenBMB/ToolBench/master/README_ZH.md",
            },
            {
                "path": "requirements.txt",
                "url": "https://raw.githubusercontent.com/OpenBMB/ToolBench/master/requirements.txt",
            },
        ),
    },
}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_utf8_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_utf8_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fetch_text_default(url: str, timeout_seconds: float) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Polaris-AgenticEval-BaselinePull/1.0",
            "Accept": "text/plain,application/json;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    with urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
        payload = response.read()
    return payload.decode("utf-8", errors="replace")


def _fetch_with_retry(
    *,
    url: str,
    timeout_seconds: float,
    fetcher: Callable[[str, float], str],
    max_retries: int,
) -> str:
    attempts = max(1, int(max_retries) + 1)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fetcher(url, float(timeout_seconds))
        except (RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(min(0.75, 0.2 * attempt))
    if last_error is None:
        raise RuntimeError("fetch_failed_without_error")
    raise last_error


def _normalize_sources(sources: Iterable[Any] | None) -> tuple[list[str], list[str]]:
    if sources is None:
        requested = ["all"]
    else:
        requested = []
        for item in sources:
            token = str(item or "").strip().lower()
            if token:
                requested.append(token)
        if not requested:
            requested = ["all"]

    if "all" in requested:
        all_selected = sorted(_BASELINE_SOURCE_CATALOG.keys())
        return all_selected, []

    selected_keys: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for token in requested:
        if token in seen:
            continue
        seen.add(token)
        if token in _BASELINE_SOURCE_CATALOG:
            selected_keys.append(token)
        else:
            unknown.append(token)
    return selected_keys, unknown


def list_baseline_library_sources() -> dict[str, dict[str, Any]]:
    """Return static baseline source definitions."""
    output: dict[str, dict[str, Any]] = {}
    for key, payload in _BASELINE_SOURCE_CATALOG.items():
        output[key] = {
            "display_name": str(payload.get("display_name") or ""),
            "homepage": str(payload.get("homepage") or ""),
            "files": [dict(item) for item in list(payload.get("files") or ()) if isinstance(item, Mapping)],
        }
    return output


def pull_baseline_library(
    *,
    workspace: str,
    sources: Iterable[Any] | None = None,
    output_root: str = "runtime/llm_evaluations/baselines",
    timeout_seconds: float = 20.0,
    max_retries: int = 2,
    use_cache: bool = True,
    check_only: bool = False,
    refresh_cache: bool = False,
    fetch_text: Callable[[str, float], str] | None = None,
) -> dict[str, Any]:
    """Fetch external baseline references into the workspace runtime directory."""

    if check_only and refresh_cache:
        raise ValueError("check_only and refresh_cache cannot both be true")
    if check_only and not use_cache:
        raise ValueError("check_only requires use_cache=true")

    workspace_root = Path(workspace).resolve()
    target_root = Path(output_root)
    if not target_root.is_absolute():
        target_root = workspace_root / target_root
    pull_id = _utc_timestamp()
    run_root = target_root / f"pull-{pull_id}"
    cache_root = target_root / "cache"

    selected_sources, unknown_sources = _normalize_sources(sources)
    fetcher = fetch_text if callable(fetch_text) else _fetch_text_default

    source_results: list[dict[str, Any]] = []
    for source_key in selected_sources:
        source_config = dict(_BASELINE_SOURCE_CATALOG.get(source_key) or {})
        source_dir = run_root / source_key
        source_dir.mkdir(parents=True, exist_ok=True)
        source_cache_dir = cache_root / source_key
        source_cache_dir.mkdir(parents=True, exist_ok=True)
        downloaded_files: list[dict[str, Any]] = []
        failed_files: list[dict[str, Any]] = []
        cache_hits = 0
        cache_misses = 0
        network_downloads = 0
        for file_spec_raw in list(source_config.get("files") or ()):
            file_spec = dict(file_spec_raw) if isinstance(file_spec_raw, Mapping) else {}
            relative_path = str(file_spec.get("path") or "").strip()
            url = str(file_spec.get("url") or "").strip()
            if not relative_path or not url:
                failed_files.append(
                    {
                        "path": relative_path or "unknown",
                        "url": url,
                        "error": "invalid_file_spec",
                    }
                )
                continue
            destination = source_dir / relative_path
            cache_path = source_cache_dir / relative_path
            if use_cache and cache_path.is_file() and not refresh_cache:
                content = _read_utf8_text(cache_path)
                _write_utf8_text(destination, content)
                downloaded_files.append(
                    {
                        "path": relative_path,
                        "url": url,
                        "bytes": len(content.encode("utf-8")),
                        "absolute_path": str(destination.resolve()),
                        "cache_path": str(cache_path.resolve()),
                        "origin": "cache",
                    }
                )
                cache_hits += 1
                continue

            if check_only:
                cache_misses += 1
                failed_files.append(
                    {
                        "path": relative_path,
                        "url": url,
                        "error": "cache_miss",
                        "cache_path": str(cache_path.resolve()),
                        "origin": "cache",
                    }
                )
                continue

            try:
                content = _fetch_with_retry(
                    url=url,
                    timeout_seconds=float(timeout_seconds),
                    fetcher=fetcher,
                    max_retries=int(max_retries),
                )
                _write_utf8_text(destination, content)
                if use_cache:
                    _write_utf8_text(cache_path, content)
                downloaded_files.append(
                    {
                        "path": relative_path,
                        "url": url,
                        "bytes": len(content.encode("utf-8")),
                        "absolute_path": str(destination.resolve()),
                        "cache_path": str(cache_path.resolve()) if use_cache else "",
                        "origin": "network",
                    }
                )
                network_downloads += 1
            except URLError as exc:
                failed_files.append(
                    {
                        "path": relative_path,
                        "url": url,
                        "error": f"network_error: {exc}",
                        "cache_path": str(cache_path.resolve()) if use_cache else "",
                        "origin": "network",
                    }
                )
            except (RuntimeError, ValueError) as exc:
                failed_files.append(
                    {
                        "path": relative_path,
                        "url": url,
                        "error": str(exc),
                        "cache_path": str(cache_path.resolve()) if use_cache else "",
                        "origin": "network",
                    }
                )

        source_status = "ok"
        if check_only:
            source_status = "cache_ready" if not failed_files else "cache_miss"
        elif failed_files:
            source_status = "partial_failure"

        source_manifest = {
            "source": source_key,
            "display_name": str(source_config.get("display_name") or source_key),
            "homepage": str(source_config.get("homepage") or ""),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "use_cache": bool(use_cache),
            "check_only": bool(check_only),
            "refresh_cache": bool(refresh_cache),
            "cache_root": str(source_cache_dir.resolve()),
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "network_downloads": network_downloads,
            "status": source_status,
            "downloaded_files": downloaded_files,
            "failed_files": failed_files,
        }
        source_manifest_path = source_dir / "SOURCE_MANIFEST.json"
        _write_utf8_text(
            source_manifest_path,
            json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n",
        )
        source_results.append(
            {
                "source": source_key,
                "display_name": str(source_config.get("display_name") or source_key),
                "homepage": str(source_config.get("homepage") or ""),
                "status": source_status,
                "downloaded_count": len(downloaded_files),
                "failed_count": len(failed_files),
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "network_downloads": network_downloads,
                "check_only": bool(check_only),
                "downloaded_files": downloaded_files,
                "failed_files": failed_files,
                "manifest_path": str(source_manifest_path.resolve()),
                "output_path": str(source_dir.resolve()),
                "cache_path": str(source_cache_dir.resolve()),
            }
        )

    success_status = {"ok", "cache_ready"}
    overall_ok = (
        bool(selected_sources)
        and not unknown_sources
        and all(str(item.get("status") or "") in success_status for item in source_results)
    )
    summary = {
        "pull_id": pull_id,
        "workspace": str(workspace_root),
        "output_root": str(run_root.resolve()),
        "cache_root": str(cache_root.resolve()),
        "selected_sources": selected_sources,
        "unknown_sources": unknown_sources,
        "use_cache": bool(use_cache),
        "check_only": bool(check_only),
        "refresh_cache": bool(refresh_cache),
        "source_results": source_results,
        "ok": overall_ok,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    run_manifest_path = run_root / "BASELINE_LIBRARY_PULL.json"
    _write_utf8_text(
        run_manifest_path,
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    summary["manifest_path"] = str(run_manifest_path.resolve())
    return summary


__all__ = [
    "list_baseline_library_sources",
    "pull_baseline_library",
]
