"""Unit tests for director_execution_backend.py pure logic (no I/O).

Covers:
- _normalize_text / _normalize_bool / _normalize_backend / _normalize_project_slug
- _mapping_payload
- DirectorExecutionBackendRequest dataclass
- resolve_director_execution_backend
"""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.director_execution_backend import (
    DirectorExecutionBackendRequest,
    _mapping_payload,
    _normalize_backend,
    _normalize_bool,
    _normalize_project_slug,
    _normalize_text,
    resolve_director_execution_backend,
)

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_string(self) -> None:
        assert _normalize_text("hello") == "hello"

    def test_whitespace_stripped(self) -> None:
        assert _normalize_text("  hello  ") == "hello"

    def test_none_returns_empty(self) -> None:
        assert _normalize_text(None) == ""  # type: ignore[arg-type]

    def test_int_converted(self) -> None:
        assert _normalize_text(42) == "42"  # type: ignore[arg-type]


class TestNormalizeBool:
    def test_bool_passthrough(self) -> None:
        assert _normalize_bool(True, default=False) is True
        assert _normalize_bool(False, default=True) is False

    def test_none_returns_default(self) -> None:
        assert _normalize_bool(None, default=True) is True
        assert _normalize_bool(None, default=False) is False

    def test_string_true_values(self) -> None:
        for val in ("1", "true", "yes", "on", "TRUE", " Yes "):
            assert _normalize_bool(val, default=False) is True

    def test_string_false_values(self) -> None:
        for val in ("0", "false", "no", "off", "FALSE", " No "):
            assert _normalize_bool(val, default=True) is False

    def test_invalid_returns_default(self) -> None:
        assert _normalize_bool("maybe", default=False) is False
        assert _normalize_bool(42, default=True) is True  # type: ignore[arg-type]


class TestNormalizeBackend:
    def test_default_backend(self) -> None:
        assert _normalize_backend("") == "code_edit"
        assert _normalize_backend(None) == "code_edit"  # type: ignore[arg-type]

    def test_supported_backends(self) -> None:
        assert _normalize_backend("code_edit") == "code_edit"
        assert _normalize_backend("projection_generate") == "projection_generate"
        assert _normalize_backend("projection_refresh_mapping") == "projection_refresh_mapping"
        assert _normalize_backend("projection_reproject") == "projection_reproject"

    def test_unknown_backend_passed_through(self) -> None:
        assert _normalize_backend("unknown") == "unknown"

    def test_case_normalized(self) -> None:
        assert _normalize_backend("CODE_EDIT") == "code_edit"


class TestNormalizeProjectSlug:
    def test_basic_slug(self) -> None:
        assert _normalize_project_slug("My Project", default_value="default") == "my_project"

    def test_empty_returns_default(self) -> None:
        assert _normalize_project_slug("", default_value="default") == "default"
        assert _normalize_project_slug("   ", default_value="default") == "default"

    def test_special_chars_replaced(self) -> None:
        assert _normalize_project_slug("foo-bar.baz", default_value="x") == "foo_bar_baz"

    def test_none_returns_default(self) -> None:
        assert _normalize_project_slug(None, default_value="default") == "default"  # type: ignore[arg-type]


class TestMappingPayload:
    def test_dict_passthrough(self) -> None:
        assert _mapping_payload({"a": 1}) == {"a": 1}

    def test_non_dict_returns_empty(self) -> None:
        assert _mapping_payload("not a dict") == {}  # type: ignore[arg-type]
        assert _mapping_payload(None) == {}  # type: ignore[arg-type]

    def test_keys_converted_to_str(self) -> None:
        assert _mapping_payload({1: "one"}) == {"1": "one"}  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# DirectorExecutionBackendRequest
# ---------------------------------------------------------------------------


class TestDirectorExecutionBackendRequest:
    def test_default_values(self) -> None:
        req = DirectorExecutionBackendRequest()
        assert req.execution_backend == "code_edit"
        assert req.source == "default"
        assert req.is_projection_backend is False
        assert req.is_supported is True

    def test_projection_backend(self) -> None:
        req = DirectorExecutionBackendRequest(execution_backend="projection_generate")
        assert req.is_projection_backend is True
        assert req.is_supported is True

    def test_unsupported_backend(self) -> None:
        req = DirectorExecutionBackendRequest(execution_backend="unknown")
        assert req.is_supported is False

    def test_to_task_metadata_code_edit(self) -> None:
        req = DirectorExecutionBackendRequest(execution_backend="code_edit", source="task_metadata")
        meta = req.to_task_metadata()
        assert meta["execution_backend"] == "code_edit"
        assert meta["execution_backend_source"] == "task_metadata"
        assert "projection" not in meta

    def test_to_task_metadata_projection(self) -> None:
        req = DirectorExecutionBackendRequest(
            execution_backend="projection_generate",
            scenario_id="sc-1",
            experiment_id="exp-1",
            project_slug="my_proj",
            use_pm_llm=False,
            run_verification=False,
            overwrite=True,
        )
        meta = req.to_task_metadata()
        assert meta["execution_backend"] == "projection_generate"
        proj = meta["projection"]
        assert proj["scenario_id"] == "sc-1"
        assert proj["experiment_id"] == "exp-1"
        assert proj["project_slug"] == "my_proj"
        assert proj["use_pm_llm"] is False
        assert proj["run_verification"] is False
        assert proj["overwrite"] is True

    def test_to_task_metadata_with_extra_metadata(self) -> None:
        req = DirectorExecutionBackendRequest(metadata={"custom": "value"})
        meta = req.to_task_metadata()
        assert meta["execution_backend_metadata"]["custom"] == "value"


