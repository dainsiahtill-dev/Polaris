"""Cross-parent file-ownership ledger (I3-r18): one file = one owner.

r18 shipped a non-running product because PM-0001-1-S4 and PM-0001-2-step-2 both
wrote main.js with no link -> last-write-wins. The ledger records the FIRST
writer as the permanent owner so a later writer can be serialized + told to edit.
"""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.file_ownership_ledger import (
    read_file_owners,
    record_file_owners,
    render_edit_contract,
)


def _steps(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"step_id": sid, "target_file": tf} for sid, tf in pairs]


class TestRecordAndRead:
    def test_first_writer_owns(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        record_file_owners(ws, ws, _steps(("S4", "main.js")), "PM-0001-1")
        owners = read_file_owners(ws, ws, ["main.js"])
        assert owners["main.js"] == {"owner_step_id": "S4", "owner_parent": "PM-0001-1"}

    def test_second_parent_does_not_steal_ownership(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        record_file_owners(ws, ws, _steps(("S4", "main.js")), "PM-0001-1")
        # PM-0001-2's step targets the same file later — must NOT overwrite the owner.
        record_file_owners(ws, ws, _steps(("step-2", "main.js")), "PM-0001-2")
        owners = read_file_owners(ws, ws, ["main.js"])
        assert owners["main.js"]["owner_step_id"] == "S4"
        assert owners["main.js"]["owner_parent"] == "PM-0001-1"

    def test_normalization_dot_slash_and_backslash(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        record_file_owners(ws, ws, _steps(("S1", "./main.js")), "P1")
        assert read_file_owners(ws, ws, ["main.js"])["main.js"]["owner_step_id"] == "S1"
        assert read_file_owners(ws, ws, [".\\main.js"])["main.js"]["owner_step_id"] == "S1"

    def test_read_only_returns_present_owned_files(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        record_file_owners(ws, ws, _steps(("S1", "index.html")), "P1")
        owners = read_file_owners(ws, ws, ["index.html", "style.css"])
        assert "index.html" in owners
        assert "style.css" not in owners

    def test_missing_ledger_reads_empty(self, tmp_path: Path) -> None:
        assert read_file_owners(str(tmp_path), str(tmp_path), ["main.js"]) == {}

    def test_persisted_under_runtime_contracts(self, tmp_path: Path) -> None:
        record_file_owners(str(tmp_path), str(tmp_path), _steps(("S1", "main.js")), "P1")
        assert (tmp_path / "contracts" / "file_ownership_ledger.json").is_file()

    def test_step_without_id_or_target_skipped(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        record_file_owners(ws, ws, [{"step_id": "", "target_file": "main.js"}], "P1")
        record_file_owners(ws, ws, [{"step_id": "S1", "target_file": ""}], "P1")
        assert read_file_owners(ws, ws, ["main.js"]) == {}

    def test_write_failure_is_swallowed(self, tmp_path: Path, monkeypatch) -> None:
        import polaris.kernelone.quality.file_ownership_ledger as mod

        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(mod, "write_json_atomic", _boom)
        # Must not raise — fission must never abort on a ledger write failure.
        record_file_owners(str(tmp_path), str(tmp_path), _steps(("S1", "main.js")), "P1")


class TestRenderEditContract:
    def test_emits_read_and_edit_instruction_for_owned_file(self) -> None:
        block = render_edit_contract({"main.js": {"owner_step_id": "S4", "owner_parent": "PM-0001-1"}})
        assert "main.js" in block
        assert "S4" in block  # names the owner so the model knows what exists
        assert "read_file" in block or "read" in block

    def test_does_not_instruct_model_to_declare_cross_parent_depends_on(self) -> None:
        # The model must NOT hand-write a cross-parent owner into depends_on:
        # validate_construction_steps only knows THIS parent's step ids and would
        # reject it (I3-r19 loop). The serializing dependency is injected at
        # publish time instead. The contract explicitly tells the model not to.
        block = render_edit_contract({"main.js": {"owner_step_id": "S4", "owner_parent": "PM-0001-1"}})
        assert "无需" in block or "不要" in block  # "do not hand-write depends_on"

    def test_empty_when_nothing_owned_elsewhere(self) -> None:
        assert render_edit_contract({}) == ""
