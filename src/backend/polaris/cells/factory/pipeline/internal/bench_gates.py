"""Factory-bench goal gates and audit attribution.

The public bench runner remains a delivery harness.  The platform-owned facts
that decide whether a generated project is actually runnable live here, inside
the ``factory.pipeline`` cell boundary.
"""

from __future__ import annotations

import json
import os as _os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import suppress
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, Path as _Path
from typing import Any

from polaris.cells.control_plane.verifier_execution.public import (
    RunVerifierPolicyCommandV1,
    run_verifier_policy,
)
from polaris.cells.control_plane.verifier_policy.public import (
    ReadVerifierPolicyQueryV1,
    read_verifier_policy,
)
from polaris.kernelone.events.final_request_evidence import normalize_context_snapshot_ref

from .run_ledger import summarize_run_ledger_projection

_REQUIRED_LLM_ROLES = ("pm", "chief_engineer", "qa", "director")
_ROLE_ALIASES = {
    "ce": "chief_engineer",
    "chief engineer": "chief_engineer",
    "chief-engineer": "chief_engineer",
    "chiefeng": "chief_engineer",
    "chief_engineer": "chief_engineer",
    "pm": "pm",
    "qa": "qa",
    "director": "director",
    "architect": "architect",
}
_ROLE_FAMILIES: dict[str, tuple[tuple[str, ...], ...]] = {
    "pm": (("kimi",),),
    "chief_engineer": (("kimi",),),
    "qa": (("minimax",), ("mini", "max")),
    "director": (("qwen", "3.6", "27"), ("qwen3.6",), ("qwen", "27b")),
}
_PY_ENTRYPOINT_NAMES = ("main.py", "app.py", "cli.py", "__main__.py")
_CPP_SOURCE_SUFFIXES = (".cc", ".cpp", ".cxx")
_ENTRYPOINT_FAILURE_MARKER_RE = re.compile(r"(?im)^\s*FAIL(?:ED)?(?:\b|:)")
_FAILURE_CATEGORIES = {
    "pm_contract",
    "chief_engineer_blueprint",
    "director_tool_execution",
    "repair_convergence",
    "task_boundary",
    "llm_output",
    "context_budget",
    "control_plane",
    "target_project_baseline",
    "runtime_environment",
    "unknown",
}


def _norm_text(value: Any) -> str:
    return str(value or "").strip()


def _norm_role(value: Any) -> str:
    raw = _norm_text(value).lower().replace("-", "_")
    return _ROLE_ALIASES.get(raw, raw)


def _tail(value: str, limit: int = 1600) -> str:
    text = str(value or "")
    return text[-limit:]


def _to_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _run_command(
    command: list[str], cwd: Path, *, timeout_s: int, extra_env: dict[str, str] | None = None
) -> dict[str, Any]:
    started = time.time()
    env = None
    if extra_env:
        env = {**_os.environ, **extra_env}
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout_s)),
            check=False,
            env=env,
        )
        return {
            "command": command,
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": _tail(proc.stdout),
            "stderr_tail": _tail(proc.stderr),
            "timeout": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": None,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": _tail(_to_text(exc.stdout)),
            "stderr_tail": _tail(_to_text(exc.stderr)),
            "timeout": True,
        }
    except OSError as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": None,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": "",
            "stderr_tail": str(exc),
            "timeout": False,
        }


def _entrypoint_has_failure_marker(result: dict[str, Any]) -> bool:
    output = f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}"
    return bool(_ENTRYPOINT_FAILURE_MARKER_RE.search(output))


def _mark_entrypoint_failure(result: dict[str, Any]) -> dict[str, Any]:
    return {
        **result,
        "ok": False,
        "detail": "entrypoint output contained a failure marker",
        "failure_marker": True,
    }


def _load_package_json(workspace: Path) -> dict[str, Any]:
    package_path = workspace / "package.json"
    if not package_path.is_file():
        return {}
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _has_package_dependencies(package: dict[str, Any]) -> bool:
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        if isinstance(package.get(key), dict) and package[key]:
            return True
    return False


def _package_declares_dependency(package: dict[str, Any], dependency_name: str) -> bool:
    target = str(dependency_name or "").strip()
    if not target:
        return False
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = package.get(key)
        if isinstance(section, dict) and target in {str(name).strip() for name in section}:
            return True
    return False


def _package_has_local_tsc(workspace: Path) -> bool:
    local_name = "tsc.cmd" if sys.platform.startswith("win") else "tsc"
    return (workspace / "node_modules" / ".bin" / local_name).is_file()


def _script_uses_tsc(command: Any) -> bool:
    text = str(command or "")
    if not text.strip():
        return False
    try:
        tokens = shlex.split(text)
    except ValueError:
        return "tsc" in text
    return any(Path(token).name.lower() == "tsc" for token in tokens)


def _package_requires_project_typescript(
    workspace: Path,
    package: dict[str, Any],
    scripts: dict[str, Any],
    code_files: list[str],
) -> bool:
    has_ts_files = bool(_files_with_suffix(code_files, (".ts", ".tsx")))
    if not has_ts_files and not (workspace / "tsconfig.json").is_file():
        return False
    return any(_script_uses_tsc(value) for value in scripts.values()) or (workspace / "tsconfig.json").is_file()


def _script_tokens(command: object) -> list[str]:
    if not isinstance(command, str) or not command.strip():
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _has_shell_chaining(command: str) -> bool:
    return any(token in command for token in ("&&", "||", "|", ";"))


def _inline_eval_code(tokens: list[str]) -> str:
    for index, token in enumerate(tokens):
        if token in {"-e", "--eval"} and index + 1 < len(tokens):
            return tokens[index + 1]
    return ""


def _is_fake_npm_lifecycle_script(command: object) -> bool:
    """Return True for npm lifecycle scripts that only print success text."""
    if not isinstance(command, str) or not command.strip() or _has_shell_chaining(command):
        return False
    tokens = _script_tokens(command)
    if not tokens:
        return False
    executable = Path(tokens[0]).name.lower()
    if executable == "echo":
        return len(tokens) > 1
    code = _inline_eval_code(tokens)
    if not code:
        return False
    lowered = code.lower()
    runner_tokens = {Path(token).name.lower() for token in tokens}
    inline_runner = bool(runner_tokens & {"node", "bun", "tsx"})
    prints_only = ("console.log" in lowered or "print(" in lowered) and "require(" not in lowered
    return inline_runner and prints_only


def _is_npm_test_script_manifest_only(command: object) -> bool:
    """Return True when a test script only validates manifests or build folders."""
    tokens = _script_tokens(command)
    if not tokens:
        return False
    code = _inline_eval_code(tokens)
    if not code:
        return False
    lowered = code.lower()
    manifest_markers = (
        "manifest check passed",
        "package.json",
        "tsconfig.json",
        "existssync('dist",
        'existssync("dist',
    )
    return any(marker in lowered for marker in manifest_markers)


def _is_npm_test_script_placeholder(command: object) -> bool:
    """Return True for npm test scripts that only announce missing/fake tests."""
    if not isinstance(command, str) or not command.strip() or _has_shell_chaining(command):
        return False
    tokens = _script_tokens(command)
    if not tokens or Path(tokens[0]).name.lower() != "echo":
        return False
    lowered = " ".join(tokens[1:]).lower()
    placeholders = (
        "no tests specified",
        "all tests passed",
        "tests not implemented",
        "no tests yet",
    )
    return any(marker in lowered for marker in placeholders)


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def _html_local_resource_refs(workspace: Path, html_rel: str, html: str) -> list[str]:
    """Return local resources explicitly referenced by an HTML entrypoint."""
    resource_refs: list[str] = []
    html_parent = Path(html_rel).parent
    workspace_root = workspace.resolve()
    tag_re = re.compile(r"<(?P<tag>script|link|img|source|video|audio|iframe)\b(?P<attrs>[^>]*)>", re.IGNORECASE)
    attr_re = re.compile(
        r"(?P<name>[a-zA-Z_:][-.\w:]*)\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
        re.DOTALL,
    )
    ignored_schemes = {"http", "https", "data", "blob", "mailto", "tel", "javascript"}

    def srcset_urls(value: str) -> list[str]:
        output: list[str] = []
        for candidate in str(value or "").split(","):
            token = candidate.strip().split()
            if token:
                output.append(token[0])
        return output

    for tag_match in tag_re.finditer(html):
        tag = tag_match.group("tag").lower()
        attrs = {
            attr_match.group("name").lower(): attr_match.group("value").strip()
            for attr_match in attr_re.finditer(tag_match.group("attrs") or "")
        }
        raw_refs = [attrs[key] for key in ("src", "href") if attrs.get(key)]
        if tag in {"img", "source"} and attrs.get("srcset"):
            raw_refs.extend(srcset_urls(attrs["srcset"]))
        if tag == "link":
            rel = attrs.get("rel", "").lower()
            if "icon" in rel:
                continue
            if not any(marker in rel for marker in ("stylesheet", "preload", "modulepreload", "manifest")):
                continue
        for raw_ref in raw_refs:
            if not raw_ref or raw_ref.startswith("#") or raw_ref.startswith("//"):
                continue
            parsed = urllib.parse.urlparse(raw_ref)
            if parsed.scheme.lower() in ignored_schemes:
                continue
            resource_path = parsed.path
            if not resource_path:
                continue
            if Path(resource_path).name.lower() == "favicon.ico":
                continue
            rel_path = Path(resource_path.lstrip("/")) if resource_path.startswith("/") else html_parent / resource_path
            try:
                candidate = (workspace / rel_path).resolve()
                candidate.relative_to(workspace_root)
            except ValueError:
                resource_refs.append(raw_ref)
                continue
            if not candidate.is_file():
                resource_refs.append(raw_ref)
    return resource_refs


def _missing_html_local_resources(workspace: Path, html_rel: str) -> list[str]:
    try:
        html = (workspace / html_rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [html_rel]
    return _html_local_resource_refs(workspace, html_rel, html)


def _is_local_web_resource_failure(page_url: str, resource_url: str) -> bool:
    page = urllib.parse.urlparse(page_url)
    resource = urllib.parse.urlparse(resource_url)
    if resource.scheme not in {"http", "https"}:
        return False
    if (resource.scheme, resource.hostname, resource.port) != (page.scheme, page.hostname, page.port):
        return False
    return not resource.path.lower().endswith("/favicon.ico")


def _is_ignorable_web_console_error(message: str) -> bool:
    lowered = str(message or "").lower()
    return (
        "failed to load resource" in lowered
        or "net::err_" in lowered
        or "favicon.ico" in lowered
        or "source map" in lowered
        or "sourcemap" in lowered
    )


def _canvas_smoke_ok(canvas_states: list[dict[str, Any]]) -> bool:
    if not canvas_states:
        return True
    return any(bool(item.get("non_blank")) for item in canvas_states)


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _png_has_nonblank_pixels(data: bytes) -> bool:
    """Return True when an 8-bit PNG contains visible non-transparent pixels."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    width = height = bit_depth = color_type = 0
    idat = bytearray()
    while offset + 8 <= len(data):
        chunk_len = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + chunk_len]
        offset += 12 + chunk_len
        if chunk_type == b"IHDR" and len(chunk_data) >= 13:
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    if width <= 0 or height <= 0 or bit_depth != 8 or not idat:
        return False
    channels_by_type = {0: 1, 2: 3, 4: 2, 6: 4}
    channels = channels_by_type.get(color_type)
    if channels is None:
        return False
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return False
    stride = width * channels
    previous = bytearray(stride)
    cursor = 0
    for _row in range(height):
        if cursor >= len(raw):
            return False
        filter_type = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        if len(row) != stride:
            return False
        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                row[index] = (row[index] + left) & 0xFF
            elif filter_type == 2:
                row[index] = (row[index] + above) & 0xFF
            elif filter_type == 3:
                row[index] = (row[index] + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                row[index] = (row[index] + _paeth_predictor(left, above, upper_left)) & 0xFF
            elif filter_type != 0:
                return False
        for pixel in range(0, stride, channels):
            if color_type == 0 and row[pixel] != 0:
                return True
            if color_type == 2 and any(row[pixel : pixel + 3]):
                return True
            if color_type == 4 and row[pixel + 1] != 0:
                return True
            if color_type == 6 and row[pixel + 3] != 0:
                return True
        previous = row
    return False


def _smoke_static_web(workspace: Path, html_rel: str, *, timeout_s: int) -> dict[str, Any]:
    """Smoke-test a static HTML entrypoint using Playwright for real browser verification.

    Falls back to simple HTTP check if Playwright is unavailable.
    """
    # Try Playwright first for real browser verification
    try:
        return _smoke_static_web_playwright(workspace, html_rel, timeout_s=timeout_s)
    except ImportError:
        pass  # Playwright not available, fall back to HTTP check
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "kind": "web_playwright",
            "ok": False,
            "entrypoint": html_rel,
            "duration_s": 0,
            "detail": f"Playwright error: {exc}",
        }

    # Fallback: simple HTTP check
    handler = partial(_QuietStaticHandler, directory=str(workspace))
    started = time.time()
    server: ThreadingHTTPServer | None = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = int(server.server_address[1])
        path = urllib.request.pathname2url(html_rel)
        url = f"http://127.0.0.1:{port}/{path}"
        with urllib.request.urlopen(url, timeout=max(1, min(10, int(timeout_s)))) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
        missing_resources = _missing_html_local_resources(workspace, html_rel)
        ok = response.status == 200 and "<html" in body.lower() and not missing_resources
        if missing_resources:
            detail = f"static web entrypoint references missing local resources: {', '.join(missing_resources[:5])}"
        elif ok:
            detail = "static web entrypoint served over local HTTP"
        else:
            detail = "static web response did not look like HTML"
        return {
            "kind": "web_static",
            "ok": ok,
            "url": url,
            "entrypoint": html_rel,
            "duration_s": round(time.time() - started, 3),
            "missing_resources": missing_resources,
            "detail": detail,
        }
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {
            "kind": "web_static",
            "ok": False,
            "entrypoint": html_rel,
            "duration_s": round(time.time() - started, 3),
            "detail": str(exc),
        }
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def _smoke_static_web_playwright(workspace: Path, html_rel: str, *, timeout_s: int) -> dict[str, Any]:
    """Use Playwright to verify the HTML entrypoint renders correctly."""
    from playwright.sync_api import sync_playwright

    handler = partial(_QuietStaticHandler, directory=str(workspace))
    started = time.time()
    server: ThreadingHTTPServer | None = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = int(server.server_address[1])
        path = urllib.request.pathname2url(html_rel)
        url = f"http://127.0.0.1:{port}/{path}"
        missing_resources = _missing_html_local_resources(workspace, html_rel)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            console_errors: list[str] = []
            browser_resource_failures: list[str] = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            def record_response(response: Any) -> None:
                try:
                    status = int(response.status)
                    resource_url = str(response.url)
                except (TypeError, ValueError, AttributeError):
                    return
                if status >= 400 and _is_local_web_resource_failure(url, resource_url):
                    browser_resource_failures.append(f"{status} {resource_url}")

            def record_request_failure(request: Any) -> None:
                try:
                    resource_url = str(request.url)
                    failure = request.failure
                    error_text = str(failure.get("errorText") if isinstance(failure, dict) else failure)
                except (TypeError, AttributeError):
                    return
                if _is_local_web_resource_failure(url, resource_url):
                    browser_resource_failures.append(f"request_failed {resource_url} {error_text}")

            page.on("response", record_response)
            page.on("requestfailed", record_request_failure)

            response = page.goto(url, timeout=max(1, min(10, int(timeout_s))) * 1000)
            page.wait_for_load_state("networkidle", timeout=5000)
            http_status = int(response.status) if response is not None else 0
            page.wait_for_timeout(750)

            canvas_states = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('canvas')).map((canvas) => {
                  const rect = canvas.getBoundingClientRect();
                  const state = {
                    width: canvas.width,
                    height: canvas.height,
                    clientWidth: Math.round(rect.width),
                    clientHeight: Math.round(rect.height),
                    context_type: '',
                    non_blank: false,
                    sample_error: ''
                  };
                  try {
                    const ctx = canvas.getContext('2d');
                    if (ctx && canvas.width > 0 && canvas.height > 0) {
                      state.context_type = '2d';
                      const width = Math.min(canvas.width, 96);
                      const height = Math.min(canvas.height, 96);
                      const data = ctx.getImageData(0, 0, width, height).data;
                      for (let i = 0; i < data.length; i += 4) {
                        if (data[i] !== 0 || data[i + 1] !== 0 || data[i + 2] !== 0 || data[i + 3] !== 0) {
                          state.non_blank = true;
                          break;
                        }
                      }
                      return state;
                    }
                    const gl =
                      canvas.getContext('webgl2') ||
                      canvas.getContext('webgl') ||
                      canvas.getContext('experimental-webgl');
                    if (gl && canvas.width > 0 && canvas.height > 0) {
                      state.context_type = 'webgl';
                      const width = Math.min(canvas.width, 96);
                      const height = Math.min(canvas.height, 96);
                      const pixels = new Uint8Array(width * height * 4);
                      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
                      for (let i = 0; i < pixels.length; i += 4) {
                        if (pixels[i] !== 0 || pixels[i + 1] !== 0 || pixels[i + 2] !== 0 || pixels[i + 3] !== 0) {
                          state.non_blank = true;
                          break;
                        }
                      }
                    }
                    if (!state.non_blank && canvas.width > 0 && canvas.height > 0) {
                      const snapshot = canvas.toDataURL('image/png');
                      const blank = document.createElement('canvas');
                      blank.width = canvas.width;
                      blank.height = canvas.height;
                      state.non_blank = snapshot !== blank.toDataURL('image/png');
                    }
                  } catch (error) {
                    state.sample_error = String(error);
                    if (state.sample_error.toLowerCase().includes('taint')) {
                      state.non_blank = true;
                    }
                  }
                  return state;
                })
                """
            )
            canvas_screenshot_non_blank = False
            canvas_screenshot_errors: list[str] = []
            if isinstance(canvas_states, list) and canvas_states and not _canvas_smoke_ok(canvas_states):
                for canvas_handle in page.query_selector_all("canvas"):
                    blank_handle: Any | None = None
                    try:
                        canvas_png = canvas_handle.screenshot(timeout=2000)
                        blank_handle = page.evaluate_handle(
                            """
                            (canvas) => {
                              const rect = canvas.getBoundingClientRect();
                              const blank = document.createElement('canvas');
                              blank.width = canvas.width;
                              blank.height = canvas.height;
                              blank.style.width = `${Math.round(rect.width)}px`;
                              blank.style.height = `${Math.round(rect.height)}px`;
                              blank.style.position = 'fixed';
                              blank.style.left = '0px';
                              blank.style.top = '0px';
                              blank.style.pointerEvents = 'none';
                              blank.setAttribute('data-polaris-canvas-probe', 'blank');
                              document.body.appendChild(blank);
                              return blank;
                            }
                            """,
                            canvas_handle,
                        )
                        blank_element = blank_handle.as_element()
                        blank_png = blank_element.screenshot(timeout=2000) if blank_element is not None else b""
                    except (OSError, RuntimeError, ValueError) as exc:
                        canvas_screenshot_errors.append(str(exc))
                        continue
                    finally:
                        if blank_handle is not None:
                            with suppress(OSError, RuntimeError, ValueError):
                                blank_handle.evaluate("(blank) => blank.remove()")
                    canvas_bytes = bytes(canvas_png)
                    blank_bytes = bytes(blank_png)
                    if (blank_bytes and canvas_bytes != blank_bytes) or (
                        not blank_bytes and _png_has_nonblank_pixels(canvas_bytes)
                    ):
                        canvas_screenshot_non_blank = True
                        break

            browser.close()

        critical_errors = [err for err in console_errors if not _is_ignorable_web_console_error(err)]
        status_ok = 200 <= http_status < 400
        canvas_ok = _canvas_smoke_ok(canvas_states if isinstance(canvas_states, list) else [])
        canvas_ok = canvas_ok or canvas_screenshot_non_blank
        ok = (
            status_ok
            and len(critical_errors) == 0
            and len(missing_resources) == 0
            and len(browser_resource_failures) == 0
            and canvas_ok
        )
        if not status_ok:
            detail = f"HTTP status {http_status} for static web entrypoint"
        elif missing_resources:
            detail = f"HTML references missing local resources: {', '.join(missing_resources[:5])}"
        elif browser_resource_failures:
            detail = f"Browser resource failures: {'; '.join(browser_resource_failures[:3])}"
        elif critical_errors:
            detail = f"Console errors: {'; '.join(critical_errors[:3])}"
        elif not canvas_ok:
            detail = "Canvas entrypoint did not render non-empty pixels"
        else:
            detail = "Playwright verification passed"
        return {
            "kind": "web_playwright",
            "ok": ok,
            "url": url,
            "entrypoint": html_rel,
            "duration_s": round(time.time() - started, 3),
            "http_status": http_status,
            "console_errors": console_errors,
            "resource_failures": browser_resource_failures,
            "missing_resources": missing_resources,
            "canvas_states": canvas_states,
            "has_canvas": bool(canvas_states),
            "canvas_non_blank": canvas_ok,
            "canvas_screenshot_non_blank": canvas_screenshot_non_blank,
            "canvas_screenshot_errors": canvas_screenshot_errors,
            "detail": detail,
        }
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def _find_html_entrypoint(workspace: Path, code_files: list[str]) -> str:
    candidates = [rel for rel in code_files if rel.lower().endswith(".html")]
    for preferred in ("index.html", "public/index.html", "src/index.html"):
        if preferred in candidates and (workspace / preferred).is_file():
            return preferred
    for candidate in candidates:
        if (workspace / candidate).is_file():
            return candidate
    return ""


