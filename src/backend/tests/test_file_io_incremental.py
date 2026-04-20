from __future__ import annotations

from polaris.cells.runtime.projection.public.service import read_incremental


def test_read_incremental_complete_lines_only_buffers_partial_line(tmp_path):
    path = tmp_path / "events.jsonl"
    state: dict[str, object] = {"pos": 0}

    path.write_text('{"a":1', encoding="utf-8")
    assert read_incremental(str(path), state, complete_lines_only=True) == []
    assert state.get("_line_buffer") == '{"a":1'

    with open(path, "a", encoding="utf-8") as handle:
        handle.write('}\n{"b":2')

    assert read_incremental(str(path), state, complete_lines_only=True) == ['{"a":1}']
    assert state.get("_line_buffer") == '{"b":2'

    with open(path, "a", encoding="utf-8") as handle:
        handle.write("}\n")

    assert read_incremental(str(path), state, complete_lines_only=True) == ['{"b":2}']
    assert state.get("_line_buffer") == ""


def test_read_incremental_complete_lines_only_drops_partial_leading_line_after_truncation(tmp_path):
    path = tmp_path / "events.jsonl"
    state: dict[str, object] = {"pos": 0}
    long_prefix = "x" * 80
    path.write_text(f"{long_prefix}\n{{\"ok\":1}}\n", encoding="utf-8")

    lines = read_incremental(str(path), state, max_chars=20, complete_lines_only=True)
    assert lines == ['{"ok":1}']


def test_read_incremental_default_mode_keeps_partial_line_behavior(tmp_path):
    path = tmp_path / "events.log"
    state: dict[str, object] = {"pos": 0}
    path.write_text("partial-line", encoding="utf-8")

    lines = read_incremental(str(path), state, complete_lines_only=False)
    assert lines == ["partial-line"]