# ---------------------------------------------------------------------------
# resolve_director_execution_backend
# ---------------------------------------------------------------------------


class TestResolveDirectorExecutionBackend:
    def test_all_none_returns_default(self) -> None:
        result = resolve_director_execution_backend(
            input_data=None, task=None, context=None, default_project_slug="proj"
        )
        assert result.execution_backend == "code_edit"
        assert result.project_slug == "proj"
        assert result.source == "default"

    def test_context_backend(self) -> None:
        result = resolve_director_execution_backend(
            input_data=None,
            task=None,
            context={"director_execution_backend": "projection_generate"},
            default_project_slug="proj",
        )
        assert result.execution_backend == "projection_generate"
        assert result.source == "context"

    def test_task_metadata_backend(self) -> None:
        result = resolve_director_execution_backend(
            input_data=None,
            task={"metadata": {"execution_backend": "projection_reproject"}},
            context=None,
            default_project_slug="proj",
        )
        assert result.execution_backend == "projection_reproject"
        assert result.source == "task_metadata"

    def test_input_data_backend(self) -> None:
        result = resolve_director_execution_backend(
            input_data={"execution_backend": "projection_refresh_mapping"},
            task=None,
            context=None,
            default_project_slug="proj",
        )
        assert result.execution_backend == "projection_refresh_mapping"
        assert result.source == "input_data"

    def test_last_match_wins_precedence(self) -> None:
        # The loop does not break early; last non-empty source wins.
        # task_metadata comes after context in the loop, so it overwrites.
        result = resolve_director_execution_backend(
            input_data=None,
            task={"metadata": {"execution_backend": "projection_generate"}},
            context={"director_execution_backend": "code_edit"},
            default_project_slug="proj",
        )
        assert result.source == "task_metadata"
        assert result.execution_backend == "projection_generate"

    def test_context_wins_when_task_empty(self) -> None:
        result = resolve_director_execution_backend(
            input_data=None,
            task={"metadata": {}},
            context={"director_execution_backend": "code_edit"},
            default_project_slug="proj",
        )
        assert result.source == "context"
        assert result.execution_backend == "code_edit"

    def test_projection_fields_from_input(self) -> None:
        result = resolve_director_execution_backend(
            input_data={
                "projection_requirement": "Build API",
                "projection_scenario": "sc-1",
                "experiment_id": "exp-1",
                "project_slug": "my_proj",
                "use_pm_llm": "false",
                "run_verification": "0",
                "overwrite": "1",
            },
            task=None,
            context=None,
            default_project_slug="default_proj",
        )
        assert result.requirement == "Build API"
        assert result.scenario_id == "sc-1"
        assert result.experiment_id == "exp-1"
        assert result.project_slug == "my_proj"
        assert result.use_pm_llm is False
        assert result.run_verification is False
        assert result.overwrite is True

    def test_projection_fields_from_task_metadata(self) -> None:
        result = resolve_director_execution_backend(
            input_data=None,
            task={
                "metadata": {
                    "projection": {
                        "scenario_id": "sc-2",
                        "requirement": "From task",
                    }
                }
            },
            context=None,
            default_project_slug="proj",
        )
        assert result.scenario_id == "sc-2"
        assert result.requirement == "From task"

    def test_combined_projection_merged(self) -> None:
        result = resolve_director_execution_backend(
            input_data=None,
            task={"metadata": {"projection": {"scenario_id": "sc-a"}}},
            context={"projection": {"experiment_id": "exp-b"}},
            default_project_slug="proj",
        )
        assert result.scenario_id == "sc-a"
        assert result.experiment_id == "exp-b"

    def test_default_project_slug_normalized(self) -> None:
        result = resolve_director_execution_backend(
            input_data=None, task=None, context=None, default_project_slug="My Project"
        )
        assert result.project_slug == "my_project"

    def test_empty_project_slug_uses_default(self) -> None:
        result = resolve_director_execution_backend(
            input_data={"project_slug": ""},
            task=None,
            context=None,
            default_project_slug="fallback",
        )
        assert result.project_slug == "fallback"
