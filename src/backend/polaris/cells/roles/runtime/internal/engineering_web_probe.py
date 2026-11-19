"""Deterministic host-side web deliverable probe.

The platform's acceptance must not rely on a weak model grading its own
artifact. This probe renders the entry page headlessly and measures objective
quality signals a model cannot self-certify: console/page errors, canvas /
WebGL presence, animation motion (two-frame pixel diff), and interactive
button behavior (click → DOM mutation). Pure stdlib + playwright + PIL in a
subprocess, same trusted-adapter pattern as engineering_browser.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Trusted adapter code. -I prevents a target-local module shadowing playwright.
_PROBE_PROGRAM = r"""
import base64
import io
import json
import sys

from playwright.sync_api import sync_playwright

settings = json.loads(sys.argv[1])
result = {"console_errors": [], "page_errors": [], "buttons": [], "metrics": {}}
try:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            settings["profile_dir"],
            headless=True,
            viewport={"width": settings["width"], "height": settings["height"]},
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.on("console", lambda message: result["console_errors"].append(message.text[:300]) if message.type == "error" else None)
        page.on("pageerror", lambda error: result["page_errors"].append(str(error)[:300]))
        page.goto(settings["url"], wait_until="load", timeout=15000)
        page.wait_for_timeout(settings["settle_ms"])
        result["metrics"]["title"] = page.title()
        result["metrics"]["canvas_count"] = page.evaluate("document.querySelectorAll('canvas').length")
        result["metrics"]["webgl"] = page.evaluate(
            "(() => { const c = document.querySelector('canvas'); if (!c) return false;"
            " const gl = c.getContext('webgl2') || c.getContext('webgl'); return !!gl; })()"
        )
        shot_a = page.screenshot(type="jpeg", quality=70)
        page.wait_for_timeout(settings["motion_ms"])
        shot_b = page.screenshot(type="jpeg", quality=70)
        from PIL import Image
        a = Image.open(io.BytesIO(shot_a)).convert("L").resize((64, 40))
        b = Image.open(io.BytesIO(shot_b)).convert("L").resize((64, 40))
        pa, pb = list(a.getdata()), list(b.getdata())
        changed = sum(1 for x, y in zip(pa, pb) if abs(x - y) > 12)
        result["metrics"]["changed_pixel_ratio"] = round(changed / len(pa), 4)
        result["metrics"]["animated"] = changed / len(pa) > 0.005
        for index in range(page.evaluate("document.querySelectorAll('button').length")):
            button = page.locator("button").nth(index)
            label = (button.inner_text() or "").strip()[:60]
            page.evaluate(
                "(() => { window.__probe_mutations = 0;"
                " window.__probe_observer = new MutationObserver(() => { window.__probe_mutations += 1; });"
                " window.__probe_observer.observe(document.body, {childList: true, subtree: true, attributes: true}); })()"
            )
            try:
                button.click(timeout=1000)
            except Exception:
                result["buttons"].append({"label": label, "clicked": False, "state_changed": False})
                page.evaluate("window.__probe_observer && window.__probe_observer.disconnect()")
                continue
            page.wait_for_timeout(450)
            count = page.evaluate("window.__probe_mutations || 0")
            page.evaluate("window.__probe_observer && window.__probe_observer.disconnect()")
            result["buttons"].append({"label": label, "clicked": True, "state_changed": count > 0})
        context.close()
except Exception as error:
    result["probe_error"] = f"{type(error).__name__}: {error}"[:400]