def _find_python_entrypoint(workspace: Path, code_files: list[str]) -> str:
    py_files = [rel for rel in code_files if rel.lower().endswith(".py")]
    by_name = {Path(rel).name: rel for rel in py_files}
    for name in _PY_ENTRYPOINT_NAMES:
        if name in by_name:
            return by_name[name]
    for rel in py_files:
        try:
            text = (workspace / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "__main__" in text:
            return rel
    return ""


def _files_with_suffix(code_files: list[str], suffixes: tuple[str, ...]) -> list[str]:
    lowered = tuple(suffix.lower() for suffix in suffixes)
    return [rel for rel in code_files if rel.lower().endswith(lowered)]


def _which_any(*names: str) -> str:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return ""


def _cli_smoke_result(kind: str, entrypoint: str, result: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": kind, "entrypoint": entrypoint, **result}
    if _entrypoint_has_failure_marker(payload):
        return _mark_entrypoint_failure(payload)
    if result.get("ok"):
        return payload
    output = f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}".lower()
    if result.get("timeout"):
        payload["ok"] = False
        payload["started"] = True
        return payload
    if (
        result.get("returncode") in {1, 2}
        and "usage" in output
        and "traceback" not in output
        and "syntaxerror" not in output
        and "exception" not in output
    ):
        payload["ok"] = True
        payload["usage_screen"] = True
        return payload
    return payload


def _collect_go_local_imports(workspace: Path, go_files: list[str]) -> list[tuple[str, str]]:
    """Return ``[(file_rel, import_path), ...]`` for all non-stdlib Go imports.

    A non-stdlib import is one whose first path segment contains a dot,
    hyphen, or underscore (e.g. ``ascii-pet-terminal/src/engine``).
    """
    import re as _re

    # Match both single-line ``import "path"`` and block-style ``\t"path"``.
    _block_import_re = _re.compile(r'^\s*"([^"]+)"', _re.MULTILINE)
    _single_import_re = _re.compile(r'import\s+"([^"]+)"')
    results: list[tuple[str, str]] = []
    for rel in go_files[:40]:
        try:
            text = (workspace / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen: set[str] = set()
        for imp in list(_block_import_re.findall(text)) + list(_single_import_re.findall(text)):
            if imp in seen:
                continue
            seen.add(imp)
            first_seg = imp.split("/")[0]
            if "." in first_seg or "-" in first_seg or "_" in first_seg:
                results.append((rel, imp))
    return results


def _discover_go_package_dirs(workspace: Path) -> set[str]:
    """Return the set of relative directory paths that contain ``.go`` files.

    These are the valid Go package import sub-paths (e.g. ``src/engine``,
    ``src/models``).  Used to repair hallucinated sub-paths.
    """
    dirs: set[str] = set()
    try:
        for p in workspace.rglob("*.go"):
            rel_dir = str(p.parent.relative_to(workspace))
            if rel_dir == ".":
                continue
            # Skip vendored and hidden directories.
            if "/." in rel_dir or rel_dir.startswith(".") or "/vendor/" in rel_dir:
                continue
            dirs.add(rel_dir)
    except OSError:
        pass
    return dirs


def _repair_go_import_subpath(import_path: str, canonical_module: str, pkg_dirs: set[str]) -> str:
    """Repair a Go import path's sub-path to match an actual package directory.

    If the import is ``canonical_module/example/pet-ascii/src/engine`` but the
    actual package directory is ``src/engine``, returns
    ``canonical_module/src/engine``.  Returns the original path when no repair
    is needed or possible.
    """
    prefix = canonical_module + "/"
    if not import_path.startswith(prefix):
        return import_path
    subpath = import_path[len(prefix) :]
    # Already valid?
    if subpath in pkg_dirs:
        return import_path
    # Try to find the best matching actual directory by suffix match.
    # E.g. ``example/pet-ascii/src/engine`` ends with ``src/engine``.
    best: str = ""
    for d in pkg_dirs:
        if (subpath.endswith("/" + d) or subpath.endswith(d)) and len(d) > len(best):
            best = d
    if best:
        return canonical_module + "/" + best
    return import_path


def _normalize_go_imports(workspace: Path, go_files: list[str], canonical_module: str) -> int:
    """Detect inconsistent Go import paths without mutating the workspace.

    ``bench_gates.py`` is a measurement gate, not a repair executor. The old
    implementation rewrote Go files here; that behavior belongs in the
    Director Repair Kernel. This compatibility helper now remains read-only
    and returns the number of files modified, which is always ``0``.
    """

    # Build a comprehensive file list: declared + discovered on disk.
    all_go: set[str] = set(go_files)
    try:
        for p in workspace.rglob("*.go"):
            rel = str(p.relative_to(workspace))
            # Skip vendored and hidden directories.
            if "/." in rel or rel.startswith(".") or "/vendor/" in rel:
                continue
            all_go.add(rel)
    except OSError:
        pass

    file_list = sorted(all_go)
    local_imports = _collect_go_local_imports(workspace, file_list)
    if not local_imports:
        return 0

    # Phase 1: Prefix normalization.
    prefixes = {imp.split("/")[0] for _, imp in local_imports}
    prefixes.discard(canonical_module)

    # Phase 2: Discover actual package directories for sub-path repair.
    pkg_dirs = _discover_go_package_dirs(workspace)

    # Build a replacement map: old_import → new_import.
    replacements: dict[str, str] = {}
    for _, imp in local_imports:
        repaired = imp
        # Fix prefix.
        first_seg = imp.split("/")[0]
        if first_seg != canonical_module and first_seg in prefixes:
            repaired = canonical_module + "/" + imp[len(first_seg) + 1 :]
        # Fix sub-path.
        repaired = _repair_go_import_subpath(repaired, canonical_module, pkg_dirs)
        if repaired != imp:
            replacements[imp] = repaired

    return 0


def _infer_go_module_name(workspace: Path, go_files: list[str]) -> str:
    """Infer the Go module name from import paths in source files.

    Scans Go files for non-stdlib import paths and extracts the common
    top-level module prefix (e.g. ``"ascii-pet-terminal"`` from
    ``"ascii-pet-terminal/src/engine"``).  Falls back to the workspace
    directory name when no local imports are found.
    """
    local_imports = _collect_go_local_imports(workspace, go_files)
    if not local_imports:
        return workspace.name or "generated"

    # Count occurrences of each prefix to find the dominant module name.
    prefix_counts: dict[str, int] = {}
    for _, imp in local_imports:
        first_seg = imp.split("/")[0]
        prefix_counts[first_seg] = prefix_counts.get(first_seg, 0) + 1

    # Return the most common prefix (the one the majority of files agree on).
    return max(prefix_counts, key=lambda p: prefix_counts[p])


def _read_go_mod_module(workspace: Path) -> str:
    """Read the module name from ``go.mod``, or return ``""`` if absent."""
    import re as _re

    go_mod = workspace / "go.mod"
    if not go_mod.is_file():
        return ""
    try:
        text = go_mod.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = _re.search(r"^module\s+(\S+)", text, _re.MULTILINE)
    return m.group(1) if m else ""


def _repair_go_duplicate_declarations(workspace: Path, go_files: list[str]) -> int:
    """Detect Go redeclarations without mutating the workspace.

    Historical bench code merged files and removed duplicates here. That is a
    Director Repair Kernel responsibility. The gate now remains read-only and
    returns ``0`` modified files.
    """
    return 0


def _go_command(workspace: Path, go_files: list[str]) -> list[str]:
    go = _resolve_go_binary()
    if not go:
        return []
    if (workspace / "go.mod").is_file():
        return [go, "test", "./..."]
    # No go.mod: do not run ``go mod init`` in the measurement gate. The
    # Director Repair Kernel must materialize module metadata when needed.
    if go_files:
        return [go, "vet", go_files[0]]
    return []


def _rust_compile_command(workspace: Path, rust_files: list[str]) -> list[str]:
    cargo = shutil.which("cargo")
    if (workspace / "Cargo.toml").is_file() and cargo:
        return [cargo, "check", "--quiet"]
    rustc = shutil.which("rustc")
    if not rustc:
        return []
    root = next(
        (rel for rel in ("src/main.rs", "main.rs", "src/lib.rs", "lib.rs") if rel in rust_files),
        rust_files[0] if rust_files else "",
    )
    return [rustc, "--edition=2021", "--emit=metadata", root] if root else []


def _run_language_build_gate(
    workspace: Path, code_files: list[str], *, timeout_s: int
) -> tuple[bool, str, list[dict[str, Any]]]:
    # Exclude build artifacts (CMake, node_modules, target, build/) from
    # language detection to prevent misclassification (e.g., CMake's
    # compiler_depend.ts making a C++ project look like TypeScript).
    _build_dir_prefixes = ("build/", "cmake-build/", "target/", "node_modules/", "dist/", "out/")
    _source_files = [
        rel
        for rel in code_files
        if not any(rel.startswith(prefix) or f"/{prefix}" in rel for prefix in _build_dir_prefixes)
    ]
    ts_files = [rel for rel in _files_with_suffix(_source_files, (".ts", ".tsx")) if not rel.endswith(".d.ts")]
    if ts_files:
        tsc = shutil.which("tsc")
        if not tsc:
            return False, "tsc unavailable for TypeScript project", []
        cmd = _run_command(
            [
                tsc,
                "--noEmit",
                "--target",
                "ES2020",
                "--module",
                "ESNext",
                "--jsx",
                "react-jsx",
                *ts_files[:80],
            ],
            workspace,
            timeout_s=max(10, int(timeout_s)),
        )
        cmd["phase"] = "build_test_lint"
        return bool(cmd.get("ok")), "tsc --noEmit passed" if cmd.get("ok") else "tsc --noEmit failed", [cmd]

    go_files = _files_with_suffix(_source_files, (".go",))
    if go_files:
        command = _go_command(workspace, go_files)
        if not command:
            return False, "go unavailable for Go project", []
        cmd = _run_command(command, workspace, timeout_s=max(10, int(timeout_s)))
        cmd["phase"] = "build_test_lint"
        command_label = "go vet" if len(command) > 1 and command[1] == "vet" else "go test"
        return bool(cmd.get("ok")), f"{command_label} passed" if cmd.get("ok") else f"{command_label} failed", [cmd]

    rust_files = _files_with_suffix(_source_files, (".rs",))
    if rust_files:
        command = _rust_compile_command(workspace, rust_files)
        if not command:
            return False, "rustc/cargo unavailable for Rust project", []
        cmd = _run_command(command, workspace, timeout_s=max(10, int(timeout_s)))
        cmd["phase"] = "build_test_lint"
        return bool(cmd.get("ok")), "Rust compile check passed" if cmd.get("ok") else "Rust compile check failed", [cmd]

    cpp_files = _files_with_suffix(_source_files, _CPP_SOURCE_SUFFIXES)
    if cpp_files:
        compiler = _which_any("g++", "c++")
        if not compiler:
            return False, "g++/c++ unavailable for C++ project", []
        commands: list[dict[str, Any]] = []
        failures: list[str] = []
        for rel in cpp_files[:20]:
            cmd = _run_command(
                [compiler, "-std=c++17", "-fsyntax-only", rel],
                workspace,
                timeout_s=max(10, int(timeout_s)),
            )
            cmd["phase"] = "build_test_lint"
            commands.append(cmd)
            if not cmd.get("ok"):
                failures.append(rel)
        ok = not failures
        return ok, "C++ syntax check passed" if ok else f"C++ syntax check failed: {', '.join(failures[:3])}", commands

    java_files = _files_with_suffix(code_files, (".java",))
    if java_files:
        javac = shutil.which("javac")
        if not javac:
            return False, "javac unavailable for Java project", []
        with tempfile.TemporaryDirectory(prefix="polaris-factory-javac-") as out_dir:
            cmd = _run_command(
                [javac, "-encoding", "UTF-8", "-d", out_dir, *java_files[:120]],
                workspace,
                timeout_s=max(10, int(timeout_s)),
            )
        cmd["phase"] = "build_test_lint"
        return bool(cmd.get("ok")), "javac compile passed" if cmd.get("ok") else "javac compile failed", [cmd]

    return False, "no language build command was discovered", []


def _run_platform_verifiers(workspace: Path, *, timeout_s: int) -> dict[str, Any]:
    policy = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=str(workspace))).policy
    result = run_verifier_policy(
        RunVerifierPolicyCommandV1(
            workspace=str(workspace),
            policy=policy,
            timeout_seconds=timeout_s,
        )
    )
    return result.gate_patch


