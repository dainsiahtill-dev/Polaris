"""Cross-file interface ledger (组合律 across parents).

Live I3-r14: two parents that legitimately shared index.html invented colliding
identifiers (id=game vs id=gameCanvas) and shipped a non-running product. The
ledger freezes the first parent's declared identifiers so siblings reuse them.
"""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.interface_ledger import (
    read_all_declared_interfaces,
    read_declared_interfaces,
    record_declared_interfaces,
    render_assume_contract,
    validate_declared_interfaces_against_snapshot,
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


class TestConcurrentRecord:
    """Regression: the load-modify-write must be serialized (mirrors the
    file_ownership_ledger fix). Two concurrent CEConsumer fission threads both
    load the same baseline; without a lock the later write clobbers the earlier's
    declared identifiers (lost write) and the cross-file contract goes silently
    incomplete — exactly the drift this ledger exists to prevent.
    """

    def test_two_concurrent_records_both_persist(self, tmp_path: Path) -> None:
        import threading

        ws = str(tmp_path)
        start = threading.Barrier(2)

        def _declare(step_id: str, target: str, name: str) -> None:
            start.wait()
            record_declared_interfaces(ws, ws, [{"step_id": step_id, "target_file": target, "interface_names": [name]}])

        t1 = threading.Thread(target=_declare, args=("S1", "a.js", "alpha"))
        t2 = threading.Thread(target=_declare, args=("S2", "b.js", "beta"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        declared = read_declared_interfaces(ws, ws, ["a.js", "b.js"])
        assert declared["a.js"]["identifiers"] == ["alpha"]
        assert declared["b.js"]["identifiers"] == ["beta"]

    def test_concurrent_same_file_unions_all_names(self, tmp_path: Path) -> None:
        import threading

        ws = str(tmp_path)
        n = 8
        start = threading.Barrier(n)

        def _declare(idx: int) -> None:
            start.wait()
            record_declared_interfaces(
                ws,
                ws,
                [{"step_id": f"S{idx}", "target_file": "shared.js", "interface_names": [f"sym{idx}"]}],
            )

        threads = [threading.Thread(target=_declare, args=(i,)) for i in range(n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        identifiers = read_declared_interfaces(ws, ws, ["shared.js"])["shared.js"]["identifiers"]
        # Every concurrent declaration must survive — no lost write under contention.
        assert sorted(identifiers) == sorted(f"sym{i}" for i in range(n))


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


class TestReadAllDeclaredInterfaces:
    """I3-r28: surface the cross-file contract to the Director as a precondition."""

    def test_returns_all_declared_files(self, tmp_path: Path) -> None:
        record_declared_interfaces(str(tmp_path), str(tmp_path), _steps())
        declared = read_all_declared_interfaces(str(tmp_path), str(tmp_path))
        assert set(declared) == {"index.html", "main.js"}
        assert declared["index.html"]["identifiers"] == ["game", "score", "lives", "message", "hud"]

    def test_exclude_target_drops_own_file(self, tmp_path: Path) -> None:
        record_declared_interfaces(str(tmp_path), str(tmp_path), _steps())
        declared = read_all_declared_interfaces(str(tmp_path), str(tmp_path), exclude_target="main.js")
        assert set(declared) == {"index.html"}

    def test_exclude_target_normalizes_dot_slash(self, tmp_path: Path) -> None:
        record_declared_interfaces(str(tmp_path), str(tmp_path), _steps())
        declared = read_all_declared_interfaces(str(tmp_path), str(tmp_path), exclude_target="./main.js")
        assert "main.js" not in declared

    def test_missing_ledger_is_empty(self, tmp_path: Path) -> None:
        assert read_all_declared_interfaces(str(tmp_path), str(tmp_path)) == {}


class TestValidateDeclaredInterfacesAgainstSnapshot:
    def test_declared_source_interface_present(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src/weather.ts").write_text(
            "export interface WeatherReport { condition: string }\n", encoding="utf-8"
        )
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "S1", "target_file": "src/weather.ts", "interface_names": ["WeatherReport"]}],
        )

        assert validate_declared_interfaces_against_snapshot(str(tmp_path), str(tmp_path)) == []

    def test_declared_source_interface_missing(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src/weather.ts").write_text(
            "export interface WeatherSnapshot { condition: string }\n", encoding="utf-8"
        )
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "S1", "target_file": "src/weather.ts", "interface_names": ["WeatherReport"]}],
        )

        errors = validate_declared_interfaces_against_snapshot(str(tmp_path), str(tmp_path))

        assert errors == [
            "Artifact quality scan failed: declared interface 'WeatherReport' missing from src/weather.ts"
        ]

    def test_unsupported_declared_interface_domain_fails_open(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<html><canvas id='game'></canvas></html>\n", encoding="utf-8")
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "S1", "target_file": "index.html", "interface_names": ["game"]}],
        )

        assert validate_declared_interfaces_against_snapshot(str(tmp_path), str(tmp_path)) == []
