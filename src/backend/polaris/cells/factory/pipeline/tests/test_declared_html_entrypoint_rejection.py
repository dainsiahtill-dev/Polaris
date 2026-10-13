"""Declared Web entrypoints keep the existing real HTTP failure checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.bench_gates import _core


@pytest.mark.parametrize(
    ("content", "diagnostic"),
    [
        ("This is plain text, not a Web document.", "did not look like HTML"),
        ("<html><body><script src='missing.js'></script></body></html>", "missing local resources"),
    ],
)
def test_actual_declared_web_smoke_rejects_invalid_or_missing_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str, diagnostic: str
) -> None:
    (tmp_path / "index.html").write_text(content, encoding="utf-8")

    def optional_browser_unavailable(*_args: object, **_kwargs: object) -> None:
        raise ImportError("Use the existing actual HTTP fallback for this component test")

    monkeypatch.setattr(_core, "_smoke_static_web_playwright", optional_browser_unavailable)
    result = _core._smoke_static_web(tmp_path, "index.html", timeout_s=5)
    assert result["kind"] == "web_static"
    assert result["ok"] is False
    assert diagnostic in result["detail"]
