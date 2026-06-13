"""Cross-file interface ledger (组合律 across parents).

Live I3-r14: two parents that legitimately shared index.html invented colliding
identifiers (id=game vs id=gameCanvas) and shipped a non-running product. The
ledger freezes the first parent's declared identifiers so siblings reuse them.
"""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.interface_ledger import (
    read_declared_interfaces,
    record_declared_interfaces,
    render_assume_contract,
)


def _steps() -> list[dict[str, object]]:
    return [
        {
            "step_id": "PM-0001-1-S1",
            "target_file": "index.html",
            "interface_names": ["game", "score", "lives", "message", "hud"],
            "signatures": [],
        },
        {
            "step_id": "PM-0001-1-S3",
            "target_file": "main.js",
            "interface_names": ["update", "draw", "loop"],
            "signatures": ["function update(dt)", "function draw(ctx)"],
        },
    ]


class TestRecordAndRead:
    def test_round_trip(self, tmp_path: Path) -> None:
        cache = str(tmp_path)
        record_declared_interfaces(str(tmp_path), cache, _steps())
        declared = read_declared_interfaces(str(tmp_path), cache, ["index.html", "main.js"])
        assert declared["index.html"]["identifiers"] == ["game", "score", "lives", "message", "hud"]
        assert declared["main.js"]["signatures"] == ["function update(dt)", "function draw(ctx)"]

    def test_persisted_to_runtime_contracts(self, tmp_path: Path) -> None:
        record_declared_interfaces(str(tmp_path), str(tmp_path), _steps())
        assert (tmp_path / "contracts" / "interface_ledger.json").is_file()

    def test_read_only_returns_requested_files(self, tmp_path: Path) -> None:
        record_declared_interfaces(str(tmp_path), str(tmp_path), _steps())
        declared = read_declared_interfaces(str(tmp_path), str(tmp_path), ["index.html"])
        assert set(declared) == {"index.html"}

    def test_normalizes_dot_slash_target(self, tmp_path: Path) -> None:
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "S1", "target_file": "./index.html", "interface_names": ["game"]}],
        )
        declared = read_declared_interfaces(str(tmp_path), str(tmp_path), ["index.html"])
        assert declared["index.html"]["identifiers"] == ["game"]

    def test_missing_ledger_reads_empty(self, tmp_path: Path) -> None:
        assert read_declared_interfaces(str(tmp_path), str(tmp_path), ["index.html"]) == {}

    def test_step_without_interfaces_is_skipped(self, tmp_path: Path) -> None:
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "S1", "target_file": "readme.md", "interface_names": [], "signatures": []}],
        )
        assert read_declared_interfaces(str(tmp_path), str(tmp_path), ["readme.md"]) == {}


class TestMerge:
    def test_first_writer_wins_and_new_names_append(self, tmp_path: Path) -> None:
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "PM-1-S1", "target_file": "index.html", "interface_names": ["game", "score"]}],
        )
        # A later parent declares a new identifier plus a redundant existing one.
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "PM-2-S1", "target_file": "index.html", "interface_names": ["score", "restartBtn"]}],
        )
        declared = read_declared_interfaces(str(tmp_path), str(tmp_path), ["index.html"])
        assert declared["index.html"]["identifiers"] == ["game", "score", "restartBtn"]


class TestRenderAssumeContract:
    def test_empty_renders_nothing(self) -> None:
        assert render_assume_contract({}) == ""

    def test_lists_identifiers_and_signatures(self) -> None:
        declared = {
            "index.html": {"identifiers": ["game", "score"], "signatures": []},
            "main.js": {"identifiers": ["update"], "signatures": ["function update(dt)"]},
        }
        text = render_assume_contract(declared)
        assert "index.html 已公开标识符: game, score" in text
        assert "main.js 已公开标识符: update" in text
        assert "function update(dt)" in text
        assert "必须复用" in text
