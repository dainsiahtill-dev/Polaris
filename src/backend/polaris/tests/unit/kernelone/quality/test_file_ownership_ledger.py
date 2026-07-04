"""Cross-parent file-ownership ledger (I3-r18): one file = one owner.

r18 shipped a non-running product because PM-0001-1-S4 and PM-0001-2-step-2 both
wrote main.js with no link -> last-write-wins. The ledger records the FIRST
writer as the permanent owner so a later writer can be serialized + told to edit.
"""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.file_ownership_ledger import (
    build_file_ownership_handoff_requests,
    normalize_file_ownership_target,
    owner_task_identifier_token_aliases,
    read_file_owners,
    record_file_owners,
    render_edit_contract,
    task_identifier_token_aliases,
)


def _steps(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"step_id": sid, "target_file": tf} for sid, tf in pairs]


class TestRecordAndRead:
    def test_public_target_normalization_is_shared_contract(self) -> None:
        assert normalize_file_ownership_target(r".\src\main.py") == "src/main.py"
        assert normalize_file_ownership_target("./src/main.py") == "src/main.py"

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


class TestConcurrentRecord:
    def test_two_concurrent_records_both_persist(self, tmp_path: Path) -> None:
        # Regression (Finding 4): the load-modify-write must be serialized. Two
        # concurrent fissions claiming DIFFERENT files both load the same baseline;
        # without a lock the later write clobbers the earlier's entries (lost write).
        import threading

        ws = str(tmp_path)
        start = threading.Barrier(2)

        def _claim(step_id: str, target: str, parent: str) -> None:
            start.wait()
            record_file_owners(ws, ws, _steps((step_id, target)), parent)

        t1 = threading.Thread(target=_claim, args=("S1", "a.js", "P1"))
        t2 = threading.Thread(target=_claim, args=("S2", "b.js", "P2"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        owners = read_file_owners(ws, ws, ["a.js", "b.js"])
        assert owners["a.js"]["owner_step_id"] == "S1"
        assert owners["b.js"]["owner_step_id"] == "S2"

    def test_concurrent_same_file_keeps_first_writer(self, tmp_path: Path) -> None:
        # Many threads racing on the SAME file must converge to exactly one owner
        # (first-writer-wins is preserved under contention, no lost write).
        import threading

        ws = str(tmp_path)
        n = 8
        start = threading.Barrier(n)

        def _claim(idx: int) -> None:
            start.wait()
            record_file_owners(ws, ws, _steps((f"S{idx}", "shared.js")), f"P{idx}")

        threads = [threading.Thread(target=_claim, args=(i,)) for i in range(n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        owners = read_file_owners(ws, ws, ["shared.js"])
        assert owners["shared.js"]["owner_step_id"].startswith("S")
        # Exactly one owner recorded — re-claiming under the lock never reassigns.
        winner = owners["shared.js"]["owner_step_id"]
        record_file_owners(ws, ws, _steps(("S-late", "shared.js")), "P-late")
        assert read_file_owners(ws, ws, ["shared.js"])["shared.js"]["owner_step_id"] == winner


class TestRenderEditContract:
    def test_emits_director_read_and_edit_instruction_for_owned_file(self) -> None:
        block = render_edit_contract({"main.js": {"owner_step_id": "S4", "owner_parent": "PM-0001-1"}})
        assert "main.js" in block
        assert "S4" in block  # names the owner so the model knows what exists
        assert "不得调用 read_file" in block
        assert "后续 Director" in block
        assert "construction_steps" in block

    def test_does_not_instruct_model_to_declare_cross_parent_depends_on(self) -> None:
        # The model must NOT hand-write a cross-parent owner into depends_on:
        # validate_construction_steps only knows THIS parent's step ids and would
        # reject it (I3-r19 loop). The serializing dependency is injected at
        # publish time instead. The contract explicitly tells the model not to.
        block = render_edit_contract({"main.js": {"owner_step_id": "S4", "owner_parent": "PM-0001-1"}})
        assert "无需" in block or "不要" in block  # "do not hand-write depends_on"

    def test_empty_when_nothing_owned_elsewhere(self) -> None:
        assert render_edit_contract({}) == ""


class TestBuildFileOwnershipHandoffRequests:
    def test_routes_owned_targets_and_preserves_unknown_targets(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        record_file_owners(ws, ws, _steps(("S4", "src/index.js")), "PM-0001-1")

        requests = build_file_ownership_handoff_requests(
            ws,
            ws,
            ["./src/index.js", "src/missing.js", "src/index.js"],
            requesting_task_id="PM-0001-2-step-3",
            reason="quality_repair_targets_outside_current_task_target_files",
        )

        assert len(requests) == 2
        assert requests[0] == {
            "schema_version": "file-ownership-handoff-request/1",
            "target_file": "src/index.js",
            "requesting_task_id": "PM-0001-2-step-3",
            "reason": "quality_repair_targets_outside_current_task_target_files",
            "owner_step_id": "S4",
            "owner_parent": "PM-0001-1",
            "owner_task_identifier_tokens": ["S4", "PM-0001-1", "PM-0001-1-S4"],
            "requesting_task_identifier_tokens": ["PM-0001-2-step-3"],
            "owner_found": True,
            "recommended_route": "owner_task_retry",
            "status": "owner_found",
        }
        assert requests[1]["target_file"] == "src/missing.js"
        assert requests[1]["owner_task_identifier_tokens"] == []
        assert requests[1]["requesting_task_identifier_tokens"] == ["PM-0001-2-step-3"]
        assert requests[1]["owner_found"] is False
        assert requests[1]["recommended_route"] == "scope_authority_resolution"

    def test_owner_task_identifier_tokens_include_composed_parent_step_alias(self) -> None:
        assert owner_task_identifier_token_aliases("S4", "PM-0001-1") == (
            "S4",
            "PM-0001-1",
            "PM-0001-1-S4",
        )
        assert owner_task_identifier_token_aliases("PM-0001-1-S4", "PM-0001-1") == (
            "PM-0001-1-S4",
            "PM-0001-1",
        )

    def test_empty_targets_return_empty_tuple(self, tmp_path: Path) -> None:
        assert (
            build_file_ownership_handoff_requests(
                str(tmp_path),
                str(tmp_path),
                ["", "./"],
                requesting_task_id="TASK-1",
                reason="scope_filter",
            )
            == ()
        )

    def test_task_identifier_token_aliases_normalize_numeric_task_ids(self) -> None:
        assert task_identifier_token_aliases("TASK-04") == ("4", "TASK-04", "TASK-4")
        assert task_identifier_token_aliases("4") == ("4", "TASK-4")
        assert task_identifier_token_aliases("PM-0001-1-S4") == ("PM-0001-1-S4",)
