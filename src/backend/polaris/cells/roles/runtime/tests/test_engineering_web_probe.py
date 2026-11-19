"""Unit tests for the deterministic web deliverable probe."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_web_probe import (
    WebProbeResult,
    find_web_entry,
    probe_feedback,
)


def test_find_web_entry_prefers_index_html(tmp_path: Path) -> None:
    assert find_web_entry(tmp_path) is None
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    assert find_web_entry(tmp_path) == "index.html"


def test_find_web_entry_falls_back_to_dist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    assert find_web_entry(tmp_path) == "dist/index.html"


def test_probe_feedback_lists_failures() -> None:
    result = WebProbeResult(
        entry="index.html",
        ran=True,
        ok=False,
        console_errors=("TypeError: x is not a function",),
        page_errors=("Uncaught ReferenceError",),
        canvas_count=1,
        webgl=True,
        animated=False,
        changed_pixel_ratio=0.0001,
        details="entry=index.html canvas=1 animated=False(0.000)",
    )
    feedback = probe_feedback(result)
    assert "TypeError: x is not a function" in feedback
    assert "Uncaught ReferenceError" in feedback
    assert "no motion detected" in feedback


def test_probe_feedback_clean_result_has_no_motion_line() -> None:
    result = WebProbeResult(entry="index.html", ran=True, ok=True, canvas_count=1, animated=True)
    feedback = probe_feedback(result)
    assert "no motion" not in feedback