def _required_user_verifier_requirement(verifier_patch: dict[str, Any]) -> dict[str, Any] | None:
    raw_verifiers = verifier_patch.get("user_verifiers")
    if not isinstance(raw_verifiers, list):
        return None
    verifiers = [item for item in raw_verifiers if isinstance(item, dict)]
    required = [item for item in verifiers if bool(item.get("required"))]
    if not required:
        return None
    failed = [item for item in required if not bool(item.get("ok") or item.get("passed"))]
    if failed:
        names = [
            str(item.get("name") or item.get("id") or item.get("script") or "custom verifier") for item in failed[:5]
        ]
        return {
            "ok": False,
            "detail": "required user verifier failed: " + ", ".join(names),
        }
    return {
        "ok": True,
        "detail": f"{len(required)} required user verifier(s) passed",
    }


def _smoke_go_cli(workspace: Path, code_files: list[str], *, timeout_s: int) -> dict[str, Any]:
    go_files = _files_with_suffix(code_files, (".go",))
    go = _resolve_go_binary()
    if not go or not go_files:
        return {"ok": False, "kind": "go_cli", "detail": "go CLI entrypoint unavailable"}
    if (workspace / "go.mod").is_file():
        command = [go, "run", ".", "--help"]
        entrypoint = "go run ."
    elif "main.go" in go_files:
        command = [go, "run", "main.go", "--help"]
        entrypoint = "main.go"
    else:
        return {"ok": False, "kind": "go_cli", "detail": "no main.go or go.mod entrypoint discovered"}
    return _cli_smoke_result(
        "go_cli", entrypoint, _run_command(command, workspace, timeout_s=min(max(3, int(timeout_s)), 10))
    )


def _smoke_rust_cli(workspace: Path, code_files: list[str], *, timeout_s: int) -> dict[str, Any]:
    rust_files = _files_with_suffix(code_files, (".rs",))
    if not rust_files:
        return {"ok": False, "kind": "rust_cli", "detail": "no Rust entrypoint discovered"}
    cargo = shutil.which("cargo")
    if (workspace / "Cargo.toml").is_file() and cargo:
        return _cli_smoke_result(
            "rust_cli",
            "cargo run",
            _run_command(
                [cargo, "run", "--quiet", "--", "--help"], workspace, timeout_s=min(max(3, int(timeout_s)), 10)
            ),
        )
    rustc = shutil.which("rustc")
    main_rel = next((rel for rel in ("src/main.rs", "main.rs") if rel in rust_files), "")
    if not rustc or not main_rel:
        return {"ok": False, "kind": "rust_cli", "detail": "rustc or main.rs entrypoint unavailable"}
    with tempfile.TemporaryDirectory(prefix="polaris-factory-rust-") as out_dir:
        binary = str(Path(out_dir) / "app")
        compile_result = _run_command(
            [rustc, "--edition=2021", main_rel, "-o", binary], workspace, timeout_s=max(10, int(timeout_s))
        )
        if not compile_result.get("ok"):
            return {"kind": "rust_cli", "entrypoint": main_rel, "compile": compile_result, **compile_result}
        result = _run_command([binary, "--help"], workspace, timeout_s=min(max(3, int(timeout_s)), 10))
    payload = _cli_smoke_result("rust_cli", main_rel, result)
    payload["compile"] = compile_result
    return payload


def _smoke_cpp_cli(workspace: Path, code_files: list[str], *, timeout_s: int) -> dict[str, Any]:
    compiler = _which_any("g++", "c++")
    if not compiler:
        return {"ok": False, "kind": "cpp_cli", "detail": "g++/c++ unavailable for C++ entrypoint"}
    main_rel = ""
    for rel in _files_with_suffix(code_files, _CPP_SOURCE_SUFFIXES):
        try:
            text = (workspace / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "int main" in text:
            main_rel = rel
            break
    if not main_rel:
        return {"ok": False, "kind": "cpp_cli", "detail": "no C++ int main entrypoint discovered"}
    compile_sources: list[str] = []
    for rel in _files_with_suffix(code_files, _CPP_SOURCE_SUFFIXES):
        normalized = rel.replace("\\", "/")
        if normalized.startswith("tests/") or "/tests/" in normalized:
            continue
        if rel not in compile_sources:
            compile_sources.append(rel)
    if main_rel in compile_sources:
        compile_sources.remove(main_rel)
    compile_sources.insert(0, main_rel)
    # Conventional C++ include roots (workspace root + src/ + include/) as -I so
    # headers included as <models/foo.hpp> (CMake target_include_directories)
    # resolve without reading CMakeLists.txt (factory_bench L1-06).
    include_flags: list[str] = []
    for inc in (".", "src", "include"):
        if (workspace / inc).is_dir():
            include_flags += ["-I", str(workspace / inc)]
    with tempfile.TemporaryDirectory(prefix="polaris-factory-cpp-") as out_dir:
        binary = str(Path(out_dir) / "app")
        compile_result = _run_command(
            [compiler, "-std=c++17", *include_flags, *compile_sources, "-o", binary],
            workspace,
            timeout_s=max(10, int(timeout_s)),
        )
        if not compile_result.get("ok"):
            return {"kind": "cpp_cli", "entrypoint": main_rel, "compile": compile_result, **compile_result}
        result = _run_command([binary, "--help"], workspace, timeout_s=min(max(3, int(timeout_s)), 10))
    payload = _cli_smoke_result("cpp_cli", main_rel, result)
    payload["compile"] = compile_result
    return payload


def _java_main_class_name(workspace: Path, main_rel: str) -> str:
    main_path = workspace / main_rel
    stem = Path(main_rel).stem
    try:
        text = main_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return stem
    package_match = re.search(
        r"(?m)^\s*package\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*;",
        text,
    )
    if not package_match:
        return stem
    return f"{package_match.group(1)}.{stem}"


def _smoke_java_cli(workspace: Path, code_files: list[str], *, timeout_s: int) -> dict[str, Any]:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        return {"ok": False, "kind": "java_cli", "detail": "javac/java unavailable for Java entrypoint"}
    java_files = _files_with_suffix(code_files, (".java",))
    if not java_files:
        return {"ok": False, "kind": "java_cli", "detail": "no Java entrypoint discovered"}
    main_rel = next((rel for rel in java_files if Path(rel).name == "Main.java"), java_files[0])
    main_class = _java_main_class_name(workspace, main_rel)
    with tempfile.TemporaryDirectory(prefix="polaris-factory-java-") as out_dir:
        compile_result = _run_command(
            [javac, "-encoding", "UTF-8", "-d", out_dir, *java_files[:120]],
            workspace,
            timeout_s=max(10, int(timeout_s)),
        )
        if not compile_result.get("ok"):
            return {"kind": "java_cli", "entrypoint": main_rel, "compile": compile_result, **compile_result}
        result = _run_command(
            [java, "-cp", out_dir, main_class, "--help"], workspace, timeout_s=min(max(3, int(timeout_s)), 10)
        )
    payload = _cli_smoke_result("java_cli", main_rel, result)
    payload["compile"] = compile_result
    return payload


def _looks_like_python_test(rel_path: str) -> bool:
    path = Path(rel_path)
    name = path.name
    return name.startswith("test_") and name.endswith(".py")


def _primary_source_language(code_files: list[str]) -> str:
    """Determine the primary source language of a generated project.

    Returns one of ``"go"``, ``"rust"``, ``"python"``, ``"javascript"``,
    ``"html"``, ``"java"``, ``"cpp"``, or ``""`` (unknown).

    The decision is based on non-test source files only: a ``tests/test_*.py``
    inside a Go project must NOT make the project "Python-primary".
    """
    go_count = len([f for f in code_files if f.endswith(".go")])
    rust_count = len([f for f in code_files if f.endswith(".rs")])
    py_non_test = len([f for f in code_files if f.endswith(".py") and "/test_" not in f and not f.startswith("test_")])
    js_count = len([f for f in code_files if f.endswith((".js", ".mjs", ".cjs", ".ts", ".tsx"))])
    html_count = len([f for f in code_files if f.endswith((".html", ".css"))])
    java_count = len([f for f in code_files if f.endswith(".java")])
    cpp_count = len([f for f in code_files if f.endswith((".cpp", ".cc", ".cxx", ".hpp", ".h", ".c"))])

    # If Go files exist and Python files are only tests, Go is primary.
    if go_count > 0 and py_non_test == 0:
        return "go"
    if rust_count > 0 and py_non_test == 0:
        return "rust"
    if java_count > 0 and py_non_test == 0:
        return "java"
    if cpp_count > 0 and py_non_test == 0:
        return "cpp"
    # Standard priority: most non-test source files wins.
    counts = {
        "go": go_count,
        "rust": rust_count,
        "python": py_non_test,
        "javascript": js_count,
        "html": html_count,
        "java": java_count,
        "cpp": cpp_count,
    }
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] > 0 else ""


def _discover_python_test_files(workspace: Path, code_files: list[str]) -> list[str]:
    discovered: set[str] = set()

    def add_path(path: Path) -> None:
        try:
            rel_path = path.relative_to(workspace)
        except ValueError:
            return
        rel = rel_path.as_posix()
        if path.is_file() and _looks_like_python_test(rel):
            discovered.add(rel)

    for rel in code_files:
        add_path(workspace / rel)

    for path in workspace.glob("test_*.py"):
        add_path(path)

    tests_dir = workspace / "tests"
    if tests_dir.is_dir():
        for path in tests_dir.rglob("test_*.py"):
            add_path(path)

    return sorted(discovered)


def _python_test_command_has_zero_tests(result: dict[str, Any]) -> bool:
    output = f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}"
    return bool(re.search(r"Ran\s+0\s+tests", output))


def _python_pytest_command_has_zero_tests(result: dict[str, Any]) -> bool:
    output = f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}".lower()
    return "no tests ran" in output or "collected 0 items" in output


def _run_python_unittest_suite(workspace: Path, test_files: list[str], *, timeout_s: int) -> dict[str, Any]:
    start_dir = "tests" if (workspace / "tests").is_dir() else "."
    result = _run_command(
        [sys.executable, "-m", "unittest", "discover", "-s", start_dir, "-p", "test_*.py", "-v"],
        workspace,
        timeout_s=max(10, int(timeout_s)),
    )
    result["kind"] = "python_tests"
    result["runner"] = "unittest"
    result["test_files"] = test_files
    if _python_test_command_has_zero_tests(result):
        result["ok"] = False
        result["detail"] = "python unittest discovered zero tests from generated test files"
    return result


def _run_python_pytest_suite(workspace: Path, test_files: list[str], *, timeout_s: int) -> dict[str, Any]:
    result = _run_command(
        [sys.executable, "-m", "pytest", *test_files, "-q"],
        workspace,
        timeout_s=max(10, int(timeout_s)),
    )
    result["kind"] = "python_tests"
    result["runner"] = "pytest"
    result["test_files"] = test_files
    if _python_pytest_command_has_zero_tests(result):
        result["ok"] = False
        result["detail"] = "python pytest discovered zero tests from generated test files"
    return result


def _run_python_test_suite(workspace: Path, test_files: list[str], *, timeout_s: int) -> list[dict[str, Any]]:
    unittest_result = _run_python_unittest_suite(workspace, test_files, timeout_s=timeout_s)
    if not _python_test_command_has_zero_tests(unittest_result):
        return [unittest_result]
    pytest_result = _run_python_pytest_suite(workspace, test_files, timeout_s=timeout_s)
    pytest_result["fallback_from"] = "unittest_zero_tests"
    return [unittest_result, pytest_result]


def _smoke_python_cli(workspace: Path, entrypoint: str, *, timeout_s: int) -> dict[str, Any]:
    # Set PYTHONPATH to workspace root so that `from src.xxx import yyy` resolves.
    py_env = {"PYTHONPATH": str(workspace)}
    command = [sys.executable, entrypoint, "--help"]
    result = _run_command(command, workspace, timeout_s=min(max(2, int(timeout_s)), 10), extra_env=py_env)
    if result["ok"]:
        if _entrypoint_has_failure_marker(result):
            return _mark_entrypoint_failure({"kind": "python_cli", "entrypoint": entrypoint, **result})
        return {"kind": "python_cli", "entrypoint": entrypoint, **result}
    fallback = _run_command(
        [sys.executable, entrypoint], workspace, timeout_s=min(max(2, int(timeout_s)), 5), extra_env=py_env
    )
    fallback_output = f"{fallback.get('stdout_tail') or ''}\n{fallback.get('stderr_tail') or ''}".lower()
    if (
        fallback.get("returncode") in {1, 2}
        and "usage:" in fallback_output
        and "traceback" not in fallback_output
        and "syntaxerror" not in fallback_output
    ):
        return {
            "kind": "python_cli",
            "entrypoint": entrypoint,
            "usage_screen": True,
            **fallback,
            "ok": True,
        }
    if fallback["ok"] or fallback.get("timeout"):
        if _entrypoint_has_failure_marker(fallback):
            return _mark_entrypoint_failure(
                {
                    "kind": "python_cli",
                    "entrypoint": entrypoint,
                    "started": bool(fallback.get("timeout")),
                    **fallback,
                }
            )
        # Timeout is NOT success - mark as failure
        if fallback.get("timeout"):
            return {
                "kind": "python_cli",
                "entrypoint": entrypoint,
                "started": True,
                "timeout": True,
                "ok": False,
                "detail": "CLI timed out - not considered successful",
            }
        return {
            "kind": "python_cli",
            "entrypoint": entrypoint,
            "started": bool(fallback.get("timeout")),
            **fallback,
            "ok": True,
        }
    return {"kind": "python_cli", "entrypoint": entrypoint, **fallback}


def _first_ok_command(commands: list[dict[str, Any]]) -> dict[str, Any] | None:
    for command in commands:
        if command.get("ok"):
            return command
    return None


_BUILD_OUTPUT_DIR_NAMES = frozenset({"dist", "build", "out", "bin"})

_SOURCE_FILE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".cxx",
        ".rs",
        ".go",
        ".java",
    }
)
_SCAFFOLD_FILE_EXTENSIONS = frozenset({".json", ".html", ".css", ".sh", ".sql"})


