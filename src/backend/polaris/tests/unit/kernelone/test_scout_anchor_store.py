"""Tests for persistent scout localization anchors (Phase-2 A7)."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.context.scout_anchor_store import (
    format_anchor_card,
    load_scout_anchors,
    record_scout_anchors,
)


def _finding(path: str, confidence: float, line: int | None = 10, symbol: str | None = "f") -> dict:
    return {"path": path, "line": line, "symbol": symbol, "confidence": confidence, "snippet": "..."}


class TestRecordAndLoad:
    def test_roundtrip(self, tmp_path: Path) -> None:
        count = record_scout_anchors(str(tmp_path), "where is X", [_finding("a/b.py", 0.9)])
        assert count == 1
        anchors = load_scout_anchors(str(tmp_path))
        assert anchors[0]["path"] == "a/b.py"
        assert anchors[0]["confidence"] == 0.9
        assert anchors[0]["query"] == "where is X"

    def test_dedup_by_path_keeps_highest_confidence(self, tmp_path: Path) -> None:
        record_scout_anchors(str(tmp_path), "q1", [_finding("a.py", 0.5)])
        record_scout_anchors(str(tmp_path), "q2", [_finding("a.py", 0.8), _finding("b.py", 0.4)])
        anchors = load_scout_anchors(str(tmp_path))
        assert [a["path"] for a in anchors] == ["a.py", "b.py"]
        assert anchors[0]["confidence"] == 0.8

    def test_cap_keeps_top_confidence(self, tmp_path: Path) -> None:
        findings = [_finding(f"m{i}.py", 0.3 + i * 0.05) for i in range(12)]
        record_scout_anchors(str(tmp_path), "q", findings)
        anchors = load_scout_anchors(str(tmp_path))
        assert len(anchors) == 8
        assert anchors[0]["confidence"] >= anchors[-1]["confidence"]

    def test_low_confidence_filtered(self, tmp_path: Path) -> None:
        record_scout_anchors(str(tmp_path), "q", [_finding("weak.py", 0.05)])
        assert load_scout_anchors(str(tmp_path)) == []

    def test_corrupt_file_fails_soft(self, tmp_path: Path) -> None:
        target = tmp_path / ".polaris" / "runtime" / "scout_anchors.json"
        target.parent.mkdir(parents=True)
        target.write_text("not json", encoding="utf-8")
        assert load_scout_anchors(str(tmp_path)) == []
        assert record_scout_anchors(str(tmp_path), "q", [_finding("a.py", 0.9)]) == 1


class TestAnchorCard:
    def test_card_format(self, tmp_path: Path) -> None:
        record_scout_anchors(
            str(tmp_path),
            "ExpressionWrapper empty Q",
            [_finding("django/db/models/sql/compiler.py", 0.85, line=465, symbol="SQLCompiler")],
        )
        card = format_anchor_card(load_scout_anchors(str(tmp_path)))
        assert card is not None
        assert "【侦察锚点】" in card
        assert "django/db/models/sql/compiler.py:465 (SQLCompiler) 置信度 0.85" in card
        assert "不要在压力下放弃" in card

    def test_empty_anchors_no_card(self) -> None:
        assert format_anchor_card([]) is None
