"""Bounded local-page observations over the existing physically owned process port.

The caller supplies KernelOne read-only/no-network containment and owns source
bindings, cancellation, durable observations and role identity. This module
never journals, installs dependencies, selects executables from model input or
starts a persistent browser service.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    ImageObservation,
    PermissionScope,
    ToolObservation,
)
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs.runtime import KernelFileSystem

_MAX_IMAGES_BYTES = 1024 * 1024
_MAX_WIRE_BYTES = 2 * 1024 * 1024
_SYSTEM_ROOTS = tuple(Path(value).resolve() for value in ("/usr", "/bin", "/sbin", "/lib", "/lib64"))

# This is trusted adapter code. -I prevents a target-local json.py/playwright.py
# or ambient PYTHONPATH from running before trusted imports/path preparation.
_BROWSER_PROGRAM = r"""
import base64
import hashlib
import json
import sys
import time
from pathlib import Path

settings = json.loads(sys.argv[1])
workspace = Path(settings['workspace'])
sys.path[:] = [path for path in sys.path if path and not Path(path).resolve().is_relative_to(workspace)]
for path in reversed(settings['python_paths']):
    sys.path.insert(0, path)

from playwright.sync_api import sync_playwright

console = []
page_errors = []
blocked_requests = []
images = []
context = None

try:
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                '/tmp/polaris-browser-profile',
                executable_path=settings['executable'],
                headless=True,
                viewport=settings['viewport'],
                device_scale_factor=1,
                accept_downloads=False,
                service_workers='block',
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-extensions', '--disable-background-networking'],
            )
            def route_request(route):
                url = route.request.url
                if url.startswith(('http:', 'https:', 'ws:', 'wss:', 'ftp:')):
                    if len(blocked_requests) < 20:
                        blocked_requests.append(url[:500])
                    route.abort()
                else:
                    route.continue_()
            context.route('**/*', route_request)
            page = context.pages[0]
            page.set_default_timeout(2000)
            page.on('console', lambda message: console.append(message.text[:500]) if len(console) < 20 else None)
            page.on('pageerror', lambda error: page_errors.append(str(error)[:500]) if len(page_errors) < 20 else None)
            page.goto(Path(settings['entry']).as_uri(), wait_until='load', timeout=4000)
            for action in settings['actions']:
                locator = page.locator(action['selector'])
                if action['type'] == 'click':
                    locator.click()
                elif action['type'] == 'fill':
                    locator.fill(action['value'])
                elif action['type'] == 'select_option':
                    locator.select_option(value=action['value'])
                else:
                    locator.press(action['key'])
                if action['wait_ms']:
                    page.wait_for_timeout(action['wait_ms'])
            started = time.monotonic()
            total = 0
            for number, offset in enumerate(settings['frame_times_ms']):
                remaining = offset - (time.monotonic() - started) * 1000
                if remaining > 0:
                    page.wait_for_timeout(remaining)
                captured_ms = round((time.monotonic() - started) * 1000)
                data = page.screenshot(type='jpeg', quality=75, full_page=False, timeout=3000)
                total += len(data)
                if total > 1024 * 1024:
                    raise ValueError('browser images exceed the 1 MiB binary limit')
                images.append({
                    'media_type': 'image/jpeg',
                    'data_base64': base64.b64encode(data).decode('ascii'),
                    'width': settings['viewport']['width'],
                    'height': settings['viewport']['height'],
                    'sha256': hashlib.sha256(data).hexdigest(),
                    'label': 'frame %d at %d ms (requested %d ms)' % (number + 1, captured_ms, offset),
                })
            body = page.locator('body')
            summary = {
                'title': page.title()[:200],
                'body_text': body.inner_text()[:2000] if body.count() else '',
                'buttons': [text[:80] for text in page.get_by_role('button').all_text_contents()[:8]],
            }
            # Read only public properties of rendered standard form controls.
            # Prefix limits describe observations, never truncate supplied actions.
            summary.update(page.locator('input, textarea, select').evaluate_all(r'''
                elements => {
                    const clip = (value, limit) => {
                        const original = String(value ?? '');
                        let text = original.slice(0, limit);
                        if (text.length) {
                            const last = text.charCodeAt(text.length - 1);
                            if (last >= 0xD800 && last <= 0xDBFF) text = text.slice(0, -1);
                        }
                        return {text, length_utf16: original.length, truncated: text.length < original.length};
                    };
                    const rendered = elements.filter(element => element.type !== 'hidden'
                        && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden');
                    return {
                        controls_total: rendered.length,
                        controls_truncated: rendered.length > 32,
                        control_limits: {count: 32, value_utf16_units: 512, identity_utf16_units: 128, selected_options: 8},
                        controls: rendered.slice(0, 32).map(element => {
                            const id = clip(element.id, 128), name = clip(element.name, 128);
                            const observed = !['password', 'file'].includes(element.type);
                            const value = observed ? clip(element.value, 512) : null;
                            const selected = element.tagName === 'SELECT' ? Array.from(element.selectedOptions) : [];
                            return {
                                tag: element.tagName.toLowerCase(), type: element.type,
                                id: id.text, id_truncated: id.truncated,
                                name: name.text, name_truncated: name.truncated,
                                value_observed: observed, value: value ? value.text : null,
                                value_length_utf16: value ? value.length_utf16 : null,
                                value_truncated: value ? value.truncated : false,
                                checked: ['checkbox', 'radio'].includes(element.type) ? element.checked : null,
                                disabled: element.disabled, read_only: Boolean(element.readOnly),
                                selected_values: selected.slice(0, 8).map(option => clip(option.value, 512)),
                                selected_values_total: selected.length, selected_values_truncated: selected.length > 8,
                            };
                        }),
                    };
                }
            '''))
            result = {
                'images': images,
                'entry': settings['relative_entry'],
                'viewport': settings['viewport'],
                'frame_times_ms': settings['frame_times_ms'],
                'actions': settings['actions'],
                'browser_version': context.browser.version if context.browser else 'unknown',
                'network_allowed': False,
                'console': console,
                'page_errors': page_errors,
                'blocked_requests': blocked_requests,
                'dom': summary,
            }
        finally:
            if context is not None:
                context.close()
    print(json.dumps(result, ensure_ascii=False))