def _is_build_output_path(path: str) -> bool:
    """Check if a path starts with a build output directory as its first segment."""
    normalized = path.replace("\\", "/")
    clean = normalized
    while clean.startswith("./"):
        clean = clean[2:]
    parts = clean.split("/")
    if not parts:
        return False
    first_segment = parts[0].lower()
    return first_segment in _BUILD_OUTPUT_DIR_NAMES


def _token_references_build_output(token: str) -> bool:
    """Check if a single command token references a build output directory."""
    if "=" in token:
        _, _, value = token.partition("=")
        value = value.strip("'\"")
        if _is_build_output_path(value):
            return True
    return _is_build_output_path(token)


def _has_build_output_path_reference(command: str) -> bool:
    """Check if command contains a build output dir used as a path root."""
    normalized = command.replace("\\", "/")
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()
    return any(_token_references_build_output(t) for t in tokens)


def _command_serves_build_output(command: str) -> bool:
    """Check if the command is known to serve build output (e.g. vite preview, serve -s dist)."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False
    idx = 0
    if tokens[0] == "npx" and len(tokens) >= 2:
        idx = 1
    if len(tokens) > idx + 1 and tokens[idx] == "vite" and tokens[idx + 1] == "preview":
        return True
    if tokens[idx] in ("serve", "http-server"):
        remaining = tokens[idx + 1 :]
        return any(_token_references_build_output(t) for t in remaining)
    return False


def _script_depends_on_build_output(scripts: dict[str, Any], script_name: str) -> bool:
    """Check if an npm script's command references build artifact directories.

    Detects build output dirs as path roots (dist, ./dist, dist/index.js),
    flag values (--dir=dist), and known build-serving commands (serve, vite preview).
    Avoids false positives for source paths like scripts/build/start.js.
    """
    command = str(scripts.get(script_name) or "").strip()
    if not command:
        return False
    if _command_serves_build_output(command):
        return True
    return _has_build_output_path_reference(command)


def _any_script_references_build_output(scripts: dict[str, Any], script_names: tuple[str, ...]) -> bool:
    """Check if any of the given npm scripts reference build artifact directories."""
    return any(_script_depends_on_build_output(scripts, name) for name in script_names if name in scripts)


def _build_declared_source_targets_requirement(record: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Build the declared_source_targets_present requirement.

    Checks if PM plan declared source targets and whether they all exist.
    """
    missing_targets = record.get("missing_declared_source_targets") or []
    declared_count = record.get("declared_source_target_count", 0)
    missing_count = record.get("missing_declared_source_target_count", 0)

    # If no declared targets, this is a risk signal but not a hard failure
    if declared_count == 0:
        pm_plan_missing = record.get("pm_plan_missing_source_targets", False)
        if pm_plan_missing:
            return {
                "ok": False,
                "detail": "PM plan has no declared source targets (pm_plan_missing_source_targets)",
            }
        return {
            "ok": True,
            "detail": "no declared source targets in PM plan",
        }

    # If declared targets exist but some are missing, fail
    if missing_count > 0:
        return {
            "ok": False,
            "detail": f"{missing_count} declared source target(s) missing: {', '.join(missing_targets[:5])}",
        }

    return {
        "ok": True,
        "detail": f"all {declared_count} declared source target(s) present",
    }


def _build_scaffolding_requirement(workspace: Path, code_files: list[str]) -> dict[str, Any]:
    """Check that TypeScript/Web projects have required scaffolding files.

    TypeScript projects must have package.json and tsconfig.json.
    Node.js/JS-only projects (no HTML) must have package.json.
    Static web projects (HTML + JS) don't require package.json.
    """
    has_ts = _files_with_suffix(code_files, (".ts", ".tsx"))
    has_js = _files_with_suffix(code_files, (".js", ".jsx", ".mjs", ".cjs"))
    has_html = [rel for rel in code_files if rel.lower().endswith(".html")]
    # TypeScript always needs npm scaffolding; JS-only (no HTML) needs it too;
    # JS + HTML is a static web project that doesn't need package.json.
    needs_package = bool(has_ts) or (bool(has_js) and not bool(has_html))
    missing: list[str] = []

    if needs_package:
        package_json = workspace / "package.json"
        if not package_json.is_file():
            missing.append("package.json")

    if has_ts:
        tsconfig_json = workspace / "tsconfig.json"
        if not tsconfig_json.is_file():
            missing.append("tsconfig.json")

    if has_html:
        html_entry = _find_html_entrypoint(workspace, code_files)
        if not html_entry:
            missing.append("index.html")

    if missing:
        return {
            "ok": False,
            "detail": f"missing required scaffolding: {', '.join(missing)}",
        }
    parts: list[str] = []
    if needs_package:
        parts.append("package.json present")
    if has_ts:
        parts.append("tsconfig.json present")
    if has_html:
        parts.append("HTML entrypoint present")
    if not parts:
        parts.append("no scaffolding required for this project type")
    return {
        "ok": True,
        "detail": "; ".join(parts),
    }


