"""Internal helpers: constants, CLI/language smoke, command utilities."""

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

from ..native_validation_sandbox import (
    NativeValidationContractError,
    NativeValidationSandboxError,
    cargo_native_test_count,
    is_cargo_test_command,
    sandboxed_cargo_test_command,
)

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

CANONICAL_BENCH_PROJECTION_SCHEMA = "factory_bench.canonical_projection.v1"
CANONICAL_BENCH_PROJECTION_SOURCE = "canonical_projection"
LEGACY_BENCH_ARTIFACT_SOURCE = "legacy_artifact"
_FINAL_QA_GATE_NAMES = frozenset({"qa_verdict"})
_TASK_RUNTIME_FACT_SOURCE = "task_runtime.execution_fact"


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
        # Cargo test is the native Rust verification boundary: it compiles the
        # project and executes unit/integration tests in one physical gate.
        return [cargo, "test", "--quiet"]
    rustc = shutil.which("rustc")
    if not rustc:
        return []
    root = next(
        (rel for rel in ("src/main.rs", "main.rs", "src/lib.rs", "lib.rs") if rel in rust_files),
        rust_files[0] if rust_files else "",
    )
    return [rustc, "--edition=2021", "--emit=metadata", root] if root else []


def _run_sandboxed_cargo_test(
    command: list[str],
    workspace: Path,
    *,
    timeout_s: int,
) -> dict[str, Any]:
    try:
        with sandboxed_cargo_test_command(
            workspace=workspace,
            command=command,
        ) as sandbox:
            result = _run_command(
                sandbox.command,
                workspace,
                timeout_s=timeout_s,
            )
    except NativeValidationContractError as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": None,
            "duration_s": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "error": f"native_validation_contract_invalid: {exc}",
            "sandboxed": False,
            "native_test_count": 0,
        }
    except NativeValidationSandboxError as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": None,
            "duration_s": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "error": f"native_validation_sandbox_unavailable: {exc}",
            "sandboxed": False,
            "native_test_count": 0,
        }

    native_test_count = cargo_native_test_count(result.get("stdout_tail"))
    result["command"] = command
    result["native_test_count"] = native_test_count
    result["sandbox_backend"] = sandbox.backend
    result["sandboxed"] = True
    if result.get("returncode") == 0 and native_test_count < 1:
        result["ok"] = False
        result["error"] = "cargo_test_zero_tests"
    return result


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
        if is_cargo_test_command(command):
            cmd = _run_sandboxed_cargo_test(
                command,
                workspace,
                timeout_s=max(10, int(timeout_s)),
            )
            cmd["phase"] = "build_test_lint"
            if cmd.get("returncode") == 0 and int(cmd.get("native_test_count") or 0) < 1:
                return False, "cargo test executed zero tests", [cmd]
            return (
                bool(cmd.get("ok")),
                "cargo test passed" if cmd.get("ok") else "cargo test failed",
                [cmd],
            )
        with tempfile.TemporaryDirectory(prefix="polaris-factory-rustc-") as out_dir:
            rustc_command = [*command, "--out-dir", out_dir]
            cmd = _run_command(rustc_command, workspace, timeout_s=max(10, int(timeout_s)))
        cmd["phase"] = "build_test_lint"
        return (
            bool(cmd.get("ok")),
            "rustc compile passed" if cmd.get("ok") else "rustc compile failed",
            [cmd],
        )

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