except Exception as error:
    print(json.dumps({'error': type(error).__name__ + ': ' + str(error)[:2000]}, ensure_ascii=False))
    raise SystemExit(2)
"""


def _text(value: object, *, label: str, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > maximum:
        raise ValueError(f"{label} must be nonempty text of at most {maximum} characters without NUL")
    return value


def _action_value(value: object) -> str:
    """Preserve literal DOM text, including empty values and surrounding spaces."""
    if not isinstance(value, str) or "\0" in value:
        raise ValueError("fill/select_option value must be a string without NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("fill/select_option value must be valid UTF-8 text") from error
    return value


def _integer(value: object, *, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _settings(arguments: dict[str, Any], *, workspace: Path, scope: PermissionScope) -> dict[str, Any]:
    if scope.browser is None:
        raise ValueError("browser observation unavailable: no host browser configuration")
    if "browser_observe" not in scope.enabled_tools:
        raise ValueError("browser observation unavailable: tool is not authorized")
    if not isinstance(arguments, dict) or arguments.keys() - {"entry", "viewport", "frame_times_ms", "actions"}:
        raise ValueError("unsupported browser observation arguments")
    entry = _text(arguments.get("entry", "index.html"), label="entry")
    relative = Path(entry)
    if relative.is_absolute() or ".." in relative.parts or ":" in entry or "\\" in entry:
        raise ValueError("browser entry must be a workspace-relative local HTML or SVG path")
    filesystem = KernelFileSystem(str(workspace), LocalFileSystemAdapter(), read_only_paths=True)
    target = filesystem.resolve_workspace_path(entry)
    if target.is_relative_to(workspace / ".polaris") or target.suffix.lower() not in {".html", ".htm", ".svg"}:
        raise ValueError("browser entry must be an authorized HTML or SVG source outside the control root")
    if not target.is_file() or not any(target.is_relative_to(Path(root).resolve()) for root in scope.read_roots):
        raise ValueError("browser entry is unavailable or outside read permission scope")

    viewport = arguments.get("viewport", {"width": 1000, "height": 750})
    if not isinstance(viewport, dict) or viewport.keys() != {"width", "height"}:
        raise ValueError("viewport must contain only width and height")
    viewport = {
        "width": _integer(viewport["width"], label="viewport.width", minimum=320, maximum=1600),
        "height": _integer(viewport["height"], label="viewport.height", minimum=240, maximum=1200),
    }
    frames = arguments.get("frame_times_ms", [0, 400])
    if not isinstance(frames, list) or not 1 <= len(frames) <= 4:
        raise ValueError("frame_times_ms must contain between 1 and 4 frame offsets")
    frames = [_integer(value, label="frame offset", minimum=0, maximum=10000) for value in frames]
    if frames != sorted(frames):
        raise ValueError("frame_times_ms must be ordered absolute offsets after the actions")
    supplied_actions = arguments.get("actions", [])
    if not isinstance(supplied_actions, list):
        raise ValueError("actions must be a list")
    actions = []
    for action in supplied_actions:
        if not isinstance(action, dict) or action.keys() - {"type", "selector", "key", "value", "wait_ms"}:
            raise ValueError("unsupported browser action fields")
        kind = action.get("type")
        if kind not in {"click", "keypress", "fill", "select_option"}:
            raise ValueError("browser action type must be click, keypress, fill or select_option")
        normalized = {
            "type": kind,
            "selector": _text(action.get("selector"), label="action selector", maximum=512),
            "wait_ms": _integer(action.get("wait_ms", 0), label="action wait_ms", minimum=0, maximum=10000),
        }
        if kind == "keypress":
            if "value" in action:
                raise ValueError("keypress actions do not accept value")
            normalized["key"] = _text(action.get("key"), label="action key", maximum=64)
        elif kind in {"fill", "select_option"}:
            if "key" in action:
                raise ValueError("fill/select_option actions do not accept key")
            normalized["value"] = _action_value(action.get("value"))
        elif "key" in action or "value" in action:
            raise ValueError("click actions do not accept key or value")
        actions.append(normalized)
    if sum(action["wait_ms"] for action in actions) + frames[-1] > 10000:
        raise ValueError("browser action waits and frame offsets exceed the 10 second timing budget")

    reads = tuple(Path(root).resolve(strict=True) for root in scope.read_roots)
    writes = tuple(Path(root).resolve(strict=True) for root in scope.write_roots)

    def trusted_path(raw: str, *, directory: bool = False, search_path: bool = False) -> Path:
        path = Path(_text(raw, label="host browser path", maximum=4096)).resolve(strict=True)
        if (
            directory != path.is_dir()
            or path.is_relative_to(workspace)
            or workspace.is_relative_to(path)
            or any(path.is_relative_to(root) or root.is_relative_to(path) for root in writes)
        ):
            raise ValueError("browser runtime paths must be read-only and cannot overlap original write authority")
        covered = any(path.is_relative_to(root) for root in (*_SYSTEM_ROOTS, *reads))
        if search_path:
            # Specific package mounts create their parent directories without
            # granting ambient read access to every sibling package.
            covered = covered or any(root.is_relative_to(path) for root in reads)
        if not covered:
            raise ValueError("browser runtime path is outside explicit host read grants or system toolchain")
        if not directory and not os.access(path, os.X_OK):
            raise ValueError("browser runtime executable is unavailable")
        return path

    executable = trusted_path(scope.browser.executable)
    python = trusted_path(scope.browser.python_executable)
    paths = [str(trusted_path(path, directory=True, search_path=True)) for path in scope.browser.python_paths]
    return {
        "workspace": str(workspace),
        "entry": str(target),
        "relative_entry": target.relative_to(workspace).as_posix(),
        "viewport": viewport,
        "frame_times_ms": frames,
        "actions": actions,
        "executable": str(executable),
        "python_executable": str(python),
        "python_paths": paths,
    }


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Check the returned JPEG structure without optional imaging dependencies."""
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("browser returned malformed JPEG bytes")
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            raise ValueError("browser returned malformed JPEG marker")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if position + 2 > len(data):
            break
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        length = int.from_bytes(data[position : position + 2], "big")
        if length < 2 or position + length > len(data):
            raise ValueError("browser returned invalid JPEG segment")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if length < 8:
                break
            height = int.from_bytes(data[position + 3 : position + 5], "big")
            width = int.from_bytes(data[position + 5 : position + 7], "big")
            return width, height
        position += length
    raise ValueError("browser returned JPEG without dimensions")


