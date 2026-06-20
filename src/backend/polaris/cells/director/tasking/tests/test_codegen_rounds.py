"""Characterization tests for the codegen-rounds cluster (G7 step 7).

These tests pin the *current* behavior of the code-generation round planning
helpers on ``WorkerExecutor`` BEFORE the cluster is extracted into a sibling
collaborator module. The existing ``test_worker_executor.py`` covers the
target-file chunking happy paths; this file fills the untested branches called
out in the G7 blueprint: ``construction_plan.rounds`` (non-concrete filtering and
``[[]]`` collapse), empty/None plan fallthrough, ``_construction_file_plans``
reading ``metadata.construction_plan.files``, the chunk-size clamp, and the
``[[]]`` no-targets fallback.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor


def _task(metadata: dict | None = None, *, subject: str = "", description: str = "") -> MagicMock:
    task = MagicMock()
    task.subject = subject
    task.description = description
    task.metadata = metadata if metadata is not None else {}
    return task


# --------------------------------------------------------------------------
# _build_code_generation_rounds: construction_plan.rounds branch
# --------------------------------------------------------------------------


def test_build_rounds_uses_construction_plan_rounds() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "construction_plan": {
                "rounds": [
                    [{"path": "src/a.ts"}, {"path": "src/b.ts"}],
                    [{"path": "src/c.ts"}],
                ]
            }
        }
    )
    rounds = executor._build_code_generation_rounds(task)
    assert [[item["path"] for item in r] for r in rounds] == [
        ["src/a.ts", "src/b.ts"],
        ["src/c.ts"],
    ]


def test_build_rounds_construction_rounds_filters_non_concrete() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "construction_plan": {
                "rounds": [
                    [{"path": "src/a.ts"}, {"path": "src/scope/"}, {"path": ""}],
                    ["not-a-dict", {"path": "src/dir"}],
                ]
            }
        }
    )
    rounds = executor._build_code_generation_rounds(task)
    # round 1 keeps only the concrete file; round 2 has no concrete dict -> dropped
    assert [[item["path"] for item in r] for r in rounds] == [["src/a.ts"]]


def test_build_rounds_construction_rounds_all_filtered_collapses_to_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "construction_plan": {
                "rounds": [
                    [{"path": "src/scope/"}],
                    [{"path": "another/dir"}],
                ]
            }
        }
    )
    rounds = executor._build_code_generation_rounds(task)
    assert rounds == [[]]


def test_build_rounds_non_list_round_items_skipped() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "construction_plan": {
                "rounds": [
                    "not-a-list",
                    [{"path": "src/a.ts"}],
                ]
            }
        }
    )
    rounds = executor._build_code_generation_rounds(task)
    assert [[item["path"] for item in r] for r in rounds] == [["src/a.ts"]]


# --------------------------------------------------------------------------
# _build_code_generation_rounds: file_plans branch all-filtered -> [[]]
# --------------------------------------------------------------------------


def test_build_rounds_file_plans_all_non_concrete_returns_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task(
        {
            "construction_plan": {
                "file_plans": [
                    {"path": "src/scope/"},
                    {"path": "another/dir"},
                ]
            }
        }
    )
    rounds = executor._build_code_generation_rounds(task)
    assert rounds == [[]]


# --------------------------------------------------------------------------
# _build_code_generation_rounds: empty plan falls through to target files
# --------------------------------------------------------------------------


def test_build_rounds_empty_plan_falls_through_to_target_files(monkeypatch) -> None:
    monkeypatch.delenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", raising=False)
    monkeypatch.delenv("KERNELONE_CE_ROUND_FILE_CHUNK", raising=False)
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"construction_plan": {}, "target_files": ["src/a.ts", "src/b.ts"]})
    rounds = executor._build_code_generation_rounds(task)
    assert [[item["path"] for item in r] for r in rounds] == [["src/a.ts", "src/b.ts"]]


def test_build_rounds_non_dict_plan_falls_through_to_target_files(monkeypatch) -> None:
    monkeypatch.delenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", raising=False)
    monkeypatch.delenv("KERNELONE_CE_ROUND_FILE_CHUNK", raising=False)
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"construction_plan": "garbage", "target_files": ["src/a.ts"]})
    rounds = executor._build_code_generation_rounds(task)
    assert [[item["path"] for item in r] for r in rounds] == [["src/a.ts"]]


def test_build_rounds_no_targets_returns_empty_round() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"target_files": []})
    rounds = executor._build_code_generation_rounds(task)
    assert rounds == [[]]


# --------------------------------------------------------------------------
# _construction_file_plans reads metadata.construction_plan.files
# --------------------------------------------------------------------------


def test_construction_file_plans_reads_files_key() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    files = [{"path": "src/a.ts"}, {"path": "src/b.ts"}]
    task = _task({"construction_plan": {"files": files}})
    assert executor._construction_file_plans(task) == files


def test_construction_file_plans_missing_files_returns_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"construction_plan": {"rounds": []}})
    assert executor._construction_file_plans(task) == []


def test_construction_file_plans_non_dict_plan_returns_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"construction_plan": "nope"})
    assert executor._construction_file_plans(task) == []


def test_construction_file_plans_non_list_files_returns_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task({"construction_plan": {"files": "nope"}})
    assert executor._construction_file_plans(task) == []


def test_construction_file_plans_non_dict_metadata_returns_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task()
    task.metadata = None
    assert executor._construction_file_plans(task) == []


# --------------------------------------------------------------------------
# _resolve_code_generation_round_chunk_size clamp / parse
# --------------------------------------------------------------------------


def test_resolve_chunk_size_default_is_two(monkeypatch) -> None:
    monkeypatch.delenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", raising=False)
    monkeypatch.delenv("KERNELONE_CE_ROUND_FILE_CHUNK", raising=False)
    assert WorkerExecutor._resolve_code_generation_round_chunk_size() == 2


def test_resolve_chunk_size_clamped_to_eight(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "20")
    assert WorkerExecutor._resolve_code_generation_round_chunk_size() == 8


def test_resolve_chunk_size_non_int_is_zero(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "abc")
    assert WorkerExecutor._resolve_code_generation_round_chunk_size() == 0


def test_resolve_chunk_size_zero_string_is_zero(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "0")
    assert WorkerExecutor._resolve_code_generation_round_chunk_size() == 0


def test_resolve_chunk_size_negative_is_zero(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "-3")
    assert WorkerExecutor._resolve_code_generation_round_chunk_size() == 0


def test_resolve_chunk_size_ce_env_fallback(monkeypatch) -> None:
    monkeypatch.delenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", raising=False)
    monkeypatch.setenv("KERNELONE_CE_ROUND_FILE_CHUNK", "4")
    assert WorkerExecutor._resolve_code_generation_round_chunk_size() == 4


# --------------------------------------------------------------------------
# Dead-but-carried helpers (preserve exact behavior)
# --------------------------------------------------------------------------


def test_code_generation_round_chunk_configured(monkeypatch) -> None:
    monkeypatch.delenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", raising=False)
    monkeypatch.delenv("KERNELONE_CE_ROUND_FILE_CHUNK", raising=False)
    assert WorkerExecutor._code_generation_round_chunk_configured() is False
    monkeypatch.setenv("KERNELONE_CE_ROUND_FILE_CHUNK", "2")
    assert WorkerExecutor._code_generation_round_chunk_configured() is True


def test_is_test_like_target_file_class_shim() -> None:
    assert WorkerExecutor._is_test_like_target_file("tests/e2e/a.spec.ts") is True
    assert WorkerExecutor._is_test_like_target_file("src/a.ts") is False


def test_effective_chunk_size_passthrough(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "5")
    executor = WorkerExecutor(workspace="/tmp")
    assert executor._effective_code_generation_round_chunk_size([{"path": "a.ts"}]) == 5
    monkeypatch.setenv("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK", "0")
    assert executor._effective_code_generation_round_chunk_size([{"path": "a.ts"}]) == 0