print(json.dumps(result, ensure_ascii=False))
"""

_CANDIDATE_ENTRIES = ("index.html", "dist/index.html", "public/index.html", "build/index.html")


@dataclass(frozen=True)
class WebProbeResult:
    entry: str
    ran: bool
    ok: bool
    console_errors: tuple[str, ...] = ()
    page_errors: tuple[str, ...] = ()
    canvas_count: int = 0
    webgl: bool = False
    animated: bool = False
    changed_pixel_ratio: float = 0.0
    buttons: tuple[dict[str, Any], ...] = ()
    title: str = ""
    probe_error: str = ""
    details: str = ""


def find_web_entry(workspace: Path) -> str | None:
    for candidate in _CANDIDATE_ENTRIES:
        if (workspace / candidate).is_file():
            return candidate
    return None


async def probe_web_deliverable(
    workspace: Path,
    *,
    entry: str | None = None,
    python_executable: str | None = None,
    settle_ms: int = 2500,
    motion_ms: int = 700,
    timeout_seconds: float = 60.0,
) -> WebProbeResult:
    """Render the entry page headlessly and return objective quality signals."""
    selected = entry or find_web_entry(workspace)
    if selected is None:
        return WebProbeResult(entry="", ran=False, ok=False, details="no web entry found")
    settings = {
        "url": (workspace / selected).resolve().as_uri(),
        "profile_dir": str(Path(workspace / ".polaris" / "runtime" / "probe-profile").resolve()),
        "width": 1280,
        "height": 800,
        "settle_ms": settle_ms,
        "motion_ms": motion_ms,
    }
    try:
        completed = await asyncio.wait_for(
            asyncio.to_thread(
                subprocess.run,
                [
                    python_executable or sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    _PROBE_PROGRAM,
                    json.dumps(settings, ensure_ascii=False),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            ),
            timeout=timeout_seconds + 10,
        )
    except (OSError, asyncio.TimeoutError) as error:
        return WebProbeResult(entry=selected, ran=True, ok=False, probe_error=str(error)[:300])
    if completed.returncode != 0:
        detail = (completed.stdout[-2000:] + "\n" + completed.stderr[-2000:])[:3000]
        return WebProbeResult(entry=selected, ran=True, ok=False, probe_error=detail)
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return WebProbeResult(entry=selected, ran=True, ok=False, probe_error="probe returned invalid JSON")
    metrics = payload.get("metrics") or {}
    console_errors = tuple(str(item) for item in payload.get("console_errors") or [])
    page_errors = tuple(str(item) for item in payload.get("page_errors") or [])
    buttons = tuple(item for item in payload.get("buttons") or [] if isinstance(item, dict))
    canvas_count = int(metrics.get("canvas_count") or 0)
    webgl = bool(metrics.get("webgl"))
    animated = bool(metrics.get("animated"))
    changed = float(metrics.get("changed_pixel_ratio") or 0.0)
    ok = (
        not payload.get("probe_error")
        and not console_errors
        and not page_errors
        and canvas_count > 0
        and animated
    )
    details = (
        f"entry={selected} title={metrics.get('title') or '-'} canvas={canvas_count} webgl={webgl} "
        f"animated={animated}({changed:.3f}) buttons={len(buttons)} "
        f"console_errors={len(console_errors)} page_errors={len(page_errors)}"
    )
    return WebProbeResult(
        entry=selected,
        ran=True,
        ok=ok,
        console_errors=console_errors,
        page_errors=page_errors,
        canvas_count=canvas_count,
        webgl=webgl,
        animated=animated,
        changed_pixel_ratio=changed,
        buttons=buttons,
        title=str(metrics.get("title") or ""),
        probe_error=str(payload.get("probe_error") or ""),
        details=details,
    )


def probe_feedback(result: WebProbeResult) -> str:
    """Model-facing feedback distilled from a failing probe."""
    lines = ["Host web probe failed — fix the deliverable, then re-run verification."]
    if result.probe_error:
        lines.append(f"probe error: {result.probe_error}")
    for error in result.console_errors[:3]:
        lines.append(f"console error: {error}")
    for error in result.page_errors[:3]:
        lines.append(f"page error: {error}")
    if result.canvas_count == 0:
        lines.append("no <canvas> element found; the scene must render into a canvas.")
    if result.canvas_count > 0 and not result.animated:
        lines.append(f"no motion detected between frames (changed ratio {result.changed_pixel_ratio:.3f}); the animation loop is not visibly running.")
    lines.append(f"signals: {result.details}")
    return "\n".join(lines)