async def capture_browser_observation(
    call_id: str,
    arguments: dict[str, Any],
    *,
    workspace: Path,
    scope: PermissionScope,
    run_process: Callable[[list[str], float], Awaitable[subprocess.CompletedProcess[str]]],
) -> ToolObservation:
    """Capture actual local pixels; the caller retains the physical effect owner.

    Cancellation and uncertain drain propagate to that existing owner. A normal
    child failure or invalid input is an explicit negative observation. A caller
    must never invoke this port using an unsandboxed process implementation.
    """
    try:
        settings = _settings(arguments, workspace=workspace.resolve(strict=True), scope=scope)
        completed = await run_process(
            [
                settings["python_executable"],
                "-I",
                "-B",
                "-c",
                _BROWSER_PROGRAM,
                json.dumps(settings, ensure_ascii=False),
            ],
            30.0,
        )
        if completed.returncode != 0:
            detail = completed.stdout[-4000:] + "\n" + completed.stderr[-2000:]
            return ToolObservation(call_id, "browser_observe", False, detail, completed.returncode, False)
        if len(completed.stdout.encode("utf-8")) > _MAX_WIRE_BYTES:
            raise ValueError("browser result exceeds the bounded wire output limit")
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("images"), list):
            raise ValueError("browser did not return an image observation payload")
        supplied_images = payload.pop("images")
        if len(supplied_images) != len(settings["frame_times_ms"]):
            raise ValueError("browser returned an unexpected number of frames")
        images: list[ImageObservation] = []
        total = 0
        for image in supplied_images:
            if not isinstance(image, dict) or image.get("media_type") != "image/jpeg":
                raise ValueError("browser returned an unsupported image format")
            data = base64.b64decode(image["data_base64"], validate=True)
            total += len(data)
            if total > _MAX_IMAGES_BYTES:
                raise ValueError("browser images exceed the 1 MiB binary limit")
            if hashlib.sha256(data).hexdigest() != image.get("sha256"):
                raise ValueError("browser image digest mismatch")
            dimensions = _jpeg_dimensions(data)
            if dimensions != (settings["viewport"]["width"], settings["viewport"]["height"]):
                raise ValueError("browser image dimensions do not match the requested viewport")
            if (image.get("width"), image.get("height")) != dimensions:
                raise ValueError("browser image metadata dimensions are inconsistent")
            images.append(ImageObservation(**image))
        return ToolObservation(
            call_id,
            "browser_observe",
            True,
            json.dumps(payload, ensure_ascii=False),
            completed.returncode,
            False,
            images=tuple(images),
        )
    except (ValueError, OSError, UnicodeError, PathSecurityError, binascii.Error, KeyError, TypeError) as error:
        return ToolObservation(call_id, "browser_observe", False, f"{type(error).__name__}: {error}", None, False)


__all__ = ["capture_browser_observation"]
