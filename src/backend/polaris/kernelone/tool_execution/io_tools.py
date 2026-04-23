"""Tool detection utilities for polaris Loop."""

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

try:
    from polaris.kernelone.fs.encoding import build_utf8_env
except ImportError:  # pragma: no cover - script-mode fallback
    from polaris.kernelone.fs.encoding import build_utf8_env  # type: ignore

try:
    from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds, timeout_seconds_or_none
except ImportError:  # pragma: no cover - script-mode fallback
    from polaris.kernelone.runtime.shared_types import (  # type: ignore
        normalize_timeout_seconds,
        timeout_seconds_or_none,
    )


def resolve_codex_path() -> str | None:
    def _prefer_windows_launcher(raw_path: str) -> str:
        candidate = str(raw_path or "").strip()
        if not candidate:
            return ""
        root, ext = os.path.splitext(candidate)
        if ext:
            return candidate
        for suffix in (".exe", ".cmd", ".bat", ".ps1"):
            alt = root + suffix
            if os.path.isfile(alt):
                return alt
        return candidate

    def _pick_best(paths):
        scored = []
        for item in paths:
            path = _prefer_windows_launcher(item)
            if not path:
                continue
            _, ext = os.path.splitext(path.lower())
            score = {".exe": 4, ".cmd": 3, ".bat": 2, ".ps1": 1}.get(ext, 0)
            scored.append((score, path))
        if not scored:
            return ""
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[0][1]

    path = _prefer_windows_launcher(shutil.which("codex") or "")
    if path:
        return path
    timeout_sec = normalize_timeout_seconds(
        os.environ.get("KERNELONE_PATH_RESOLVE_TIMEOUT") or os.environ.get("KERNELONE_PATH_RESOLVE_TIMEOUT", "3"),
        default=3,
    )
    timeout_val = timeout_seconds_or_none(timeout_sec, default=3)
    try:
        output = subprocess.check_output(
            ["where", "codex"],
            text=True,
            encoding="utf-8",
            errors="ignore",
            env=build_utf8_env(),
            timeout=timeout_val,
        )
        candidates = [line.strip() for line in output.splitlines() if line.strip()]
        best = _pick_best(candidates)
        if best:
            return best
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Codex path detection via which failed: {e}")
    try:
        output = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Command codex | Select-Object -ExpandProperty Source",
            ],
            text=True,
            encoding="utf-8",
            errors="ignore",
            env=build_utf8_env(),
            timeout=timeout_val,
        )
        candidates = [line.strip() for line in output.splitlines() if line.strip()]
        best = _pick_best(candidates)
        if best:
            return best
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Codex path detection via PowerShell failed: {e}")
    return None


def ensure_codex_available() -> str:
    path = resolve_codex_path()
    if not path:
        raise RuntimeError("codex command not found in PATH.")
    return path


def resolve_ollama_path() -> str | None:
    path = shutil.which("ollama")
    if path:
        return path
    timeout_sec = normalize_timeout_seconds(
        os.environ.get("KERNELONE_PATH_RESOLVE_TIMEOUT") or os.environ.get("KERNELONE_PATH_RESOLVE_TIMEOUT", "3"),
        default=3,
    )
    timeout_val = timeout_seconds_or_none(timeout_sec, default=3)
    try:
        output = subprocess.check_output(
            ["where", "ollama"],
            text=True,
            encoding="utf-8",
            errors="ignore",
            env=build_utf8_env(),
            timeout=timeout_val,
        )
        for line in output.splitlines():
            line = line.strip()
            if line:
                return line
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Ollama path detection failed: {e}")
    return None


def ensure_ollama_available() -> str:
    path = resolve_ollama_path()
    if not path:
        raise RuntimeError("ollama command not found in PATH.")
    return path


def ensure_tools_available() -> None:
    missing_execs = []
    missing_modules = []
    for name in ("ruff", "pytest", "coverage", "mypy"):
        if not shutil.which(name):
            missing_execs.append(name)
    for module in (
        "pydantic",
        "jsonschema",
        "tree_sitter",
        "tree_sitter_languages",
        "rich",
    ):
        try:
            __import__(module)
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Module import check failed for {module}: {e}")
            missing_modules.append(module)
    if missing_execs or missing_modules:
        parts = []
        if missing_execs:
            parts.append("missing executables: " + ", ".join(missing_execs))
        if missing_modules:
            parts.append("missing python modules: " + ", ".join(missing_modules))
        raise RuntimeError("Required tools not available (" + "; ".join(parts) + ").")
