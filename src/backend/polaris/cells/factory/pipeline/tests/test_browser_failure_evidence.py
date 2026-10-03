"""Actual browser failures remain failed observations, not escaped runner errors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.bench_gates import _core


@pytest.mark.parametrize("scene", ["moving_blank", "stable_blank", "painted"])
def test_real_canvas_probe_preserves_failure_and_success(scene: str, tmp_path: Path) -> None:
    pytest.importorskip("playwright.sync_api")
    script = {
        "moving_blank": "let n=0;function move(){document.querySelector('canvas').style.left=((n++%2)*30)+'px';requestAnimationFrame(move)}move()",
        "stable_blank": "",
        "painted": "const c=document.querySelector('canvas').getContext('2d');c.fillStyle='red';c.fillRect(0,0,80,80)",
    }[scene]
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><body><canvas width='80' height='80' style='position:absolute'></canvas>"
        f"<script>{script}</script></body></html>",
        encoding="utf-8",
    )
    # Reproduce the stale graphical-session hint even on a healthy test host.
    # A genuinely headless probe must not require that X server.
    backend_root = Path(__file__).resolve().parents[5]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(backend_root)
    environment["DISPLAY"] = "127.0.0.1:0"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys; from pathlib import Path; "
            "from polaris.cells.factory.pipeline.internal.bench_gates import _core; "
            "print(json.dumps(_core._smoke_static_web(Path(sys.argv[1]),'index.html',timeout_s=10)))",
            str(tmp_path),
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=True,
        cwd=backend_root.parents[1],
        env=environment,
    )
    result = json.loads(probe.stdout.splitlines()[-1])
    assert result["kind"] == "web_playwright"
    assert result["ok"] is (scene == "painted"), result["detail"]
    if scene == "moving_blank":
        evidence = " ".join(result.get("canvas_screenshot_errors", [])) + " " + result["detail"]
        assert "Timeout 2000ms exceeded" in evidence
        assert result.get("canvas_screenshot_non_blank", False) is False


def test_available_browser_runtime_error_never_falls_back_to_http_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = pytest.importorskip("playwright.sync_api")

    def failed_browser(*args: object, **kwargs: object) -> dict:
        raise api.Error("fixture browser navigation failed")

    monkeypatch.setattr(_core, "_smoke_static_web_playwright", failed_browser)
    result = _core._smoke_static_web(tmp_path, "index.html", timeout_s=10)
    assert result["kind"] == "web_playwright"
    assert result["ok"] is False
    assert "fixture browser navigation failed" in result["detail"]
