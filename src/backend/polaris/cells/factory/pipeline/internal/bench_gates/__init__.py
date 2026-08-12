"""Factory-bench goal gates and audit attribution.

The public bench runner remains a delivery harness.  The platform-owned facts
that decide whether a generated project is actually runnable live here, inside
the ``factory.pipeline`` cell boundary.

This package is the lossless successor of the former ``bench_gates`` module.
"""

from __future__ import annotations

import json
import os as _os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types as _types
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import suppress
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, Path as _Path
from typing import Any

from polaris.cells.control_plane.verifier_execution.public import (
    RunVerifierPolicyCommandV1,
    run_verifier_policy,
)
from polaris.cells.control_plane.verifier_policy.public import (
    ReadVerifierPolicyQueryV1,
    read_verifier_policy,
)
from polaris.kernelone.events.final_request_evidence import normalize_context_snapshot_ref

from ..factory_stage_helpers import _runtime_row_execution_completed
from ..native_validation_sandbox import (
    NativeValidationContractError,
    NativeValidationSandboxError,
    cargo_native_test_count,
    is_cargo_test_command,
    sandboxed_cargo_test_command,
)
from ..run_ledger import summarize_run_ledger_projection
from . import _audit as _audit_mod, _core as _core_mod, _gates as _gates_mod
from ._audit import (
    _CE_BLUEPRINT_GENERATED_RE,
    _EXECUTION_CONTROL_PLANE_FAILURE_CLASSES,
    _MODEL_PROVIDER_INVALID_REQUEST_TOKENS,
    _MODEL_PROVIDER_RATE_LIMIT_TOKENS,
    _MODEL_PROVIDER_TIMEOUT_TOKENS,
    _MODEL_PROVIDER_UNAVAILABLE_TOKENS,
    _RUNTIME_ENVIRONMENT_FAILURE_TOKENS,
    _TASK_BOUNDARY_FAILURE_CLASSES,
    _TASK_BOUNDARY_FAILURE_STATUSES,
    _canonical_execution_verdict,
    _canonical_failure_attribution,
    _category_signature,
    _check_failure_is_runtime_environment,
    _check_failures,
    _chief_engineer_failure_evidence,
    _chief_engineer_failure_reason,
    _contains_context_budget_signal,
    _director_failure_evidence,
    _director_failure_reason,
    _director_failure_tokens,
    _first_mapping,
    _first_real_run_failure,
    _first_repair_plan_probe,
    _first_task_boundary_verdict,
    _gate_failures,
    _has_model_provider_invalid_request,
    _has_partial_chief_engineer_blueprint_generation,
    _independent_gate_attribution,
    _is_repair_plan_probe_payload,
    _iter_mapping_payloads,
    _legacy_display_attribution,
    _llm_event_error_text,
    _load_runtime_json,
    _mapping_copy,
    _model_provider_failure_evidence,
    _model_provider_failure_reason,
    _nested_chain_results,
    _project_factory_stage_failure,
    _project_named_runtime_metadata,
    _project_qa_verdict,
    _project_runtime_status,
    _project_task_boundary,
    _record_execution_control_plane_attribution,
    _record_has_chief_engineer_blueprint_failure,
    _record_has_director_execution_failure,
    _record_has_explicit_director_execution_failure,
    _record_has_generated_artifact_failure,
    _record_has_model_provider_failure,
    _record_has_qa_artifact_quality_failure,
    _record_has_runtime_environment_failure,
    _record_llm_events,
    _record_model_provider_failure_text,
    _record_repair_convergence_attribution,
    _record_task_boundary_attribution,
    _run_ledger_projection_integrity_available,
    _runtime_dir_candidates,
    _runtime_environment_failure_evidence,
    _runtime_environment_failure_reason,
    _stable_reason,
    aggregate_goal_audit,
    apply_factory_bench_failure_taxonomy,
    build_canonical_bench_projection,
    classify_factory_bench_failure,
)
from ._core import (
    _CPP_SOURCE_SUFFIXES,
    _ENTRYPOINT_FAILURE_MARKER_RE,
    _FAILURE_CATEGORIES,
    _FINAL_QA_GATE_NAMES,
    _PY_ENTRYPOINT_NAMES,
    _REQUIRED_LLM_ROLES,
    _ROLE_ALIASES,
    _ROLE_FAMILIES,
    _TASK_RUNTIME_FACT_SOURCE,
    CANONICAL_BENCH_PROJECTION_SCHEMA,
    CANONICAL_BENCH_PROJECTION_SOURCE,
    LEGACY_BENCH_ARTIFACT_SOURCE,
    _as_dict,
    _canvas_smoke_ok,
    _cli_smoke_result,
    _collect_go_local_imports,
    _discover_go_package_dirs,
    _discover_python_test_files,
    _entrypoint_has_failure_marker,
    _files_with_suffix,
    _find_html_entrypoint,
    _find_python_entrypoint,
    _first_ok_command,
    _go_command,
    _go_version_of,
    _has_package_dependencies,
    _has_shell_chaining,
    _html_local_resource_refs,
    _infer_go_module_name,
    _inline_eval_code,
    _is_fake_npm_lifecycle_script,
    _is_ignorable_web_console_error,
    _is_local_web_resource_failure,
    _is_npm_test_script_manifest_only,
    _is_npm_test_script_placeholder,
    _java_main_class_name,
    _load_package_json,
    _looks_like_python_test,
    _mark_entrypoint_failure,
    _missing_html_local_resources,
    _norm_role,
    _norm_text,
    _normalize_go_imports,
    _package_declares_dependency,
    _package_has_local_tsc,
    _package_requires_project_typescript,
    _paeth_predictor,
    _png_has_nonblank_pixels,
    _primary_source_language,
    _python_pytest_command_has_zero_tests,
    _python_test_command_has_zero_tests,
    _QuietStaticHandler,
    _read_go_mod_module,
    _repair_go_duplicate_declarations,
    _repair_go_import_subpath,
    _required_user_verifier_requirement,
    _resolve_go_binary,
    _run_command,
    _run_language_build_gate,
    _run_platform_verifiers,
    _run_python_pytest_suite,
    _run_python_test_suite,
    _run_python_unittest_suite,
    _run_sandboxed_cargo_test,
    _rust_compile_command,
    _script_tokens,
    _script_uses_tsc,
    _smoke_cpp_cli,
    _smoke_go_cli,
    _smoke_java_cli,
    _smoke_python_cli,
    _smoke_rust_cli,
    _smoke_static_web,
    _smoke_static_web_playwright,
    _tail,
    _to_text,
    _which_any,
)
from ._gates import (
    _BUILD_OUTPUT_DIR_NAMES,
    _SCAFFOLD_FILE_EXTENSIONS,
    _SOURCE_FILE_EXTENSIONS,
    _any_script_references_build_output,
    _append_dispatch_route_events,
    _binding_key,
    _build_declared_source_targets_requirement,
    _build_scaffolding_requirement,
    _command_serves_build_output,
    _first_string,
    _has_build_output_path_reference,
    _is_build_output_path,
    _is_llm_route_skip_event,
    _is_real_llm_route_event,
    _loose_binding_key,
    _matches_family,
    _nested_dict,
    _normalize_llm_event,
    _read_jsonl,
    _resolve_polaris_roots_runtime_dir,
    _resolve_provider_from_expected,
    _script_depends_on_build_output,
    _token_references_build_output,
    build_llm_route_audit,
    build_real_run_gate,
    collect_llm_events,
    resolve_expected_llm_bindings,
)

__all__ = [
    "aggregate_goal_audit",
    "apply_factory_bench_failure_taxonomy",
    "build_llm_route_audit",
    "build_real_run_gate",
    "classify_factory_bench_failure",
    "collect_llm_events",
    "resolve_expected_llm_bindings",
]

# Propagate monkeypatch.setattr(package, name, value) into submodules so tests
# that patch package-level helpers (e.g. _run_command) still affect call sites
# defined in _core/_gates/_audit (same fidelity as the former monofile).
_SUBMODULES = (_core_mod, _gates_mod, _audit_mod)


class _BenchGatesPackage(_types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name.startswith("__") and name.endswith("__"):
            return
        for mod in _SUBMODULES:
            if name in mod.__dict__:
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _BenchGatesPackage