def build_real_run_gate(workspace: Path, record: dict[str, Any], *, timeout_s: int = 60) -> dict[str, Any]:
    """Run the platform's real-runnability gate for one generated project."""
    code_files = [str(item) for item in record.get("code_files") or []]
    source_files = [rel for rel in code_files if Path(rel).suffix.lower() in _SOURCE_FILE_EXTENSIONS]
    html_css_only = code_files and all(Path(rel).suffix.lower() in {".html", ".css"} for rel in code_files)
    scaffold_only = code_files and not source_files and not html_css_only
    source_files_ok = bool(source_files) or bool(html_css_only)
    commands: list[dict[str, Any]] = []
    package = _load_package_json(workspace)
    scripts = _as_dict(package.get("scripts"))

    environment_ok = False
    environment_detail = "no environment preparation ran"
    if package:
        npm = shutil.which("npm")
        if (
            npm
            and _package_requires_project_typescript(workspace, package, scripts, code_files)
            and not _package_declares_dependency(package, "typescript")
            and not _package_has_local_tsc(workspace)
        ):
            environment_detail = "package.json missing devDependency 'typescript' for TypeScript build"
        elif npm and _has_package_dependencies(package) and not (workspace / "node_modules").exists():
            install = _run_command(
                [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                workspace,
                timeout_s=max(30, int(timeout_s)),
            )
            install["phase"] = "environment"
            commands.append(install)
            environment_ok = bool(install.get("ok"))
            environment_detail = "npm dependencies installed" if environment_ok else "npm install failed"
        elif npm:
            environment_ok = True
            environment_detail = "npm available; no dependency install required"
        else:
            environment_detail = "npm unavailable for package.json project"
    elif any(rel.endswith(".py") for rel in code_files):
        environment_ok = True
        environment_detail = f"python executable available: {sys.executable}"
    elif any(rel.endswith(".html") for rel in code_files):
        environment_ok = True
        environment_detail = "static web project has no dependency manifest"
    elif _files_with_suffix(code_files, (".go",)):
        environment_ok = bool(_resolve_go_binary())
        environment_detail = "go toolchain available" if environment_ok else "go toolchain unavailable"
    elif _files_with_suffix(code_files, (".rs",)):
        environment_ok = bool(_which_any("cargo", "rustc"))
        environment_detail = "rust toolchain available" if environment_ok else "rust toolchain unavailable"
    elif _files_with_suffix(code_files, _CPP_SOURCE_SUFFIXES):
        environment_ok = bool(_which_any("g++", "c++"))
        environment_detail = "C++ compiler available" if environment_ok else "g++/c++ unavailable"
    elif _files_with_suffix(code_files, (".java",)):
        environment_ok = bool(shutil.which("javac") and shutil.which("java"))
        environment_detail = "Java toolchain available" if environment_ok else "javac/java unavailable"
    elif _files_with_suffix(code_files, (".ts", ".tsx")):
        environment_ok = bool(shutil.which("tsc"))
        environment_detail = "TypeScript compiler available" if environment_ok else "tsc unavailable"

    build_command_ok = False
    build_detail = "no build/test/lint command was discovered"
    package_script_failed = False
    if package and shutil.which("npm") and environment_ok:
        has_build_script = "build" in scripts
        has_ts_files = _files_with_suffix(code_files, (".ts", ".tsx"))

        build_cmd_str = str(scripts.get("build") or "")
        has_build_output_ref = _any_script_references_build_output(scripts, ("test", "start", "check", "lint"))
        should_build_first = has_build_script and (has_ts_files or has_build_output_ref or "tsc" in build_cmd_str)
        if should_build_first:
            cmd = _run_command(["npm", "run", "build"], workspace, timeout_s=max(10, int(timeout_s)))
            cmd["phase"] = "build_test_lint"
            cmd["script"] = "build"
            commands.append(cmd)
            build_command_ok = bool(cmd.get("ok"))
            package_script_failed = not build_command_ok
            if build_command_ok:
                build_detail = "npm run build passed"
                ran_quality = False
                for script_name in ("test", "lint", "check"):
                    if script_name in scripts:
                        cmd = _run_command(["npm", "run", script_name], workspace, timeout_s=max(10, int(timeout_s)))
                        cmd["phase"] = "build_test_lint"
                        cmd["script"] = script_name
                        commands.append(cmd)
                        ran_quality = True
                        if not cmd.get("ok"):
                            build_command_ok = False
                            package_script_failed = True
                            build_detail = f"npm run {script_name} failed"
                            break
                        build_detail = f"npm run build and npm run {script_name} passed"
                        break
                if not ran_quality and build_command_ok:
                    build_detail = "npm run build passed"
            else:
                stderr = str(cmd.get("stderr_tail") or "")
                build_detail = "npm run build failed" + (f": {stderr}" if stderr else "")
        else:
            for script_name in ("test", "build", "lint", "check"):
                if script_name in scripts:
                    cmd = _run_command(["npm", "run", script_name], workspace, timeout_s=max(10, int(timeout_s)))
                    cmd["phase"] = "build_test_lint"
                    cmd["script"] = script_name
                    commands.append(cmd)
                    build_command_ok = bool(cmd.get("ok"))
                    package_script_failed = not build_command_ok
                    build_detail = f"npm run {script_name} {'passed' if build_command_ok else 'failed'}"
                    break
    python_test_files = _discover_python_test_files(workspace, code_files)
    primary_lang = _primary_source_language(code_files)
    # Skip the Python compileall/test path when the project is primarily a
    # compiled-language project (Go, Rust, …) that happens to include a
    # Python contract-verification test.  Running ``python -m unittest`` on a
    # Go project's contract test would fail on symbol mismatches and mask the
    # real Go build gate result.
    _skip_python_for_non_python_project = primary_lang in ("go", "rust", "java", "cpp")
    if (
        not build_command_ok
        and not package_script_failed
        and not _skip_python_for_non_python_project
        and any(rel.endswith(".py") for rel in code_files)
    ):
        cmd = _run_command(
            [sys.executable, "-m", "compileall", "-q", "."], workspace, timeout_s=max(10, int(timeout_s))
        )
        cmd["phase"] = "build_test_lint"
        commands.append(cmd)
        build_command_ok = bool(cmd.get("ok"))
        build_detail = "python compileall passed" if build_command_ok else "python compileall failed"
        if build_command_ok and python_test_files:
            test_commands = _run_python_test_suite(workspace, python_test_files, timeout_s=timeout_s)
            for test_cmd in test_commands:
                test_cmd["phase"] = "build_test_lint"
                commands.append(test_cmd)
            test_cmd = test_commands[-1]
            build_command_ok = bool(test_cmd.get("ok"))
            if build_command_ok:
                runner = str(test_cmd.get("runner") or "tests")
                build_detail = f"python compileall and {runner} passed ({len(python_test_files)} test file(s))"
            else:
                runner = str(test_cmd.get("runner") or "python tests")
                build_detail = str(test_cmd.get("detail") or f"python {runner} failed")
    if (
        not build_command_ok
        and not package_script_failed
        and any(rel.endswith((".js", ".mjs", ".cjs")) for rel in code_files)
        and shutil.which("node")
    ):
        js_files = [rel for rel in code_files if rel.endswith((".js", ".mjs", ".cjs")) and not rel.endswith(".min.js")]
        failures: list[str] = []
        for rel in js_files[:20]:
            cmd = _run_command(["node", "--check", rel], workspace, timeout_s=max(5, min(30, int(timeout_s))))
            cmd["phase"] = "build_test_lint"
            commands.append(cmd)
            if not cmd.get("ok"):
                failures.append(rel)
        build_command_ok = bool(js_files) and not failures
        build_detail = "node --check passed" if build_command_ok else f"node --check failed: {', '.join(failures[:3])}"
    if not build_command_ok and not package_script_failed:
        language_ok, language_detail, language_commands = _run_language_build_gate(
            workspace,
            code_files,
            timeout_s=timeout_s,
        )
        commands.extend(language_commands)
        if language_detail != "no language build command was discovered":
            build_command_ok = language_ok
            build_detail = language_detail

    entrypoint: dict[str, Any] = {"ok": False, "kind": "", "detail": "no CLI/Web/API entrypoint discovered"}
    html_entry = _find_html_entrypoint(workspace, code_files)
    if html_entry:
        entrypoint = _smoke_static_web(workspace, html_entry, timeout_s=timeout_s)
    elif package and shutil.which("npm") and "start" in scripts:
        start_needs_build = _script_depends_on_build_output(scripts, "start")
        build_was_attempted = any(cmd.get("script") == "build" for cmd in commands)
        if start_needs_build and (not build_was_attempted or not build_command_ok):
            fail_detail = "build did not succeed"
            if build_was_attempted and build_detail:
                fail_detail = build_detail
            entrypoint = {
                "kind": "npm_start",
                "entrypoint": "npm run start",
                "ok": False,
                "detail": f"npm start depends on build output but {fail_detail}",
            }
        else:
            cmd = _run_command(["npm", "run", "start"], workspace, timeout_s=min(max(3, int(timeout_s)), 8))
            # npm start timeout 不得直接算成功；server 项目需要端口/health probe 或明确启动成功证据
            has_success_evidence = bool(cmd.get("ok")) and not bool(cmd.get("timeout"))
            entrypoint = {
                "kind": "npm_start",
                "entrypoint": "npm run start",
                "ok": has_success_evidence,
                "detail": "npm run start completed successfully"
                if has_success_evidence
                else "npm run start timed out or failed",
                **cmd,
            }
    else:
        # Determine the primary source language so that a Go project with a
        # stray ``tests/test_*.py`` doesn't get the Python CLI smoke path.
        _ep_lang = primary_lang if primary_lang else _primary_source_language(code_files)
        if _ep_lang == "go" and _files_with_suffix(code_files, (".go",)):
            entrypoint = _smoke_go_cli(workspace, code_files, timeout_s=timeout_s)
        elif _ep_lang == "rust" and _files_with_suffix(code_files, (".rs",)):
            entrypoint = _smoke_rust_cli(workspace, code_files, timeout_s=timeout_s)
        elif _ep_lang == "java" and _files_with_suffix(code_files, (".java",)):
            entrypoint = _smoke_java_cli(workspace, code_files, timeout_s=timeout_s)
        elif _ep_lang == "cpp" and _files_with_suffix(code_files, _CPP_SOURCE_SUFFIXES):
            entrypoint = _smoke_cpp_cli(workspace, code_files, timeout_s=timeout_s)
        else:
            py_entry = _find_python_entrypoint(workspace, code_files)
            if py_entry:
                entrypoint = _smoke_python_cli(workspace, py_entry, timeout_s=timeout_s)
            elif _files_with_suffix(code_files, (".go",)):
                entrypoint = _smoke_go_cli(workspace, code_files, timeout_s=timeout_s)
            elif _files_with_suffix(code_files, (".rs",)):
                entrypoint = _smoke_rust_cli(workspace, code_files, timeout_s=timeout_s)
            elif _files_with_suffix(code_files, _CPP_SOURCE_SUFFIXES):
                entrypoint = _smoke_cpp_cli(workspace, code_files, timeout_s=timeout_s)
            elif _files_with_suffix(code_files, (".java",)):
                entrypoint = _smoke_java_cli(workspace, code_files, timeout_s=timeout_s)

    if (
        not build_command_ok
        and bool(entrypoint.get("ok"))
        and str(entrypoint.get("kind") or "") in ("web_static", "web_playwright")
        and not package
        and code_files
        and all(rel.endswith((".html", ".css")) for rel in code_files)
    ):
        build_command_ok = True
        build_detail = "static HTML/CSS entrypoint smoke passed"

    requirements = {
        "artifact_landed": {
            "ok": bool(code_files),
            "detail": f"{len(code_files)} generated code file(s)",
        },
        "source_files_present": {
            "ok": source_files_ok,
            "detail": (
                f"{len(source_files)} source file(s)"
                if source_files
                else (
                    "pure HTML/CSS web project (no business-logic source files)"
                    if html_css_only
                    else f"scaffold-only delivery: {len(code_files)} code file(s) but zero source files "
                    "(only config/metadata like package.json, tsconfig.json)"
                )
            ),
        },
        "declared_source_targets_present": _build_declared_source_targets_requirement(record, workspace),
        "scaffolding_present": _build_scaffolding_requirement(workspace, code_files),
        "environment_prepared": {"ok": environment_ok, "detail": environment_detail},
        "build_test_lint_ran": {"ok": build_command_ok, "detail": build_detail},
        "entrypoint_smoke": {
            "ok": bool(entrypoint.get("ok")),
            "detail": str(entrypoint.get("detail") or entrypoint.get("stderr_tail") or entrypoint.get("kind") or ""),
            "kind": str(entrypoint.get("kind") or ""),
        },
    }
    ok = all(bool(item.get("ok")) for item in requirements.values())
    failing = [name for name, item in requirements.items() if not item.get("ok")]
    result: dict[str, Any] = {
        "ok": ok,
        "requirements": requirements,
        "commands": commands[-12:],
        "command_count_total": len(commands),
        "commands_truncated": len(commands) > 12,
        "entrypoint": entrypoint,
        "summary": "real run gate passed" if ok else "real run gate failed: " + ", ".join(failing),
    }
    verifier_patch = _run_platform_verifiers(workspace, timeout_s=timeout_s)
    if verifier_patch:
        verifier_requirement = _required_user_verifier_requirement(verifier_patch)
        if verifier_requirement is not None:
            requirements["user_verifiers"] = verifier_requirement
            if not bool(verifier_requirement.get("ok")):
                result["ok"] = False
                result["summary"] = "real run gate failed: user_verifiers"
        result.update(verifier_patch)
    if scaffold_only:
        result["missing_source_targets"] = {
            "code_file_count": len(code_files),
            "source_file_count": 0,
            "scaffold_files": code_files[:10],
            "detail": "Director produced only scaffold files (package.json, tsconfig.json, etc.) "
            "with zero source code files",
        }
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _nested_dict(value: Any, *keys: str) -> dict[str, Any]:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _first_string(*values: Any) -> str:
    for value in values:
        token = _norm_text(value)
        if token:
            return token
    return ""


def _normalize_llm_event(raw: dict[str, Any], *, source_path: str = "") -> dict[str, Any] | None:
    data = _as_dict(raw.get("data"))
    payload = _as_dict(raw.get("payload"))
    meta = _as_dict(raw.get("meta"))
    metadata = _as_dict(raw.get("metadata"))
    data_metadata = _as_dict(data.get("metadata"))
    extra_fields = _as_dict(data_metadata.get("extra_fields"))
    tokens = _as_dict(raw.get("tokens"))
    audit_refs = _as_dict(raw.get("audit_refs"))
    final_request_evidence = _as_dict(raw.get("final_request_evidence"))
    if not final_request_evidence:
        final_request_evidence = _as_dict(data.get("final_request_evidence"))
    if not final_request_evidence:
        final_request_evidence = _as_dict(data_metadata.get("final_request_evidence"))
    final_request_evidence_authority = _as_dict(final_request_evidence.get("final_request_evidence_authority"))
    final_request_context_audit = _as_dict(raw.get("final_request_context_audit"))
    if not final_request_context_audit:
        final_request_context_audit = _as_dict(data.get("final_request_context_audit"))
    if not final_request_context_audit:
        final_request_context_audit = _as_dict(data_metadata.get("final_request_context_audit"))
    context_snapshot_ref = normalize_context_snapshot_ref(
        _first_string(
            raw.get("context_snapshot_ref"),
            data.get("context_snapshot_ref"),
            metadata.get("context_snapshot_ref"),
            data_metadata.get("context_snapshot_ref"),
            extra_fields.get("context_snapshot_ref"),
            audit_refs.get("context_snapshot_ref"),
            final_request_evidence.get("context_snapshot_ref"),
        )
    )

    event_name = _first_string(
        raw.get("event_type"), raw.get("event"), raw.get("type"), raw.get("name"), data.get("event_type")
    )
    role = _norm_role(_first_string(raw.get("role"), data.get("role"), payload.get("role"), meta.get("role")))
    provider = _first_string(
        raw.get("provider_id"),
        raw.get("provider"),
        data.get("provider_id"),
        data.get("provider"),
        metadata.get("provider_id"),
        metadata.get("provider"),
        data_metadata.get("provider_id"),
        data_metadata.get("provider"),
        extra_fields.get("provider_id"),
        extra_fields.get("provider"),
    )
    model = _first_string(
        raw.get("model"),
        data.get("model"),
        metadata.get("model"),
        data_metadata.get("model"),
        extra_fields.get("model"),
    )
    binding_id = _first_string(
        raw.get("binding_id"), data.get("binding_id"), data_metadata.get("binding_id"), extra_fields.get("binding_id")
    )
    source = _first_string(
        raw.get("source"),
        data.get("source"),
        metadata.get("source"),
        data_metadata.get("source"),
        extra_fields.get("source"),
    )
    lowered_source = source.lower()
    if lowered_source == "llm":
        source = "llm"
    elif "llm" not in lowered_source:
        metadata_source = _norm_text(data_metadata.get("source"))
        if metadata_source.lower() == "llm" or "llm" in event_name.lower():
            source = "llm"
            lowered_source = "llm"
    cache_hit = bool(
        raw.get("cache_hit")
        or data.get("cache_hit")
        or metadata.get("cache_hit")
        or data_metadata.get("cache_hit")
        or extra_fields.get("cache_hit")
        or data_metadata.get("cached")
        or lowered_source == "cache"
    )
    prompt_tokens = data.get("prompt_tokens", tokens.get("prompt"))
    completion_tokens = data.get("completion_tokens", tokens.get("completion"))
    total_tokens = tokens.get("total")
    if total_tokens is None:
        try:
            total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        except (TypeError, ValueError):
            total_tokens = None

    marker = " ".join([event_name, str(raw.get("kind") or ""), str(raw.get("channel") or "")]).lower()
    if not role and "llm" not in marker and not event_name.startswith("invoke"):
        return None
    if not role:
        role = "unknown"
    lowered_event = event_name.lower()
    terminal = lowered_event in {
        "llm_call_end",
        "llm_error",
        "call_end",
        "call_error",
        "invoke_end",
        "error",
        "llm_route_terminal",
    }
    raw_invocation = raw.get("invocation")
    if isinstance(raw_invocation, bool):
        invocation = raw_invocation
    else:
        invocation = terminal or "llm" in lowered_event or lowered_event.startswith("invoke")
    skipped = bool(raw.get("skipped") or data.get("skipped") or metadata.get("skipped") or data_metadata.get("skipped"))
    fail_closed = bool(
        raw.get("fail_closed")
        or data.get("fail_closed")
        or metadata.get("fail_closed")
        or data_metadata.get("fail_closed")
    )
    skip_reason = _first_string(
        raw.get("skip_reason"),
        data.get("skip_reason"),
        metadata.get("skip_reason"),
        data_metadata.get("skip_reason"),
        extra_fields.get("skip_reason"),
    )
    return {
        "event": event_name,
        "role": role,
        "provider_id": provider,
        "model": model,
        "binding_id": binding_id,
        "source": source,
        "cache_hit": cache_hit,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "terminal": terminal,
        "invocation": invocation,
        "skipped": skipped,
        "skip_reason": skip_reason,
        "fail_closed": fail_closed,
        "context_snapshot_ref": context_snapshot_ref,
        "final_request_context_audit_present": bool(
            final_request_context_audit or final_request_evidence.get("final_request_context_audit_present")
        ),
        "final_request_context_audit_hash": _first_string(
            raw.get("final_request_context_audit_hash"),
            data.get("final_request_context_audit_hash"),
            audit_refs.get("final_request_context_audit_hash"),
            final_request_evidence.get("final_request_context_audit_hash"),
        ),
        "final_request_evidence_hash": _first_string(
            raw.get("final_request_evidence_hash"),
            data.get("final_request_evidence_hash"),
            audit_refs.get("final_request_evidence_hash"),
            final_request_evidence.get("final_request_evidence_hash"),
        ),
        "final_request_evidence_authority_hash": _first_string(
            raw.get("final_request_evidence_authority_hash"),
            data.get("final_request_evidence_authority_hash"),
            audit_refs.get("final_request_evidence_authority_hash"),
            final_request_evidence.get("final_request_evidence_authority_hash"),
            final_request_evidence_authority.get("final_request_evidence_authority_hash"),
        ),
        "final_request_evidence_coverage_pass": final_request_evidence.get("final_request_evidence_coverage_pass"),
        "role_id": _first_string(
            final_request_evidence.get("role_id"), final_request_evidence_authority.get("role_id")
        ),
        "expected_role_id": _first_string(
            final_request_evidence.get("expected_role_id"),
            final_request_evidence_authority.get("expected_role_id"),
        ),
        "role_identity_ok": final_request_evidence.get(
            "role_identity_ok", final_request_evidence_authority.get("role_identity_ok")
        ),
        "required_refs": final_request_evidence.get("required_refs")
        if isinstance(final_request_evidence.get("required_refs"), list)
        else final_request_evidence_authority.get("required_refs")
        if isinstance(final_request_evidence_authority.get("required_refs"), list)
        else [],
        "included_refs": final_request_evidence.get("included_refs")
        if isinstance(final_request_evidence.get("included_refs"), list)
        else final_request_evidence_authority.get("included_refs")
        if isinstance(final_request_evidence_authority.get("included_refs"), list)
        else [],
        "missing_required_refs": final_request_evidence.get("missing_required_refs")
        if isinstance(final_request_evidence.get("missing_required_refs"), list)
        else [],
        "required_tools": final_request_evidence.get("required_tools")
        if isinstance(final_request_evidence.get("required_tools"), list)
        else final_request_evidence_authority.get("required_tools")
        if isinstance(final_request_evidence_authority.get("required_tools"), list)
        else [],
        "available_tools": final_request_evidence.get("available_tools")
        if isinstance(final_request_evidence.get("available_tools"), list)
        else final_request_evidence_authority.get("available_tools")
        if isinstance(final_request_evidence_authority.get("available_tools"), list)
        else [],
        "missing_required_tools": final_request_evidence.get("missing_required_tools")
        if isinstance(final_request_evidence.get("missing_required_tools"), list)
        else [],
        "unexpected_tool_pruning": final_request_evidence.get("unexpected_tool_pruning")
        if isinstance(final_request_evidence.get("unexpected_tool_pruning"), list)
        else final_request_evidence_authority.get("unexpected_tool_pruning")
        if isinstance(final_request_evidence_authority.get("unexpected_tool_pruning"), list)
        else [],
        "tool_schema_registry_coverage": _as_dict(
            final_request_evidence.get("tool_schema_registry_coverage")
            or final_request_evidence_authority.get("tool_schema_registry_coverage")
        ),
        "workflow_chain": _as_dict(
            final_request_evidence.get("workflow_chain") or final_request_evidence_authority.get("workflow_chain")
        ),
        "source_path": source_path,
        "raw": raw,
    }


def _resolve_polaris_roots_runtime_dir(workspace: Path) -> Path | None:
    """Resolve the canonical runtime_root via resolve_polaris_roots if available."""
    try:
        from polaris.cells.storage.layout import resolve_polaris_roots

        roots = resolve_polaris_roots(str(workspace))
        runtime_root = roots.runtime_root
        if runtime_root:
            return Path(runtime_root)
    except (ImportError, RuntimeError, ValueError, OSError):
        pass
    return None


def _append_dispatch_route_events(
    normalized: list[dict[str, Any]],
    dispatch_data: dict[str, Any],
    *,
    source_path: str,
) -> None:
    """Append normalized LLM route events embedded in a Director dispatch log."""
    for raw in dispatch_data.get("fail_closed_route_events") or []:
        if not isinstance(raw, dict):
            continue
        item = _normalize_llm_event(raw, source_path=source_path)
        if item is not None:
            item["fail_closed"] = True
            normalized.append(item)
    for raw in dispatch_data.get("per_binding_route_events") or []:
        if not isinstance(raw, dict):
            continue
        item = _normalize_llm_event(raw, source_path=source_path)
        if item is not None:
            normalized.append(item)


def collect_llm_events(
    workspace: Path,
    runtime_dir: Path | Iterable[Path] | None,
    audit_bundle: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect normalized LLM invocation evidence from runtime artifacts."""
    candidates: set[Path] = set()
    if runtime_dir is None:
        runtime_dirs: list[Path] = []
    elif isinstance(runtime_dir, Path):
        runtime_dirs = [runtime_dir]
    else:
        runtime_dirs = [path for path in runtime_dir if isinstance(path, Path)]
    extra_bases: list[Path] = [
        workspace / ".polaris" / "runtime",
        workspace / ".polaris",
    ]
    polaris_roots_runtime = _resolve_polaris_roots_runtime_dir(workspace)
    if polaris_roots_runtime is not None:
        extra_bases.insert(0, polaris_roots_runtime)
    for base in (*runtime_dirs, *extra_bases):
        if base is None:
            continue
        candidates.update(base.glob("events/*.llm.events.jsonl"))
        candidates.update(base.glob("telemetry/events_*.jsonl"))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(candidates):
        for raw in _read_jsonl(path):
            item = _normalize_llm_event(raw, source_path=str(path))
            if item is None:
                continue
            key = json.dumps(
                {
                    "event": item.get("event"),
                    "role": item.get("role"),
                    "provider_id": item.get("provider_id"),
                    "model": item.get("model"),
                    "binding_id": item.get("binding_id"),
                    "tokens": item.get("total_tokens"),
                    "source_path": item.get("source_path"),
                    "event_id": raw.get("event_id"),
                    "seq": raw.get("seq"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)

    bundle = audit_bundle if isinstance(audit_bundle, dict) else {}
    for raw in bundle.get("events_tail") or []:
        if not isinstance(raw, dict):
            continue
        item = _normalize_llm_event(raw, source_path="audit_bundle.events_tail")
        if item is not None:
            normalized.append(item)
        result_payload = _as_dict(raw.get("result"))
        if result_payload:
            _append_dispatch_route_events(
                normalized,
                result_payload,
                source_path="audit_bundle.events_tail.result",
            )

    for base in (*runtime_dirs, *extra_bases):
        if base is None:
            continue
        dispatch_dir = base / "dispatch"
        if not dispatch_dir.is_dir():
            continue
        dispatch_logs = {dispatch_dir / "log.json"}
        dispatch_logs.update(dispatch_dir.glob("*.log.json"))
        for dispatch_log in sorted(path for path in dispatch_logs if path.exists()):
            try:
                dispatch_data = json.loads(dispatch_log.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(dispatch_data, dict):
                continue
            _append_dispatch_route_events(normalized, dispatch_data, source_path=str(dispatch_log))
    return normalized


def resolve_expected_llm_bindings(roles: tuple[str, ...] = _REQUIRED_LLM_ROLES) -> dict[str, list[dict[str, Any]]]:
    """Resolve actual configured role bindings from the runtime LLM config."""
    expected: dict[str, list[dict[str, Any]]] = {}
    try:
        from polaris.kernelone.llm.runtime_config import load_role_config, resolve_role_worker_plan
    except (ImportError, RuntimeError, ValueError):
        return expected
    for role in roles:
        normalized = _norm_role(role)
        rows: list[dict[str, Any]] = []
        try:
            if normalized == "director":
                slots = resolve_role_worker_plan(normalized)
                provider_ids = tuple(dict.fromkeys(str(slot.provider_id) for slot in slots if slot.provider_id))
                try:
                    from polaris.cells.orchestration.pm_dispatch.public.service import (
                        reachable_provider_pool,
                    )

                    live_provider_ids = set(reachable_provider_pool(provider_ids))
                except (ImportError, RuntimeError, ValueError, TypeError, OSError):
                    live_provider_ids = set()
                if live_provider_ids:
                    slots = [slot for slot in slots if str(slot.provider_id) in live_provider_ids]
                seen_routes: set[str] = set()
                for slot in slots:
                    route_key = f"{slot.provider_id}|{slot.model}"
                    if route_key in seen_routes:
                        continue
                    seen_routes.add(route_key)
                    rows.append(
                        {
                            "role": normalized,
                            "provider_id": slot.provider_id,
                            "model": slot.model,
                            "binding_id": slot.binding_id,
                            "slot_index": slot.slot_index,
                        }
                    )
            else:
                role_config = load_role_config(normalized)
                if role_config is not None and role_config.bindings:
                    rows = [
                        {
                            "role": normalized,
                            "provider_id": binding.provider_id,
                            "model": binding.model,
                            "binding_id": binding.binding_id,
                            "binding_index": binding.binding_index,
                        }
                        for binding in role_config.bindings
                    ]
                elif role_config is not None:
                    rows = [
                        {
                            "role": normalized,
                            "provider_id": role_config.provider_id,
                            "model": role_config.model,
                            "binding_id": "",
                        }
                    ]
        except (RuntimeError, ValueError, TypeError, OSError):
            rows = []
        expected[normalized] = rows
    return expected


def _binding_key(row: dict[str, Any]) -> str:
    provider = _norm_text(row.get("provider_id") or row.get("provider"))
    model = _norm_text(row.get("model"))
    binding_id = _norm_text(row.get("binding_id"))
    if binding_id:
        return f"{provider}|{model}|{binding_id}"
    return f"{provider}|{model}"


def _loose_binding_key(row: dict[str, Any]) -> str:
    return f"{_norm_text(row.get('provider_id') or row.get('provider'))}|{_norm_text(row.get('model'))}"


def _matches_family(role: str, row: dict[str, Any]) -> bool:
    alternatives = _ROLE_FAMILIES.get(role)
    if not alternatives:
        return True
    haystack = f"{row.get('provider_id') or row.get('provider') or ''} {row.get('model') or ''}".lower()
    return any(all(token in haystack for token in alternative) for alternative in alternatives)


def _is_real_llm_route_event(event: dict[str, Any]) -> bool:
    source = _norm_text(event.get("source"))
    data = _as_dict(event.get("data"))
    data_meta = _as_dict(data.get("metadata"))
    if not source or source.lower() != "llm":
        source = _norm_text(data_meta.get("source"))
    provider = _norm_text(event.get("provider_id") or event.get("provider"))
    model = _norm_text(event.get("model"))
    if not model:
        model = _norm_text(data.get("model"))
    cache_hit = bool(event.get("cache_hit") or data_meta.get("cached"))
    if event.get("skipped") or event.get("fail_closed"):
        return False
    return bool(event.get("invocation") and source.lower() == "llm" and not cache_hit and provider and model)


def _is_llm_route_skip_event(event: dict[str, Any]) -> bool:
    source = _norm_text(event.get("source"))
    data = _as_dict(event.get("data"))
    data_meta = _as_dict(data.get("metadata"))
    if not source or source.lower() != "llm":
        source = _norm_text(data_meta.get("source"))
    provider = _norm_text(event.get("provider_id") or event.get("provider"))
    model = _norm_text(event.get("model") or data.get("model"))
    reason = _norm_text(event.get("skip_reason") or data_meta.get("skip_reason"))
    allowed_reasons = {
        "provider_connectivity_unavailable",
        "provider_unreachable",
        "provider_readiness_failed",
        "role_binding_cooldown",
        "binding_unavailable",
    }
    return bool(source.lower() == "llm" and provider and model and event.get("skipped") and reason in allowed_reasons)


def _resolve_provider_from_expected(
    event: dict[str, Any],
    expected_bindings: dict[str, list[dict[str, Any]]],
) -> bool:
    if _norm_text(event.get("provider_id") or event.get("provider")):
        return False
    model = _norm_text(event.get("model"))
    if not model:
        data = _as_dict(event.get("data"))
        model = _norm_text(data.get("model"))
        if model:
            event["model"] = model
    if not model:
        return False
    role = _norm_role(event.get("role"))
    candidates = [
        row
        for row in expected_bindings.get(role, [])
        if _norm_text(row.get("model")) == model and _norm_text(row.get("provider_id") or row.get("provider"))
    ]
    if len(candidates) == 1:
        match = candidates[0]
        event["provider_id"] = _norm_text(match.get("provider_id") or match.get("provider"))
        binding_id = _norm_text(match.get("binding_id"))
        if binding_id:
            event["binding_id"] = binding_id
        return True
    return False


def build_llm_route_audit(
    events: list[dict[str, Any]],
    *,
    expected_bindings: dict[str, list[dict[str, Any]]] | None = None,
    required_roles: tuple[str, ...] = _REQUIRED_LLM_ROLES,
    require_all_director_routes: bool = True,
) -> dict[str, Any]:
    expected = (
        expected_bindings if isinstance(expected_bindings, dict) else resolve_expected_llm_bindings(required_roles)
    )
    candidate_events = [
        event for event in events if event.get("invocation") and _norm_role(event.get("role")) in required_roles
    ]
    for event in candidate_events:
        _resolve_provider_from_expected(event, expected)
    evidence = [event for event in candidate_events if _is_real_llm_route_event(event)]
    terminal = [event for event in evidence if event.get("terminal")]
    diagnostic_events = [
        event for event in events if _norm_role(event.get("role")) in required_roles and _is_llm_route_skip_event(event)
    ]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for event in terminal or evidence:
        by_role.setdefault(_norm_role(event.get("role")), []).append(event)
    diagnostic_by_role: dict[str, list[dict[str, Any]]] = {}
    for event in diagnostic_events:
        _resolve_provider_from_expected(event, expected)
        diagnostic_by_role.setdefault(_norm_role(event.get("role")), []).append(event)

    role_results: dict[str, dict[str, Any]] = {}
    ok = True
    for role in required_roles:
        normalized = _norm_role(role)
        configured = list(expected.get(normalized) or [])
        observed = list(by_role.get(normalized) or [])
        configured_keys = {_binding_key(row) for row in configured if _loose_binding_key(row) != "|"}
        configured_loose = {_loose_binding_key(row) for row in configured if _loose_binding_key(row) != "|"}
        observed_keys = {_binding_key(row) for row in observed if _loose_binding_key(row) != "|"}
        observed_loose = {_loose_binding_key(row) for row in observed if _loose_binding_key(row) != "|"}
        skipped = list(diagnostic_by_role.get(normalized) or [])
        skipped_keys = {_binding_key(row) for row in skipped if _loose_binding_key(row) != "|"}
        skipped_loose = {_loose_binding_key(row) for row in skipped if _loose_binding_key(row) != "|"}
        missing = sorted(
            key
            for key in configured_keys
            if key not in observed_keys
            and key not in skipped_keys
            and key.rsplit("|", 1)[0] not in observed_loose
            and key.rsplit("|", 1)[0] not in skipped_loose
        )
        configured_match_ok = bool(observed_keys.intersection(configured_keys)) or bool(
            observed_loose.intersection(configured_loose)
        )
        family_ok = configured_match_ok or any(_matches_family(normalized, row) for row in observed)
        binding_ok = bool(configured) and bool(observed) and not missing
        if normalized != "director" and configured_loose:
            binding_ok = bool(observed_loose.intersection(configured_loose))
        multi_route_ok = True
        if normalized == "director":
            configured_routes = configured_loose
            multi_route_ok = bool(observed) and bool(configured_routes) and not missing
            if require_all_director_routes:
                binding_ok = binding_ok and multi_route_ok
            else:
                binding_ok = bool(observed) and (
                    not configured_routes or bool(observed_loose.intersection(configured_loose))
                )
        role_ok = binding_ok and family_ok
        role_results[normalized] = {
            "ok": role_ok,
            "configured": configured,
            "observed_count": len(observed),
            "observed_bindings": sorted(observed_loose),
            "skipped_bindings": sorted(skipped_loose),
            "fail_closed_count": len(skipped),
            "missing_bindings": missing,
            "family_ok": family_ok,
            "multi_route_ok": multi_route_ok,
            "multi_route_required": bool(normalized == "director" and require_all_director_routes),
        }
        ok = ok and role_ok

    failing_roles = [role for role, result in role_results.items() if not result.get("ok")]
    return {
        "ok": ok,
        "roles": role_results,
        "events_observed": len(evidence),
        "candidate_events_observed": len(candidate_events),
        "events_rejected": len(candidate_events) - len(evidence),
        "terminal_events_observed": len(terminal),
        "summary": "LLM route audit passed" if ok else "LLM route audit failed: " + ", ".join(failing_roles),
    }


def _gate_failures(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [gate for gate in record.get("factory_gates") or [] if isinstance(gate, dict) and not gate.get("ok")]


def _check_failures(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [check for check in record.get("checks") or [] if isinstance(check, dict) and not check.get("ok")]


def _contains_context_budget_signal(text: str) -> bool:
    return bool(
        re.search(
            r"context[_ -]?(?:window|budget|length|limit)|"
            r"token[_ -]?budget|max[_ -]?tokens|"
            r"context_length_exceeded|prompt[_ -]?too[_ -]?long|"
            r"input[_ -]?tokens?[^.]{0,80}(?:exceed|limit)|"
            r"(?:context|prompt|message)[_ -]?truncated",
            text,
            re.IGNORECASE,
        )
    )


def _first_real_run_failure(real_run_gate: dict[str, Any]) -> str:
    requirements = real_run_gate.get("requirements")
    if not isinstance(requirements, dict):
        return ""
    for name, payload in requirements.items():
        if isinstance(payload, dict) and not payload.get("ok"):
            return str(name)
    return ""


def _category_signature(category: str, reason: str) -> str:
    stable_category = category if category in _FAILURE_CATEGORIES else "unknown"
    stable_reason = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", reason.strip().lower()).strip("_") or "unknown"
    return f"{stable_category}:{stable_reason}"


def _iter_mapping_payloads(value: Any, *, limit: int = 600) -> Iterable[dict[str, Any]]:
    """Yield nested dict payloads without treating text projections as facts."""

    stack: list[Any] = [value]
    seen = 0
    while stack and seen < limit:
        current = stack.pop()
        if isinstance(current, dict):
            seen += 1
            yield current
            stack.extend(current.values())
        elif isinstance(current, list | tuple):
            stack.extend(current)


_TASK_BOUNDARY_FAILURE_STATUSES = frozenset(
    {
        "artifact_semantic_mismatch",
        "dependency_not_unlocked",
        "execution_evidence_missing",
        "incomplete_materialization",
        "missing_entrypoint_target",
        "required_evidence_failed",
        "required_verifier_failed",
        "required_verifier_missing",
        "tool_dispatch_dropped",
        "unresolved_local_import",
    }
)
_TASK_BOUNDARY_FAILURE_CLASSES = frozenset(
    {
        "blueprint_scope_mismatch",
        "dependency_not_unlocked",
        "execution_evidence_missing",
        "implementation_defect",
        "incomplete_materialization",
        "missing_entrypoint_target",
        "tool_dispatch_dropped",
        "unresolved_local_import",
    }
)


def _runtime_dir_candidates(record: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    runtime_dir = str(record.get("runtime_dir") or "").strip()
    if runtime_dir:
        candidates.append(Path(runtime_dir))
    runtime_dirs = record.get("runtime_dirs")
    if isinstance(runtime_dirs, list):
        for item in runtime_dirs:
            path_text = str(item or "").strip()
            if path_text:
                candidates.append(Path(path_text))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _load_runtime_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_director_result_payloads(record: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for runtime_dir in _runtime_dir_candidates(record):
        candidates = [runtime_dir / "results" / "director.result.json"]
        runs_dir = runtime_dir / "runs"
        if runs_dir.is_dir():
            with suppress(OSError):
                candidates.extend(sorted(runs_dir.glob("*/results/director.result.json")))
        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            payload = _load_runtime_json(path)
            if payload:
                payloads.append(payload)
    return payloads


def _task_result_boundary_payload(task_result: dict[str, Any]) -> dict[str, Any]:
    adapter_result = task_result.get("adapter_result")
    adapter = adapter_result if isinstance(adapter_result, dict) else {}
    task_id = str(task_result.get("task_id") or adapter.get("task_id") or "").strip()
    raw_status = str(task_result.get("status") or "").strip().lower()
    raw_error = str(task_result.get("error") or adapter.get("materialization_error") or "").strip()
    raw_failure_class = str(adapter.get("failure_class") or task_result.get("failure_class") or "").strip()
    responsible_layer = str(adapter.get("responsible_layer") or "").strip()

    status = str(adapter.get("status") or "").strip()
    failure_class = raw_failure_class
    reason = ""
    if raw_error in {"director_no_materialized_changes", "no_materialized_changes"}:
        status = "incomplete_materialization"
        failure_class = failure_class or "INCOMPLETE_MATERIALIZATION"
        reason = "Director task produced no materialized workspace changes before timeout or completion"
    elif raw_error == "blocked_by_failed_dependency" or raw_status == "blocked":
        status = "dependency_not_unlocked"
        failure_class = failure_class or "DEPENDENCY_NOT_UNLOCKED"
        blocked_by = task_result.get("blocked_by")
        reason = f"Blocked by failed dependency: {blocked_by}" if blocked_by else "Blocked by failed dependency"
    elif failure_class.lower() in _TASK_BOUNDARY_FAILURE_CLASSES:
        status = status or failure_class.lower()
        reason = str(adapter.get("materialization_error") or adapter.get("reason") or "").strip()

    if not status and not failure_class:
        return {}
    return {
        "schema_version": "polaris.task_boundary_verdict.synthetic_from_director_result.v1",
        "task_id": task_id,
        "status": status or "task_boundary_failed",
        "failure_class": failure_class,
        "responsible_layer": responsible_layer or "task_boundary",
        "reason": reason,
    }


def _runtime_director_task_boundary_payloads(record: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for director_result in _runtime_director_result_payloads(record):
        task_results = director_result.get("task_results")
        if not isinstance(task_results, list):
            continue
        for item in task_results:
            if not isinstance(item, dict):
                continue
            payload = _task_result_boundary_payload(item)
            if payload:
                payloads.append(payload)
    return payloads


def _first_repair_plan_probe(record: dict[str, Any]) -> dict[str, Any]:
    """Return the first nested repair plan-probe payload with stable structure."""

    for payload in _iter_mapping_payloads(record):
        for key in (
            "plan_probe_preaudit",
            "repair_plan_probe",
            "workspace_quality_repair_plan_probe",
            "workspace_quality_repair_plan_probe_report",
        ):
            nested = payload.get(key)
            if isinstance(nested, dict) and _is_repair_plan_probe_payload(nested):
                return nested
        if _is_repair_plan_probe_payload(payload):
            return payload
    return {}


def _is_repair_plan_probe_payload(payload: dict[str, Any]) -> bool:
    schema = str(payload.get("schema_version") or "")
    status = str(payload.get("status") or "")
    if schema.startswith("director.repair_plan_probe_result"):
        return True
    return bool(
        status
        and (
            "plannable_source_tools" in payload
            or "covered_unplannable_source_tools" in payload
            or "covered_unplannable_diagnostics" in payload
            or "uncovered_diagnostics" in payload
        )
    )


def _record_repair_convergence_attribution(record: dict[str, Any]) -> tuple[str, str, str] | None:
    """Classify failures where runtime repair knows a concrete plan but convergence still failed."""

    plan_probe = _first_repair_plan_probe(record)
    if not plan_probe:
        return None

    status = str(plan_probe.get("status") or "").strip()
    plannable_source_tools = [
        str(item).strip() for item in plan_probe.get("plannable_source_tools") or [] if str(item or "").strip()
    ]
    covered_unplannable_source_tools = [
        str(item).strip()
        for item in plan_probe.get("covered_unplannable_source_tools") or []
        if str(item or "").strip()
    ]
    if status == "covered_plannable" and plannable_source_tools:
        return (
            "repair_convergence",
            "covered_plannable_not_converged",
            f"plan_probe:covered_plannable;plannable_source_tools={','.join(plannable_source_tools[:8])}",
        )
    if status == "coverage_matched_but_unplannable" or covered_unplannable_source_tools:
        return (
            "task_boundary",
            "repair_plan_probe_unplannable",
            "plan_probe:coverage_matched_but_unplannable;"
            f"covered_unplannable_source_tools={','.join(covered_unplannable_source_tools[:8])}",
        )
    return None


def _first_task_boundary_verdict(record: dict[str, Any]) -> dict[str, Any]:
    """Find a failed TaskBoundary verdict projected anywhere in a bench record."""

    for payload in _runtime_director_task_boundary_payloads(record):
        return payload

    for payload in _iter_mapping_payloads(record):
        schema = str(payload.get("schema_version") or "").strip()
        status = str(payload.get("status") or payload.get("verdict_status") or "").strip()
        failure_class = str(payload.get("failure_class") or "").strip()
        if status in {"completed_verified", "passed"} or failure_class.lower() == "passed":
            continue
        if bool(payload.get("ok")) is True:
            continue
        if (
            schema.startswith("task_boundary.")
            or status in _TASK_BOUNDARY_FAILURE_STATUSES
            or failure_class.lower() in _TASK_BOUNDARY_FAILURE_CLASSES
        ):
            return payload
    return {}


def _record_task_boundary_attribution(record: dict[str, Any]) -> tuple[str, str, str] | None:
    verdict = _first_task_boundary_verdict(record)
    if not verdict:
        return None

    status = str(verdict.get("status") or verdict.get("verdict_status") or "").strip() or "task_boundary_failed"
    failure_class = str(verdict.get("failure_class") or "").strip()
    responsible_layer = str(verdict.get("responsible_layer") or "").strip()
    reason = str(verdict.get("reason") or "").strip()
    if status == "tool_dispatch_dropped" or failure_class.lower() == "tool_dispatch_dropped":
        return (
            "control_plane",
            "tool_dispatch_dropped",
            reason or "TaskBoundary verdict: tool dispatch dropped",
        )
    return (
        "task_boundary",
        status,
        ";".join(
            item
            for item in (
                f"failure_class={failure_class}" if failure_class else "",
                f"responsible_layer={responsible_layer}" if responsible_layer else "",
                reason,
            )
            if item
        ),
    )


_EXECUTION_CONTROL_PLANE_FAILURE_TOKENS: tuple[tuple[str, str], ...] = (
    ("session_not_active", "session_not_active"),
    ("tool_dispatch_failed", "tool_dispatch_failed"),
    ("decoded tool batch produced only failed tool results", "tool_dispatch_failed"),
    ("tool batch produced only failed tool results", "tool_dispatch_failed"),
)


def _record_execution_control_plane_attribution(record: dict[str, Any]) -> tuple[str, str, str] | None:
    """Classify provider/tool/effect/session transaction failures."""

    text = json.dumps(
        {
            "failure_reasons": record.get("failure_reasons"),
            "failure_evidence": record.get("failure_evidence"),
            "chain": record.get("chain"),
            "chain_diagnostics": record.get("chain_diagnostics"),
            "factory_gates": record.get("factory_gates"),
            "real_run_gate": record.get("real_run_gate"),
            "runtime_director_results": _runtime_director_result_payloads(record),
        },
        ensure_ascii=False,
        default=str,
    ).lower()
    for token, reason in _EXECUTION_CONTROL_PLANE_FAILURE_TOKENS:
        if token in text:
            return (
                "control_plane",
                reason,
                _execution_control_plane_evidence(record, token=token) or token,
            )
    return None


def _execution_control_plane_evidence(record: dict[str, Any], *, token: str) -> str:
    token_lower = str(token or "").lower()
    candidate_roots = (
        record.get("failure_evidence"),
        record.get("failure_reasons"),
        record.get("chain_diagnostics"),
        record.get("chain"),
        record.get("factory_gates"),
        record.get("real_run_gate"),
        _runtime_director_result_payloads(record),
    )
    for root in candidate_roots:
        for text in _iter_strings(root):
            if token_lower in text.lower():
                return text[:1000]
    return ""


def _iter_strings(value: Any) -> Iterator[str]:
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            text = item.strip()
            if text:
                yield text
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            stack.extend(item)


def _check_failure_is_runtime_environment(check: dict[str, Any]) -> bool:
    text = json.dumps(check, ensure_ascii=False, default=str)
    return bool(re.search(r"\bunavailable\b|not found|toolchain unavailable|compiler unavailable", text, re.IGNORECASE))


def _record_has_generated_artifact_failure(record: dict[str, Any]) -> bool:
    """Return true when the failure points at malformed generated artifacts."""
    failed_checks = _check_failures(record)
    if any(
        str(check.get("check") or "").lower() in {"ts_syntax", "js_syntax", "py_compile"} for check in failed_checks
    ):
        return True

    text = json.dumps(
        {
            "checks": failed_checks,
            "real_run_gate": record.get("real_run_gate"),
        },
        ensure_ascii=False,
        default=str,
    )
    return bool(
        re.search(
            r"syntax check failed|syntaxerror|unexpected keyword|"
            r"\bTS\d{3,5}\b|compile failed|build failed|test failed|lint failed|"
            r"npm run (?:build|test|lint|check) failed|"
            r"package\.json missing devDependency 'typescript'|"
            r"shell command substitution|package manifest script|"
            r"sh:\s*\d+:\s*[A-Za-z0-9_.-]+:\s*not found|invalid source content",
            text,
            re.IGNORECASE,
        )
    )


def _nested_chain_results(record: dict[str, Any]) -> dict[str, Any]:
    chain_results = record.get("chain_results")
    if isinstance(chain_results, dict):
        return chain_results
    chain = record.get("chain")
    if isinstance(chain, dict):
        chain_results = chain.get("chain_results")
        if isinstance(chain_results, dict):
            return chain_results
    return {}


def _director_failure_evidence(record: dict[str, Any]) -> str:
    chain = record.get("chain")
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if isinstance(failure, dict):
        detail = str(failure.get("detail") or failure.get("code") or "").strip()
        if detail:
            return detail
    return ""


def _director_failure_tokens(record: dict[str, Any]) -> str:
    chain = record.get("chain")
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if not isinstance(failure, dict):
        return ""
    values: list[str] = []
    for key in (
        "detail",
        "code",
        "error_code",
        "reason_code",
        "failure_class",
        "materialization_error",
        "materialization_mode",
    ):
        value = str(failure.get(key) or "").strip()
        if value:
            values.append(value)
    return "\n".join(values).lower()


def _director_failure_reason(record: dict[str, Any]) -> str:
    text = _director_failure_tokens(record)
    if "binding fanout" in text or "quarantined" in text:
        return "director_binding_fanout_failed"
    if (
        "director_materialization_quality_failed" in text
        or "director_missing_write_receipt" in text
        or "director_no_materialized_changes" in text
    ):
        return "director_materialization_failed"
    if "director.run_status_non_success" in text:
        return "director_run_status_non_success"
    return "director_execution_failed"


def _record_has_director_execution_failure(record: dict[str, Any]) -> bool:
    chain_results = _nested_chain_results(record)
    director = chain_results.get("director") if isinstance(chain_results, dict) else {}
    if isinstance(director, dict) and (int(director.get("failures") or 0) > 0 or int(director.get("blocked") or 0) > 0):
        return True

    text = json.dumps(
        {
            "terminal_status": record.get("terminal_status"),
            "chain": record.get("chain"),
            "failed_gates": _gate_failures(record),
        },
        ensure_ascii=False,
        default=str,
    )
    return bool(
        re.search(
            r"director(?:[_ .-]?binding)?[_ .-]?fanout|"
            r"director[_ .-]?dispatch failed|"
            r"director[_ .-]?materialization[_ .-]?quality[_ .-]?failed|"
            r"director[_ .-]?missing[_ .-]?write[_ .-]?receipt|"
            r"director[_ .-]?no[_ .-]?materialized[_ .-]?changes|"
            r"director\.run_status_non_success|"
            r"director_partial|"
            r"\\bquarantined\\b",
            text,
            re.IGNORECASE,
        )
    )


def _record_has_explicit_director_execution_failure(record: dict[str, Any]) -> bool:
    chain_results = _nested_chain_results(record)
    director = chain_results.get("director") if isinstance(chain_results, dict) else {}
    if isinstance(director, dict) and (int(director.get("failures") or 0) > 0 or int(director.get("blocked") or 0) > 0):
        return True

    text = json.dumps(
        {
            "chain": record.get("chain"),
            "failed_gates": _gate_failures(record),
        },
        ensure_ascii=False,
        default=str,
    )
    return bool(
        re.search(
            r"director(?:[_ .-]?binding)?[_ .-]?fanout|"
            r"director[_ .-]?dispatch failed|"
            r"director[_ .-]?materialization[_ .-]?quality[_ .-]?failed|"
            r"director[_ .-]?missing[_ .-]?write[_ .-]?receipt|"
            r"director[_ .-]?no[_ .-]?materialized[_ .-]?changes|"
            r"director\.run_status_non_success|"
            r"\\bquarantined\\b",
            text,
            re.IGNORECASE,
        )
    )


_RUNTIME_ENVIRONMENT_FAILURE_TOKENS = (
    "cognitive_runtime_mainline_unavailable",
    "event_wait_timeout",
    "runtime_v2_connection_failed",
    "mainline_unavailable:process",
    "process:filenotfounderror",
    "pm.runtime.exception",
    "runtime.environment",
    "workspace_switch_failed",
)
_MODEL_PROVIDER_RATE_LIMIT_TOKENS = (
    "director.provider_rate_limit",
    "rate_limit",
    "rate limit",
    "rate-limited",
    "too many requests",
    "token plan",
    "用量上限",
)
_MODEL_PROVIDER_UNAVAILABLE_TOKENS = (
    "director.provider_unavailable",
    "provider_timeout",
    "request timeout",
    "transport timeout",
    "circuit_open",
    "circuit breaker is open",
)
_MODEL_PROVIDER_INVALID_REQUEST_TOKENS = (
    "invalid_request_error",
    "tool_choice",
    "incompatible with thinking",
    "thinking mode does not support",
)


def _has_model_provider_invalid_request(text: str) -> bool:
    lowered = str(text or "").lower()
    return all(token in lowered for token in _MODEL_PROVIDER_INVALID_REQUEST_TOKENS[:3]) or (
        "thinking mode does not support" in lowered and "tool_choice" in lowered
    )


def _record_model_provider_failure_text(record: dict[str, Any]) -> str:
    event_error_texts: list[str] = []
    for event in _record_llm_events(record):
        if str(event.get("role") or "").strip().lower() != "director":
            continue
        if bool(event.get("skipped")):
            continue
        event_name = str(event.get("event") or "").strip().lower()
        if event_name not in {"llm_error", "call_error", "error"} and not bool(event.get("terminal")):
            continue
        error_text = _llm_event_error_text(event)
        if error_text:
            event_error_texts.append(error_text)
    return json.dumps(
        {
            "failure_reasons": record.get("failure_reasons"),
            "failure_evidence": record.get("failure_evidence"),
            "chain": record.get("chain"),
            "chain_diagnostics": record.get("chain_diagnostics"),
            "llm_route_audit": record.get("llm_route_audit"),
            "factory_gates": record.get("factory_gates"),
            "llm_event_errors": event_error_texts,
        },
        ensure_ascii=False,
        default=str,
    ).lower()


def _record_llm_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    workspace_text = str(record.get("workspace") or record.get("project_workspace") or "").strip()
    if not workspace_text:
        return []
    runtime_candidates: list[Path] = []
    runtime_dir = str(record.get("runtime_dir") or "").strip()
    if runtime_dir:
        runtime_candidates.append(Path(runtime_dir))
    runtime_dirs = record.get("runtime_dirs")
    if isinstance(runtime_dirs, list):
        for item in runtime_dirs:
            path_text = str(item or "").strip()
            if path_text:
                runtime_candidates.append(Path(path_text))
    try:
        return collect_llm_events(Path(workspace_text), runtime_candidates or None)
    except (OSError, RuntimeError, ValueError, TypeError):
        return []


def _llm_event_error_text(event: dict[str, Any]) -> str:
    raw_value = event.get("raw")
    raw = raw_value if isinstance(raw_value, dict) else {}
    data_value = raw.get("data")
    data = data_value if isinstance(data_value, dict) else {}
    metadata_value = raw.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    data_metadata_value = data.get("metadata")
    data_metadata = data_metadata_value if isinstance(data_metadata_value, dict) else {}
    parts: list[str] = []
    for source in (event, raw, data, metadata, data_metadata):
        for key in (
            "event",
            "event_type",
            "error_category",
            "error_code",
            "error_message",
            "message",
            "status",
            "retry_decision",
        ):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(parts)


def _record_has_model_provider_failure(record: dict[str, Any]) -> bool:
    text = _record_model_provider_failure_text(record)
    return (
        any(token in text for token in _MODEL_PROVIDER_RATE_LIMIT_TOKENS)
        or any(token in text for token in _MODEL_PROVIDER_UNAVAILABLE_TOKENS)
        or _has_model_provider_invalid_request(text)
    )


def _model_provider_failure_reason(record: dict[str, Any]) -> str:
    text = _record_model_provider_failure_text(record)
    if any(token in text for token in _MODEL_PROVIDER_RATE_LIMIT_TOKENS):
        return "director_provider_rate_limit"
    if any(token in text for token in _MODEL_PROVIDER_UNAVAILABLE_TOKENS):
        return "director_provider_unavailable"
    if _has_model_provider_invalid_request(text):
        return "director_provider_invalid_request"
    return "model_provider_failure"


def _model_provider_failure_evidence(record: dict[str, Any]) -> str:
    for event in _record_llm_events(record):
        if str(event.get("role") or "").strip().lower() != "director":
            continue
        error_text = _llm_event_error_text(event)
        lowered = error_text.lower()
        if (
            any(token in lowered for token in _MODEL_PROVIDER_RATE_LIMIT_TOKENS)
            or any(token in lowered for token in _MODEL_PROVIDER_UNAVAILABLE_TOKENS)
            or _has_model_provider_invalid_request(lowered)
        ):
            return error_text[:1000]
    chain = record.get("chain")
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if isinstance(failure, dict):
        detail = str(failure.get("detail") or failure.get("code") or "").strip()
        if detail:
            return detail
    return ""


def _record_has_runtime_environment_failure(record: dict[str, Any]) -> bool:
    text = json.dumps(
        {
            "failure_reasons": record.get("failure_reasons"),
            "failure_evidence": record.get("failure_evidence"),
            "chain": record.get("chain"),
            "chain_diagnostics": record.get("chain_diagnostics"),
            "factory_gates": record.get("factory_gates"),
        },
        ensure_ascii=False,
        default=str,
    ).lower()
    if "event_wait_timeout" in text or "runtime_v2_connection_failed" in text:
        return True
    if "workspace_switch_failed" in text:
        return True
    if "runtime_roles_not_ready" in text:
        return True
    if "cognitive_runtime_mainline_unavailable" in text:
        return True
    if "no available director binding after readiness filtering" in text:
        return True
    if (
        "active_binding_count" in text
        and "provider_unreachable" in text
        and re.search(r'"active_binding_count"\s*:\s*0', text)
    ):
        return True
    return "filenotfounderror" in text and (
        "pm.run_status_non_success" in text or "pm.runtime.exception" in text or "mainline_unavailable" in text
    )


def _runtime_environment_failure_reason(record: dict[str, Any]) -> str:
    text = json.dumps(record, ensure_ascii=False, default=str).lower()
    if "runtime_v2_connection_failed" in text:
        return "event_wait_runtime_v2_connection_failed"
    if "event_wait_timeout" in text:
        return "event_wait_timeout"
    if "workspace_switch_failed" in text:
        return "workspace_switch_failed"
    if "runtime_roles_not_ready" in text:
        return "runtime_roles_not_ready"
    if "cognitive_runtime_mainline_unavailable" in text:
        return "cognitive_runtime_mainline_unavailable"
    if "no available director binding after readiness filtering" in text or (
        "provider_unreachable" in text and re.search(r'"active_binding_count"\s*:\s*0', text)
    ):
        return "director_bindings_unavailable"
    if "filenotfounderror" in text:
        return "file_not_found"
    return "runtime_environment_failed"


def _runtime_environment_failure_evidence(record: dict[str, Any]) -> str:
    chain = record.get("chain")
    diagnostics = record.get("chain_diagnostics")
    if isinstance(diagnostics, dict):
        event_wait_error = diagnostics.get("event_wait_error")
        if isinstance(event_wait_error, dict):
            detail = str(event_wait_error.get("message") or event_wait_error.get("kind") or "").strip()
            if detail:
                return detail
        cancel_error = diagnostics.get("cancel_error")
        if isinstance(cancel_error, dict):
            detail = str(cancel_error.get("reason") or cancel_error.get("exception") or "").strip()
            if detail:
                return detail
    if isinstance(chain, dict):
        event_wait_error = chain.get("event_wait_error")
        if isinstance(event_wait_error, dict):
            detail = str(event_wait_error.get("message") or event_wait_error.get("kind") or "").strip()
            if detail:
                return detail
    real_run_gate = record.get("real_run_gate")
    if isinstance(real_run_gate, dict) and real_run_gate.get("skipped"):
        detail = str(real_run_gate.get("summary") or "").strip()
        if detail:
            return detail
    if isinstance(chain, dict) and str(chain.get("error") or "") == "workspace_switch_failed":
        workspace_switch = chain.get("workspace_switch")
        if isinstance(workspace_switch, dict):
            detail = str(workspace_switch.get("workspace") or workspace_switch.get("detail") or "").strip()
            if detail:
                return detail
    start_error = chain.get("start_error") if isinstance(chain, dict) else {}
    if isinstance(start_error, dict):
        payload = start_error.get("json")
        if isinstance(payload, dict):
            detail = json.dumps(payload, ensure_ascii=False, default=str)
            if detail:
                return detail
        detail = str(start_error.get("body") or "").strip()
        if detail:
            return detail
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if isinstance(failure, dict):
        detail = str(failure.get("detail") or failure.get("code") or "").strip()
        if detail:
            return detail
    for key in ("failure_evidence", "failure_reasons"):
        values = record.get(key)
        if isinstance(values, list):
            for value in values:
                text = str(value or "").strip()
                if text:
                    return text
    return ""


def _record_has_qa_artifact_quality_failure(record: dict[str, Any]) -> bool:
    """Return true when QA ran and failed on malformed generated artifacts."""
    chain_results = _nested_chain_results(record)
    if not bool(chain_results.get("qa_ran")):
        return False
    real_run_gate = record.get("real_run_gate")
    if not (isinstance(real_run_gate, dict) and not real_run_gate.get("ok")):
        return False
    return _record_has_generated_artifact_failure(record)


def _chief_engineer_failure_evidence(record: dict[str, Any]) -> str:
    chain = record.get("chain")
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if isinstance(failure, dict):
        detail = str(failure.get("detail") or failure.get("code") or "").strip()
        if detail:
            return detail
    return ""


def _chief_engineer_failure_reason(record: dict[str, Any]) -> str:
    text = json.dumps(record.get("chain") or {}, ensure_ascii=False, default=str).lower()
    if "chief_engineer.llm_review_failed" in text or "no json object matched chief_engineer blueprint keys" in text:
        return "llm_review_failed"
    if _has_partial_chief_engineer_blueprint_generation(text):
        return "partial_blueprint_generation"
    return "missing_or_invalid_blueprint"


_CE_BLUEPRINT_GENERATED_RE = re.compile(
    r"chief engineer review generated\s+(?P<generated>\d+)\s*/\s*(?P<total>\d+)\s+blueprints",
    re.IGNORECASE,
)


def _has_partial_chief_engineer_blueprint_generation(text: str) -> bool:
    for match in _CE_BLUEPRINT_GENERATED_RE.finditer(str(text or "")):
        try:
            generated = int(match.group("generated"))
            total = int(match.group("total"))
        except (TypeError, ValueError):
            continue
        if total > 0 and generated < total:
            return True
    return False


def _record_has_chief_engineer_blueprint_failure(record: dict[str, Any]) -> bool:
    if record.get("has_blueprint_doc") is False or any(
        gate.get("gate") == "blueprint_artifact_present" and not gate.get("ok") for gate in _gate_failures(record)
    ):
        return True

    text = json.dumps(
        {
            "chain_state": record.get("chain_state"),
            "chain": record.get("chain"),
            "director_convergence": record.get("director_convergence"),
        },
        ensure_ascii=False,
        default=str,
    )
    if str(record.get("chain_state") or "") == "clean":
        return False
    return bool(
        re.search(
            r"chief_engineer\.llm_review_failed|"
            r"no json object matched chief_engineer blueprint keys|"
            r"current_stage['\"]?:\s*['\"]chief_engineer_review|"
            r"blocking_phase['\"]?:\s*['\"]chief_engineer_review",
            text,
            re.IGNORECASE,
        )
        or _has_partial_chief_engineer_blueprint_generation(text)
    )


def _run_ledger_projection_integrity_available(record: dict[str, Any]) -> bool:
    projection = record.get("run_ledger_projection")
    if not isinstance(projection, dict):
        return False
    if projection.get("source") != "run_ledger":
        return False
    if int(projection.get("gate_count") or 0) <= 0:
        return False
    if not bool(projection.get("integrity_ok")):
        return False
    evidence_policy = projection.get("evidence_policy")
    evidence_policy_map = evidence_policy if isinstance(evidence_policy, dict) else {}
    missing_required = evidence_policy_map.get("missing_required_modalities")
    return not (isinstance(missing_required, list) and bool(missing_required))


def classify_factory_bench_failure(record: dict[str, Any]) -> dict[str, Any]:
    """Assign one stable root-cause category to a per-project bench record."""
    if record.get("all_checks_passed"):
        return {
            "ok": True,
            "category": "",
            "root_cause_signature": "pass",
            "reasons": [],
            "evidence": [],
        }

    evidence: list[str] = []
    reasons: list[str] = []
    combined = json.dumps(record, ensure_ascii=False, default=str)
    run_ledger_gate_failed = any(
        gate.get("gate") in {"run_ledger_projection", "run_ledger_event"} and not gate.get("ok")
        for gate in _gate_failures(record)
    )
    if _record_has_model_provider_failure(record):
        category, reason = "runtime_environment", _model_provider_failure_reason(record)
        evidence.append(_model_provider_failure_evidence(record))
    elif (control_plane_attribution := _record_execution_control_plane_attribution(record)) is not None:
        category, reason, detail = control_plane_attribution
        evidence.append(detail)
    elif (task_boundary_attribution := _record_task_boundary_attribution(record)) is not None:
        category, reason, detail = task_boundary_attribution
        evidence.append(detail)
    elif _record_has_runtime_environment_failure(record):
        category, reason = "runtime_environment", _runtime_environment_failure_reason(record)
        evidence.append(_runtime_environment_failure_evidence(record))
        if run_ledger_gate_failed:
            ledger_status = summarize_run_ledger_projection(record.get("run_ledger_projection"))
            ledger_detail = str(ledger_status.get("detail") or "run ledger projection missing").strip()
            if ledger_detail:
                evidence.append(f"secondary_run_ledger:{ledger_detail}")
    elif run_ledger_gate_failed and not _run_ledger_projection_integrity_available(record):
        ledger_status = summarize_run_ledger_projection(record.get("run_ledger_projection"))
        category, reason = "control_plane", "run_ledger_projection_missing"
        evidence.append(str(ledger_status.get("detail") or "run ledger projection missing"))
    elif _contains_context_budget_signal(combined):
        category, reason = "context_budget", "context_or_token_budget"
    elif _record_has_chief_engineer_blueprint_failure(record):
        category, reason = "chief_engineer_blueprint", _chief_engineer_failure_reason(record)
        evidence.append(_chief_engineer_failure_evidence(record))
    elif isinstance(record.get("llm_route_audit"), dict) and not record["llm_route_audit"].get("ok"):
        category, reason = "llm_output", "llm_route_audit"
        evidence.append(str(record["llm_route_audit"].get("summary") or ""))
    elif (repair_attribution := _record_repair_convergence_attribution(record)) is not None:
        category, reason, detail = repair_attribution
        evidence.append(detail)
    elif _record_has_qa_artifact_quality_failure(record):
        real_run_gate = record["real_run_gate"]
        failed_requirement = _first_real_run_failure(real_run_gate)
        category, reason = "llm_output", f"real_run_gate.{failed_requirement or 'generated_artifact_quality'}"
        evidence.append(str(real_run_gate.get("summary") or ""))
    elif _record_has_explicit_director_execution_failure(record) or _record_has_director_execution_failure(record):
        category, reason = "director_tool_execution", _director_failure_reason(record)
        evidence.append(_director_failure_evidence(record))
        real_run_gate = record.get("real_run_gate")
        if isinstance(real_run_gate, dict) and not real_run_gate.get("ok"):
            summary = str(real_run_gate.get("summary") or "").strip()
            if summary:
                evidence.append(f"secondary_real_run_gate:{summary}")
    elif isinstance(record.get("real_run_gate"), dict) and not record["real_run_gate"].get("ok"):
        failed_requirement = _first_real_run_failure(record["real_run_gate"])
        reason = f"real_run_gate.{failed_requirement or 'unknown'}"
        if failed_requirement == "chain_terminal":
            category = "runtime_environment"
        elif failed_requirement == "artifact_landed":
            category = "director_tool_execution"
        elif failed_requirement == "environment_prepared" and _record_has_generated_artifact_failure(record):
            category = "llm_output"
        elif failed_requirement == "environment_prepared":
            category = "runtime_environment"
        elif _record_has_generated_artifact_failure(record):
            category = "llm_output"
        else:
            category = "target_project_baseline"
        evidence.append(str(record["real_run_gate"].get("summary") or ""))
    elif any(gate.get("gate") == "integration_qa_passed" and not gate.get("ok") for gate in _gate_failures(record)):
        category, reason = "llm_output", "integration_qa_failed"
        chain_results = record.get("chain_results") if isinstance(record.get("chain_results"), dict) else {}
        if isinstance(chain_results, dict):
            evidence.append(str(chain_results.get("qa_reason") or ""))
    elif not record.get("has_plan_doc") or record.get("wrong_product_suspect"):
        category = "pm_contract"
        reason = "missing_or_wrong_contract"
    elif str(record.get("chain_state") or "") != "clean":
        director = _nested_chain_results(record).get("director", {})
        if isinstance(director, dict) and (
            int(director.get("failures") or 0) > 0 or int(director.get("blocked") or 0) > 0
        ):
            category, reason = "director_tool_execution", "director_failures_or_blocked"
        else:
            category, reason = "runtime_environment", f"chain_state.{record.get('chain_state') or 'unknown'}"
    elif _check_failures(record):
        first_check = _check_failures(record)[0]
        reason = str(first_check.get("check") or "check_failed")
        category = (
            "runtime_environment" if _check_failure_is_runtime_environment(first_check) else "target_project_baseline"
        )
    else:
        failed_gates = _gate_failures(record)
        category = "unknown"
        reason = str(failed_gates[0].get("gate") if failed_gates else "unclassified_failure")

    for gate in _gate_failures(record):
        reasons.append(f"gate:{gate.get('gate')}={gate.get('detail')}")
    for check in _check_failures(record):
        reasons.append(f"check:{check.get('check')}={check.get('detail')}")
    return {
        "ok": False,
        "category": category,
        "root_cause_signature": _category_signature(category, reason),
        "reasons": reasons,
        "evidence": [item for item in evidence if item],
    }


def apply_factory_bench_failure_taxonomy(record: dict[str, Any]) -> dict[str, Any]:
    """Classify a bench record and expose stable top-level attribution fields."""
    taxonomy = classify_factory_bench_failure(record)
    record["failure_taxonomy"] = taxonomy
    record["failure_category"] = str(taxonomy.get("category") or "")
    record["root_cause_signature"] = str(taxonomy.get("root_cause_signature") or "")
    reasons = taxonomy.get("reasons")
    evidence = taxonomy.get("evidence")
    record["failure_reasons"] = list(reasons) if isinstance(reasons, list) else []
    record["failure_evidence"] = list(evidence) if isinstance(evidence, list) else []
    # OpenCode external audits are a main-Agent-only collaboration mechanism.
    # They must never become Polaris/Factory machine-readable platform state.
    record.pop("opencode_audit", None)
    record["goal_audit"] = aggregate_goal_audit([record])
    return taxonomy


def aggregate_goal_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    real_passed = sum(
        1 for record in records if isinstance(record.get("real_run_gate"), dict) and record["real_run_gate"].get("ok")
    )
    ledger_projected = sum(1 for record in records if _run_ledger_projection_integrity_available(record))
    route_passed = sum(
        1
        for record in records
        if isinstance(record.get("llm_route_audit"), dict) and record["llm_route_audit"].get("ok")
    )
    categories: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    for record in records:
        taxonomy = record.get("failure_taxonomy")
        if not isinstance(taxonomy, dict) or taxonomy.get("ok"):
            continue
        category = str(taxonomy.get("category") or "unknown")
        signature = str(taxonomy.get("root_cause_signature") or f"{category}:unknown")
        categories[category] += 1
        signatures[signature] += 1
    return {
        "total": total,
        "real_run_gate": {"passed": real_passed, "total": total},
        "run_ledger": {"projected": ledger_projected, "total": total, "missing": total - ledger_projected},
        "llm_route_audit": {"passed": route_passed, "total": total},
        "failure_categories": dict(sorted(categories.items())),
        "root_cause_signatures": dict(sorted(signatures.items())),
    }


__all__ = [
    "aggregate_goal_audit",
    "apply_factory_bench_failure_taxonomy",
    "build_llm_route_audit",
    "build_real_run_gate",
    "classify_factory_bench_failure",
    "collect_llm_events",
    "resolve_expected_llm_bindings",
]


def _go_version_of(binary: str) -> tuple[int, ...]:
    """Parse the Go version tuple from ``go version`` output (e.g. ``(1, 23, 8)``)."""
    try:
        result = subprocess.run([binary, "version"], capture_output=True, text=True, timeout=5)
        # "go version go1.23.8 linux/amd64" → (1, 23, 8)
        import re as _re

        m = _re.search(r"go(\d+(?:\.\d+)*)", result.stdout)
        if m:
            return tuple(int(x) for x in m.group(1).split("."))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return (0,)


def _resolve_go_binary() -> str | None:
    """Locate the ``go`` binary, preferring versions >= 1.23 when available.

    Go 1.23 made the ``iter`` package a first-class citizen; earlier versions
    gate it behind ``GOEXPERIMENT=rangefunc`` which breaks ``go test`` on code
    that transitively imports ``bufio``/``bytes``.
    """
    home = _Path(_os.path.expanduser("~"))
    # Ordered: prefer explicit newer installs, then PATH, then common fallbacks.
    candidates: list[str] = []
    for p in (
        home / ".local" / "go123" / "bin" / "go",
        home / ".local" / "go124" / "bin" / "go",
        home / ".local" / "go125" / "bin" / "go",
    ):
        if p.is_file() and _os.access(p, _os.X_OK):
            candidates.append(str(p))
    path_go = shutil.which("go")
    if path_go:
        candidates.append(path_go)
    for p in (home / ".local" / "go" / "bin" / "go", home / "go" / "bin" / "go"):
        if p.is_file() and _os.access(p, _os.X_OK):
            s = str(p)
            if s not in candidates:
                candidates.append(s)
    if not candidates:
        return None
    # Prefer the first binary with Go >= 1.23; fall back to whatever is found.
    for binary in candidates:
        if _go_version_of(binary) >= (1, 23):
            return binary
    return candidates[0]
