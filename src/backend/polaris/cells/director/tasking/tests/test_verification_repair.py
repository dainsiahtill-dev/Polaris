"""Characterization tests for the verification-repair cluster (G7 step 6).

These tests pin the *current* behavior of the verification-repair helpers on
``WorkerExecutor`` BEFORE the cluster is extracted into a sibling collaborator
module. They are deliberately exhaustive on branch coverage so the post-extract
shims can be proven byte-equivalent.

Note on existence: ``_candidate_paths_for_unresolved_import`` and
``_repair_path_allowed`` gate on ``_resolve_workspace_file_path`` which only
rejects *traversal* (absolute / outside-workspace) paths -- it does NOT require
the file to exist on disk. The tests below reflect that real behavior.

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
# _verification_feedback: 4 precedence shapes + empty/non-dict -> {}
# --------------------------------------------------------------------------


def test_verification_feedback_direct_previous_result() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    payload = {"unresolved_imports": ["a.ts: ../b"]}
    task = _task({"previous_verification_result": payload})
    assert executor._verification_feedback(task) == payload


def test_verification_feedback_phase_context_result() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    payload = {"status": "failed"}
    task = _task({"phase_context": {"verification_result": payload}})
    assert executor._verification_feedback(task) == payload


def test_verification_feedback_task_context_previous_result() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    payload = {"status": "failed", "k": 1}
    task = _task({"task_context": {"previous_verification_result": payload}})
    assert executor._verification_feedback(task) == payload


def test_verification_feedback_task_context_nested_phase_result() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    payload = {"status": "nested"}
    task = _task({"task_context": {"phase_context": {"verification_result": payload}}})
    assert executor._verification_feedback(task) == payload


def test_verification_feedback_precedence_direct_wins() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    direct = {"src": "direct"}
    task = _task(
        {
            "previous_verification_result": direct,
            "phase_context": {"verification_result": {"src": "phase"}},
            "task_context": {"previous_verification_result": {"src": "ctx"}},
        }
    )
    assert executor._verification_feedback(task) == direct


def test_verification_feedback_empty_metadata_returns_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    assert executor._verification_feedback(_task({})) == {}


def test_verification_feedback_non_dict_metadata_returns_empty() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    task = _task()
    task.metadata = None
    assert executor._verification_feedback(task) == {}


def test_verification_feedback_empty_dict_values_skipped() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    # Empty dicts are falsy -> skipped, falls through to {}.
    task = _task(
        {
            "previous_verification_result": {},
            "phase_context": {"verification_result": {}},
            "task_context": {"previous_verification_result": {}},
        }
    )
    assert executor._verification_feedback(task) == {}


def test_verification_feedback_non_dict_direct_skipped_to_phase() -> None:
    executor = WorkerExecutor(workspace="/tmp")
    payload = {"src": "phase"}
    task = _task(
        {
            "previous_verification_result": "not-a-dict",
            "phase_context": {"verification_result": payload},
        }
    )
    assert executor._verification_feedback(task) == payload


# --------------------------------------------------------------------------
# _parse_unresolved_import_entry (static)
# --------------------------------------------------------------------------


def test_parse_unresolved_import_entry_basic() -> None:
    assert WorkerExecutor._parse_unresolved_import_entry("a.ts: ../b") == ("a.ts", "../b")


def test_parse_unresolved_import_entry_strips_quotes_backticks() -> None:
    assert WorkerExecutor._parse_unresolved_import_entry("a.ts: `../b`") == ("a.ts", "../b")
    assert WorkerExecutor._parse_unresolved_import_entry("a.ts: '../b'") == ("a.ts", "../b")
    assert WorkerExecutor._parse_unresolved_import_entry('a.ts: "../b"') == ("a.ts", "../b")


def test_parse_unresolved_import_entry_ref_without_leading_dot_is_none() -> None:
    assert WorkerExecutor._parse_unresolved_import_entry("a.ts: b") is None


def test_parse_unresolved_import_entry_no_colon_is_none() -> None:
    assert WorkerExecutor._parse_unresolved_import_entry("noColon") is None


def test_parse_unresolved_import_entry_empty_ref_is_none() -> None:
    assert WorkerExecutor._parse_unresolved_import_entry("a.ts:") is None


def test_parse_unresolved_import_entry_backslash_source_normalized() -> None:
    assert WorkerExecutor._parse_unresolved_import_entry("dir\\a.ts: ./b") == ("dir/a.ts", "./b")


def test_parse_unresolved_import_entry_none_is_none() -> None:
    assert WorkerExecutor._parse_unresolved_import_entry(None) is None


def test_parse_unresolved_import_entry_splits_on_first_colon() -> None:
    assert WorkerExecutor._parse_unresolved_import_entry("a.ts: ./b:c") == ("a.ts", "./b:c")


# --------------------------------------------------------------------------
# _candidate_paths_for_unresolved_import
# --------------------------------------------------------------------------


def test_candidate_paths_ts_source_resolves_extensionless_ref(tmp_path) -> None:
    # NB: _resolve_workspace_file_path only rejects traversal, NOT non-existence.
    # A .ts source with an extensionless ref therefore yields the full ordered
    # candidate list (every concrete extension + index files), unfiltered by disk.
    executor = WorkerExecutor(workspace=str(tmp_path))
    candidates = executor._candidate_paths_for_unresolved_import("tests/integration/task.test.ts", "../../src/app")
    assert candidates == [
        "src/app.ts",
        "src/app.tsx",
        "src/app.js",
        "src/app.jsx",
        "src/app.json",
        "src/app/index.ts",
        "src/app/index.tsx",
        "src/app/index.js",
    ]


def test_candidate_paths_escaping_ref_returns_empty(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    # resolved path escapes workspace via ../
    assert executor._candidate_paths_for_unresolved_import("a.ts", "../../outside") == []


def test_candidate_paths_explicit_extension_ref(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    candidates = executor._candidate_paths_for_unresolved_import("src/a.ts", "./b.json")
    assert candidates == ["src/b.json"]


def test_candidate_paths_explicit_ts_extension_single(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    candidates = executor._candidate_paths_for_unresolved_import("src/a.ts", "./widget.ts")
    assert candidates == ["src/widget.ts"]


def test_candidate_paths_js_source_branch(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    candidates = executor._candidate_paths_for_unresolved_import("src/a.js", "./util")
    # first concrete candidate kept; index dirs are not concrete file paths in
    # the bare sense but ".js" etc all are concrete.
    assert candidates[0] == "src/util.js"
    assert "src/util.ts" in candidates


def test_candidate_paths_non_ts_js_source_single_candidate(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    # python source -> else branch: raw_candidates = [resolved]; resolved has no
    # extension so it is NOT a concrete target file path -> filtered out.
    assert executor._candidate_paths_for_unresolved_import("pkg/a.py", "./mod") == []


def test_candidate_paths_dedup_preserves_order(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    candidates = executor._candidate_paths_for_unresolved_import("src/a.ts", "./shared")
    # uniqueness preserved
    assert len(candidates) == len(set(candidates))
    assert candidates[0] == "src/shared.ts"


# --------------------------------------------------------------------------
# _repair_path_allowed
# --------------------------------------------------------------------------


def test_repair_path_allowed_in_normalize_target_files(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task({"target_files": ["src/a.ts"]})
    assert executor._repair_path_allowed(task, "src/a.ts") is True


def test_repair_path_allowed_under_scope(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task({"target_files": [], "scope_paths": ["src"]})
    assert executor._repair_path_allowed(task, "src/nested/a.ts") is True


def test_repair_path_allowed_traversal_rejected(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task({"target_files": ["../escape.ts"], "scope_paths": ["src"]})
    assert executor._repair_path_allowed(task, "../escape.ts") is False


def test_repair_path_allowed_neither_target_nor_scope(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task({"target_files": ["src/a.ts"], "scope_paths": ["src"]})
    assert executor._repair_path_allowed(task, "other/b.ts") is False


def test_repair_path_allowed_empty_path(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task({"target_files": ["src/a.ts"]})
    assert executor._repair_path_allowed(task, "") is False


# --------------------------------------------------------------------------
# _unresolved_import_repair_records
# --------------------------------------------------------------------------


def test_unresolved_import_repair_records_basic(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task(
        {
            "target_files": ["tests/integration/task.test.ts"],
            "scope_paths": ["src", "tests"],
            "previous_verification_result": {
                "unresolved_imports": [
                    "tests/integration/task.test.ts: ../../src/app",
                ],
            },
        }
    )
    records = executor._unresolved_import_repair_records(task)
    # candidate_files is truncated to the first 3 of the unfiltered candidate list.
    assert records == [
        {
            "source_file": "tests/integration/task.test.ts",
            "import_ref": "../../src/app",
            "candidate_files": ["src/app.ts", "src/app.tsx", "src/app.js"],
        }
    ]


def test_unresolved_import_repair_records_non_list_unresolved(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task(
        {
            "target_files": ["a.ts"],
            "previous_verification_result": {"unresolved_imports": "nope"},
        }
    )
    assert executor._unresolved_import_repair_records(task) == []


def test_unresolved_import_repair_records_dedup_on_tuple(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task(
        {
            "target_files": ["tests/a.test.ts"],
            "scope_paths": ["src", "tests"],
            "previous_verification_result": {
                "unresolved_imports": [
                    "tests/a.test.ts: ../src/app",
                    "tests/a.test.ts: ../src/app",
                ],
            },
        }
    )
    records = executor._unresolved_import_repair_records(task)
    assert len(records) == 1


def test_unresolved_import_repair_records_skip_source_not_allowed(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task(
        {
            "target_files": ["tests/a.test.ts"],
            "scope_paths": ["tests"],
            "previous_verification_result": {
                "unresolved_imports": [
                    "outside/x.test.ts: ../src/app",
                ],
            },
        }
    )
    assert executor._unresolved_import_repair_records(task) == []


def test_unresolved_import_repair_records_skip_no_allowed_candidates(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    # source allowed (under tests), but candidates resolve under src which is not
    # in target_files nor scope_paths -> no allowed candidates -> skipped.
    task = _task(
        {
            "target_files": ["tests/a.test.ts"],
            "scope_paths": ["tests"],
            "previous_verification_result": {
                "unresolved_imports": [
                    "tests/a.test.ts: ../src/app",
                ],
            },
        }
    )
    assert executor._unresolved_import_repair_records(task) == []


def test_unresolved_import_repair_records_candidate_truncation(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    # ts source extensionless ref produces many raw candidates; allowed ones are
    # truncated to 3.
    task = _task(
        {
            "target_files": ["src/a.ts"],
            "scope_paths": ["src"],
            "previous_verification_result": {
                "unresolved_imports": [
                    "src/a.ts: ./shared",
                ],
            },
        }
    )
    records = executor._unresolved_import_repair_records(task)
    assert len(records) == 1
    assert len(records[0]["candidate_files"]) <= 3


# --------------------------------------------------------------------------
# _verification_repair_target_paths
# --------------------------------------------------------------------------


def test_verification_repair_target_paths_source_plus_first_candidate(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task(
        {
            "target_files": ["tests/integration/task.test.ts"],
            "scope_paths": ["src", "tests"],
            "previous_verification_result": {
                "unresolved_imports": [
                    "tests/integration/task.test.ts: ../../src/app",
                    "tests/integration/task.test.ts: ../../src/repositories/task.repository",
                ],
            },
        }
    )
    paths = executor._verification_repair_target_paths(task)
    assert paths == [
        "tests/integration/task.test.ts",
        "src/app.ts",
        "src/repositories/task.repository.ts",
    ]


def test_verification_repair_target_paths_empty_when_no_records(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task({"target_files": ["a.ts"]})
    assert executor._verification_repair_target_paths(task) == []


# --------------------------------------------------------------------------
# _verification_repair_prompt_section
# --------------------------------------------------------------------------


def test_verification_repair_prompt_section_empty(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task({"target_files": ["a.ts"]})
    assert executor._verification_repair_prompt_section(task) == ("- No previous verification failure was provided.")


def test_verification_repair_prompt_section_populated(tmp_path) -> None:
    executor = WorkerExecutor(workspace=str(tmp_path))
    task = _task(
        {
            "target_files": ["tests/integration/task.test.ts"],
            "scope_paths": ["src", "tests"],
            "previous_verification_result": {
                "unresolved_imports": [
                    "tests/integration/task.test.ts: ../../src/app",
                ],
            },
        }
    )
    section = executor._verification_repair_prompt_section(task)
    lines = section.split("\n")
    assert lines[0] == "- Previous verification failed with unresolved relative imports."
    assert lines[1] == (
        "- Resolve each issue by creating the listed candidate file or "
        "changing the source import to an existing local module."
    )
    # candidates rendered are the record's candidate_files[:3].
    assert (
        "- tests/integration/task.test.ts imports ../../src/app; "
        "allowed repair candidate(s): src/app.ts, src/app.tsx, src/app.js"
    ) in lines
