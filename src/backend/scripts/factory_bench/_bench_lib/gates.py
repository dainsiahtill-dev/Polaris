"""Factory-bench gates, requirements docs, and repair-coverage summaries.

Private helper module for run_factory_bench.
"""

from __future__ import annotations

# ruff: noqa: F821, E402
# mypy: ignore-errors


def _pull_namespace(module: object) -> None:
    """Copy non-dunder attributes into this module (private helpers + imports)."""
    g = globals()
    for key, value in vars(module).items():
        if key.startswith("__"):
            continue
        g[key] = value


from scripts.factory_bench._bench_lib import session as _session

_pull_namespace(_session)
del _session


def _bench_gate(gate: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"gate": gate, "ok": bool(ok), "detail": detail}


def map_factory_run_to_chain_results(
    run_status: dict[str, Any],
    audit_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Return legacy artifact observations without execution authority.

    ``audit_bundle`` is retained for operator inspection only. Canonical
    execution state is projected later from Run Ledger, TaskBoundary, and QA
    verdict facts; no JSON string or prose is parsed here.
    """

    summary_raw = audit_bundle.get("summary_json")
    summary_json = dict(summary_raw) if isinstance(summary_raw, Mapping) else {}
    director_raw = summary_json.get("director")
    director = dict(director_raw) if isinstance(director_raw, Mapping) else {}
    return {
        "source": LEGACY_BENCH_ARTIFACT_SOURCE,
        "authoritative": False,
        "degraded": True,
        "qa_ran": None,
        "qa_passed": None,
        "qa_reason": "",
        "director": {
            "total": director.get("total"),
            "successes": director.get("successes"),
            "failures": director.get("failures"),
            "blocked": director.get("blocked"),
        },
        "contract_goal": "",
        "exit_class": "legacy_unknown",
        "factory_stage_hint": str(run_status.get("phase") or "").strip().lower(),
    }


def project_final_request_refs(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project stable final-request references from normalized LLM events."""

    keys = (
        "role",
        "context_snapshot_ref",
        "final_request_context_audit_hash",
        "final_request_evidence_hash",
        "final_request_evidence_authority_hash",
    )
    projected: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for event in events:
        row = {key: str(event.get(key) or "").strip() for key in keys}
        if not any(row[key] for key in keys[1:]):
            continue
        identity = tuple(row[key] for key in keys)
        if identity in seen:
            continue
        seen.add(identity)
        projected.append(row)
    return projected


def read_factory_qa_invocation_status(workspace: Path, factory_run_id: str) -> bool | None:
    """Read whether QA was physically invoked for one Factory run.

    A deterministic workspace failure intentionally stops before advisory QA.
    The resulting report is still a verdict artifact, but ``qa_invoked=false``;
    requiring a QA provider route in that state misclassifies correct local
    recovery as an LLM routing failure.
    """

    normalized_run_id = str(factory_run_id or "").strip()
    if not normalized_run_id:
        return None
    candidates = (
        workspace / ".polaris" / "roles" / "qa" / normalized_run_id / "report.json",
        workspace / ".polaris" / "qa" / f"{normalized_run_id}.report.json",
        workspace / ".polaris" / "qa" / "latest.report.json",
    )
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(payload, Mapping) and isinstance(payload.get("qa_invoked"), bool):
            return bool(payload["qa_invoked"])
    return None


def required_llm_roles_for_factory_record(
    *,
    chain: dict[str, Any],
    record: dict[str, Any],
) -> tuple[str, ...]:
    chain_results_raw = chain.get("chain_results")
    chain_results: dict[str, Any] = (
        cast(dict[str, Any], chain_results_raw) if isinstance(chain_results_raw, dict) else {}
    )
    start_from = (
        str(
            record.get("factory_bench_start_from")
            or record.get("start_from")
            or chain.get("start_from")
            or chain_results.get("factory_bench_start_from")
            or ""
        )
        .strip()
        .lower()
    )
    stage_hint = str(chain_results.get("factory_stage_hint") or "").strip().lower()
    terminal_status = chain.get("factory_terminal_status")
    if isinstance(terminal_status, dict):
        terminal_status_map = cast(dict[str, Any], terminal_status)
        terminal_metadata_raw = terminal_status_map.get("metadata")
        metadata: dict[str, Any] = (
            cast(dict[str, Any], terminal_metadata_raw) if isinstance(terminal_metadata_raw, dict) else {}
        )
        stage_hint = (
            str(
                metadata.get("last_failed_stage")
                or metadata.get("current_stage")
                or terminal_status_map.get("current_stage")
                or terminal_status_map.get("phase")
                or stage_hint
            )
            .strip()
            .lower()
        )
    exit_class = str(chain_results.get("exit_class") or "").strip().lower()
    director_result = chain_results.get("director")
    director_evidence = False
    if isinstance(director_result, dict):
        director_evidence = any(value not in (None, "", 0) for value in director_result.values())
    qa_invoked = record.get("qa_invoked")
    if start_from in {"director", "director_resume"}:
        resume_roles = []
        if "director" in stage_hint or exit_class in {"director_partial", "qa_failed", "clean"} or director_evidence:
            resume_roles.append("director")
        if qa_invoked is not False and (
            bool(chain_results.get("qa_ran"))
            or "qa" in stage_hint
            or "quality" in stage_hint
            or exit_class in {"qa_failed", "clean"}
        ):
            resume_roles.append("qa")
        return tuple(role for role in FACTORY_BENCH_REQUIRED_LLM_ROLES if role in set(resume_roles))

    roles: list[str] = ["pm"]
    pm_only_stage = "pm" in stage_hint and "chief" not in stage_hint and "director" not in stage_hint
    if exit_class == "pm_failed" or pm_only_stage:
        return tuple(roles)
    roles.append("chief_engineer")
    if exit_class == "chief_engineer_failed" or "chief" in stage_hint or "engineer" in stage_hint:
        return tuple(dict.fromkeys(roles))
    if "director" in stage_hint or exit_class in {"director_partial", "qa_failed", "clean"} or director_evidence:
        roles.append("director")
    if qa_invoked is not False and (
        bool(chain_results.get("qa_ran"))
        or "qa" in stage_hint
        or "quality" in stage_hint
        or exit_class in {"qa_failed", "clean"}
    ):
        roles.append("qa")
    return tuple(role for role in FACTORY_BENCH_REQUIRED_LLM_ROLES if role in set(roles))


def build_factory_bench_gates(record: dict[str, Any], chain: dict[str, Any]) -> list[dict[str, Any]]:
    """Build fail-closed full-chain gates for the factory-bench verdict.

    The per-project deterministic checks measure artifact shape/content only.
    A benchmark run must not pass if the Polaris chain failed, QA was skipped or
    failed, required governance artifacts are absent, or the product was likely
    for a different brief.
    """

    del chain
    canonical = record.get("canonical_projection")
    canonical_map = canonical if isinstance(canonical, Mapping) else build_canonical_bench_projection(record)
    execution = canonical_map.get("execution")
    execution_map = execution if isinstance(execution, Mapping) else {}
    gates = [
        _bench_gate(
            "canonical_execution",
            bool(execution_map.get("ok")),
            str(execution_map.get("reason_code") or "canonical execution projection missing"),
        ),
        _bench_gate(
            "plan_artifact_present",
            bool(record.get("has_plan_doc")),
            "plan artifact discovered" if record.get("has_plan_doc") else "plan artifact missing",
        ),
        _bench_gate(
            "blueprint_artifact_present",
            bool(record.get("has_blueprint_doc")),
            "blueprint artifact discovered" if record.get("has_blueprint_doc") else "blueprint artifact missing",
        ),
        _bench_gate(
            "wrong_product_guard",
            not bool(record.get("wrong_product_suspect")),
            (
                "no wrong-product signal"
                if not record.get("wrong_product_suspect")
                else f"wrong-product suspect match={record.get('wrong_product_match') or 'unknown'}"
            ),
        ),
    ]
    # Backend fingerprint freshness gate (fail-closed)
    backend_freshness = record.get("backend_freshness")
    if isinstance(backend_freshness, dict):
        gates.append(
            _bench_gate(
                "stale_backend_or_unknown",
                bool(backend_freshness.get("ok")),
                str(backend_freshness.get("detail") or "backend freshness check missing detail"),
            )
        )
    else:
        gates.append(
            _bench_gate(
                "stale_backend_or_unknown",
                False,
                "backend freshness gate missing; cannot verify backend is current",
            )
        )

    real_run_gate = record.get("real_run_gate")
    if isinstance(real_run_gate, dict):
        gates.append(
            _bench_gate(
                "real_run_gate",
                bool(real_run_gate.get("ok")),
                str(real_run_gate.get("summary") or "real run gate missing summary"),
            )
        )
    else:
        gates.append(_bench_gate("real_run_gate", False, "real run gate missing"))
    llm_route_audit = record.get("llm_route_audit")
    if isinstance(llm_route_audit, dict):
        gates.append(
            _bench_gate(
                "llm_route_audit",
                bool(llm_route_audit.get("ok")),
                str(llm_route_audit.get("summary") or "LLM route audit missing summary"),
            )
        )
    else:
        gates.append(_bench_gate("llm_route_audit", False, "LLM route audit missing"))
    implementation_depth = record.get("implementation_depth")
    if isinstance(implementation_depth, dict) and implementation_depth:
        gates.append(
            _bench_gate(
                "delivery_depth_gate",
                bool(implementation_depth.get("ok")),
                str(implementation_depth.get("detail") or "implementation depth missing detail"),
            )
        )
    else:
        gates.append(_bench_gate("delivery_depth_gate", False, "implementation depth evidence missing"))
    return gates


def build_director_repair_coverage_gap_summary(
    record: Mapping[str, Any],
    audit_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Project Director repair coverage gaps into the bench record for rule-authoring Agents."""

    reports = _collect_repair_coverage_reports((record, audit_bundle))
    gaps: list[dict[str, Any]] = []
    seen_gap_keys: set[tuple[str, str, str, str, str]] = set()
    languages: set[str] = set()
    archetypes: set[str] = set()
    diagnostic_codes: set[str] = set()
    recommended_routes: set[str] = set()
    handoff_recommendations: set[str] = set()
    slot_statuses: set[str] = set()
    for report in reports:
        for raw_gap in report.get("coverage_gaps") or ():
            if not isinstance(raw_gap, Mapping):
                continue
            gap = _bench_repair_coverage_gap_payload(raw_gap, record=record)
            gap_key = (
                str(gap.get("diagnostic_id") or ""),
                str(gap.get("diagnostic_code") or ""),
                str(gap.get("diagnostic_language") or ""),
                str(gap.get("path") or ""),
                str(gap.get("message") or ""),
            )
            if gap_key in seen_gap_keys:
                continue
            seen_gap_keys.add(gap_key)
            gaps.append(gap)
            languages.add(str(gap.get("diagnostic_language") or "unknown"))
            archetypes.add(str(gap.get("diagnostic_archetype") or "unknown"))
            diagnostic_codes.add(str(gap.get("diagnostic_code") or "unknown"))
            recommended_routes.add(str(gap.get("recommended_route") or "llm_repair"))
            handoff_recommendations.add(str(gap.get("handoff_recommendation") or "coverage_triage_required"))
            slot_statuses.add(str(gap.get("slot_status") or "reserved_slot_missing"))
    return {
        "schema_version": "factory_bench.director_repair_coverage_gap_summary.v1",
        "source": "factory_bench.audit_bundle.director_runtime_repair_coverage",
        "gate_affects_pass": False,
        "rule_discovery_required": bool(gaps),
        "coverage_gap_count": len(gaps),
        "coverage_gap_languages": sorted(languages),
        "coverage_gap_archetypes": sorted(archetypes),
        "coverage_gap_diagnostic_codes": sorted(diagnostic_codes),
        "coverage_gap_recommended_routes": sorted(recommended_routes),
        "coverage_gap_handoff_recommendations": sorted(handoff_recommendations),
        "coverage_gap_slot_statuses": sorted(slot_statuses),
        "coverage_gaps": gaps,
    }


def load_workspace_validation_repair_coverage(
    workspace: Path,
    runtime_dirs: Path | list[Path] | tuple[Path, ...] | None,
) -> dict[str, Any]:
    """Load Director repair coverage embedded in workspace validation artifacts."""

    reports: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for candidate in _workspace_validation_artifact_candidates(workspace, runtime_dirs):
        try:
            key = candidate.resolve().as_posix()
        except OSError:
            key = candidate.as_posix()
        if key in seen_paths or not candidate.is_file():
            continue
        seen_paths.add(key)
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        reports.extend(_workspace_validation_repair_coverage_reports(payload))
    return {
        "schema_version": "factory_bench.workspace_validation_repair_coverage.v1",
        "source": "workspace_validation_artifacts",
        "report_count": len(reports),
        "reports": reports,
    }


def _workspace_validation_artifact_candidates(
    workspace: Path,
    runtime_dirs: Path | list[Path] | tuple[Path, ...] | None,
) -> list[Path]:
    candidates = [
        workspace / ".polaris" / "qa" / "latest.workspace-validation.json",
        *sorted((workspace / ".polaris" / "qa").glob("*.workspace-validation.json")),
        *sorted((workspace / ".polaris" / "roles" / "qa").glob("*/workspace-validation.json")),
    ]
    if runtime_dirs is None:
        runtime_dir_list: list[Path] = []
    elif isinstance(runtime_dirs, Path):
        runtime_dir_list = [runtime_dirs]
    else:
        runtime_dir_list = list(runtime_dirs)
    for runtime_dir in runtime_dir_list:
        candidates.append(runtime_dir / "qa" / "workspace-validation.json")
    return candidates


def _workspace_validation_repair_coverage_reports(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    repair = payload.get("repair")
    if not isinstance(repair, Mapping):
        return reports
    raw_report = repair.get("director_runtime_repair_coverage")
    if _looks_like_repair_coverage_report(raw_report):
        reports.append(dict(cast(Mapping[str, Any], raw_report)))
    rounds = repair.get("rounds")
    if isinstance(rounds, Sequence) and not isinstance(rounds, (str, bytes, bytearray)):
        for item in rounds:
            if not isinstance(item, Mapping):
                continue
            round_report = item.get("director_runtime_repair_coverage")
            if _looks_like_repair_coverage_report(round_report):
                reports.append(dict(cast(Mapping[str, Any], round_report)))
    return reports


def _looks_like_repair_coverage_report(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("coverage_gaps"), list)
        and ("coverage_gap_count" in value or "uncovered_diagnostic_count" in value)
    )


def _collect_repair_coverage_reports(sources: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            if isinstance(value.get("coverage_gaps"), list) and (
                "coverage_gap_count" in value or "uncovered_diagnostic_count" in value
            ):
                reports.append(dict(value))
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for source in sources:
        visit(source)
    return reports


def _bench_repair_coverage_gap_payload(
    gap: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    diagnostic_raw = gap.get("diagnostic")
    diagnostic: Mapping[str, Any] = diagnostic_raw if isinstance(diagnostic_raw, Mapping) else {}
    return {
        "project_id": str(record.get("project_id") or ""),
        "run_id": str(record.get("run_id") or ""),
        "diagnostic_id": str(gap.get("diagnostic_id") or diagnostic.get("diagnostic_id") or ""),
        "diagnostic_code": str(gap.get("diagnostic_code") or diagnostic.get("code") or "unknown"),
        "diagnostic_language": str(gap.get("diagnostic_language") or gap.get("language") or "unknown"),
        "diagnostic_archetype": str(gap.get("diagnostic_archetype") or "unknown"),
        "diagnostic_phase": str(gap.get("diagnostic_phase") or gap.get("phase_suggestion") or "unknown"),
        "path": str(diagnostic.get("path") or ""),
        "message": str(diagnostic.get("message") or ""),
        "reserved_slot_available": bool(gap.get("reserved_slot_available")),
        "slot_status": str(gap.get("slot_status") or "reserved_slot_missing"),
        "recommended_route": str(gap.get("recommended_route") or "llm_repair"),
        "handoff_recommendation": str(gap.get("handoff_recommendation") or "coverage_triage_required"),
        "recommended_next_owner": str(gap.get("recommended_next_owner") or "llm"),
        "audit_reason": str(gap.get("audit_reason") or "known_rule_matched=false"),
        "coverage_status": str(gap.get("coverage_status") or "coverage_gap"),
        "authoritative_rule_registration_allowed": False,
    }


def build_bench_backend_audit_context(
    backend_url: str,
    *,
    backend_token: str = "",
    workspace: str = "",
) -> dict[str, Any]:
    """Build backend freshness and trace metadata for every bench record."""
    freshness = check_backend_freshness(
        backend_url,
        token=backend_token,
        backend_root=_BACKEND_ROOT,
    )
    backend_info = freshness.get("backend_info")
    backend_info_dict: dict[str, Any] = backend_info if isinstance(backend_info, dict) else {}
    metadata = build_run_backend_metadata(
        backend_url,
        token_source="configured" if backend_token else "missing",
        workspace=workspace,
        expected_fingerprint=str(freshness.get("expected_fingerprint") or ""),
        actual_fingerprint=str(freshness.get("actual_fingerprint") or ""),
        backend_pid=backend_info_dict.get("pid") if isinstance(backend_info_dict.get("pid"), int) else None,
        backend_instance_id=str(backend_info_dict.get("instance_id") or ""),
        backend_root=str(backend_info_dict.get("backend_root") or ""),
        backend_startup_time=str(backend_info_dict.get("startup_time") or ""),
        fingerprint_source=str(backend_info_dict.get("source") or ""),
    )
    return {
        "backend_freshness": freshness,
        "backend_metadata": metadata,
    }


def _url_port(value: str) -> int | None:
    parsed = urlparse(str(value or "").strip())
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return None


def _read_task_boundary_verdict_from_run_ledger_projection(
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Read the authoritative TaskBoundary verdict without synthesizing facts.

    Bench is an internal observer. It must never infer a replacement verdict
    from workspace files, prose diagnostics, or a later real-run gate. Missing
    TaskBoundary evidence is itself an execution-control-plane failure.
    """

    task_boundary = projection.get("task_boundary")
    boundary_map: Mapping[str, Any] = task_boundary if isinstance(task_boundary, Mapping) else {}
    latest = boundary_map.get("latest")
    if isinstance(latest, Mapping):
        return dict(latest)
    return {
        "schema_version": "task_boundary_verdict.v1",
        "status": "task_boundary_verdict_missing",
        "ok": False,
        "failure_class": "EXECUTION_EVIDENCE_MISSING",
        "responsible_layer": "execution_control_plane",
        "reason": "No authoritative TaskBoundary verdict was committed to the Execution Ledger",
        "source": "factory_bench_read_projection",
        "authoritative": False,
    }


def apply_factory_bench_gates(record: dict[str, Any], chain: dict[str, Any]) -> None:
    """Fold full-chain gates into ``all_checks_passed`` in-place."""

    static_checks_passed = bool(record.get("static_checks_passed", record.get("all_checks_passed")))
    canonical = record.get("canonical_projection")
    if not isinstance(canonical, Mapping) or canonical.get("source") != CANONICAL_BENCH_PROJECTION_SOURCE:
        record["canonical_projection"] = build_canonical_bench_projection(record)
    record["run_ledger_projection_status"] = summarize_run_ledger_projection(record.get("run_ledger_projection"))
    gates = build_factory_bench_gates(record, chain)
    record["static_checks_passed"] = static_checks_passed
    record["factory_gates"] = gates
    record["all_checks_passed"] = static_checks_passed and all(gate["ok"] for gate in gates)


def _build_language_runnable_contract(primary_language: str) -> str:
    """Build a runnable-language contract specifying how the project must be executable."""
    lang = primary_language.lower().strip()
    if lang == "typescript":
        return (
            "## Language-Specific Runnable Contract (TypeScript)\n"
            "- 必须包含 `package.json` 且定义 `scripts.start` / `scripts.build` 脚本。\n"
            "- `npm install && npm run build` 必须成功。\n"
            "- 必须包含 `tsconfig.json`。\n"
            "- `tsc --noEmit` 必须通过。\n"
        )
    if lang == "python":
        return (
            "## Language-Specific Runnable Contract (Python)\n"
            "- 必须包含 `requirements.txt` 或 `pyproject.toml`。\n"
            "- `python -m pip install -r requirements.txt` 或等价命令必须成功。\n"
        )
    if lang == "rust":
        return "## Language-Specific Runnable Contract (Rust)\n- 必须包含 `Cargo.toml`。\n- `cargo build` 必须成功。\n"
    return ""


def _build_source_tree_contract(primary_language: str, project_type: str) -> str:
    """Build explicit source tree structure requirements for the given language/type.

    This ensures the PM -> Chief Engineer -> Director chain creates src/
    directories and core source files rather than only scaffolding files like
    package.json and tsconfig.json.
    """
    lang = primary_language.lower().strip()
    ptype = project_type.lower().strip()

    sections: list[str] = []
    sections.append("## Source Tree Structure Contract (MANDATORY)\n")
    sections.append(
        "PM -> Chief Engineer -> Director 必须按以下结构创建源代码文件, 仅生成 package.json / tsconfig.json 等配置文件"
        "不算完成, 必须包含核心业务逻辑源码:\n"
    )

    if lang == "typescript":
        sections.append(
            "- 必须包含 `src/` 目录, 核心业务逻辑在 `src/` 下的 `.ts` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/core/` — 核心引擎/逻辑\n"
            "  - `src/index.ts` — 应用入口\n"
            "- 必须包含 `tests/` 目录下的至少一个 `.test.ts` 测试文件。\n"
            '- tsconfig.json 的 `include` 必须包含 `"src/**/*.ts"`。\n'
        )
    elif lang == "javascript":
        sections.append(
            "- 必须包含 `src/` 目录, 核心业务逻辑在 `src/` 下的 `.js` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/core/` — 核心引擎/逻辑\n"
            "  - `src/index.js` — 应用入口\n"
            "- 必须包含 `tests/` 目录下的至少一个测试文件。\n"
        )
    elif lang == "python":
        sections.append(
            "- 必须包含 `src/` 目录(或项目级 Python 包), 核心业务逻辑在 `.py` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/core/` — 核心引擎/逻辑\n"
            "  - `src/__init__.py` 或项目入口 `.py` 文件\n"
            "- 必须包含 `tests/` 目录下的至少一个 `test_*.py` 测试文件。\n"
        )
    elif lang == "go":
        sections.append(
            "- 必须包含 `src/` 或项目级 Go 包, 核心业务逻辑在 `.go` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` 或 `models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `engine/` — 核心引擎/逻辑\n"
            "  - `main.go` 或 `cmd/` — 应用入口\n"
            "- 必须包含 `*_test.go` 测试文件。\n"
        )
    elif lang == "rust":
        sections.append(
            "- 必须包含 `src/` 目录, 核心业务逻辑在 `src/` 下的 `.rs` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` 或 `src/model.rs` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/lib.rs` — 核心引擎/逻辑\n"
            "  - `src/main.rs` — 应用入口\n"
            "- 必须包含 `tests/` 目录下的集成测试或 `#[test]` 单元测试。\n"
        )
    elif lang == "cpp":
        sections.append(
            "- 必须包含 `src/` 目录, 核心业务逻辑在 `.cpp`/`.hpp` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` 或 `include/models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/core/` — 核心引擎/逻辑\n"
            "  - `src/main.cpp` — 应用入口\n"
            "- 必须包含 `tests/` 目录下的测试文件。\n"
        )
    elif lang == "java":
        sections.append(
            "- 必须包含 `src/main/java/` 目录, 核心业务逻辑在 `.java` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/main/java/**/models/` — 数据模型/实体定义\n"
            "  - `src/main/java/**/engine/` 或 `core/` — 核心引擎/逻辑\n"
            "  - `src/main/java/**/App.java` — 应用入口\n"
            "- 必须包含 `src/test/java/` 下的测试文件。\n"
        )
    else:
        sections.append(
            f"- primary_language={lang!r} — 请按该语言惯例创建 src/ 目录结构, "
            "包含核心业务逻辑源码、数据模型和测试文件。\n"
        )

    if "simulation" in ptype or "game" in ptype or "interactive" in ptype:
        sections.append(
            "- simulation/game/interactive 项目必须包含一个可渲染的场景/引擎核心文件 "
            "(如 `src/engine/renderer.ts`, `src/core/simulation.py` 等)。\n"
        )

    sections.append(
        "\n**重要**: Director 任务的 target_files 必须覆盖 src/ 下的源文件, "
        "不能只包含 package.json / tsconfig.json / index.html 等脚手架文件。\n"
    )
    return "".join(sections)


def _build_feature_keywords_contract(feature_keywords: list[str]) -> str:
    """Build a contract section requiring feature keywords in generated source code."""
    if not feature_keywords:
        return ""
    kw_list = ", ".join(feature_keywords)
    return (
        "\n## Feature Keywords Contract (MANDATORY)\n"
        f"以下关键词必须出现在生成的源代码文件中(变量名、类名、注释或字符串均可): "
        f"**{kw_list}**\n"
        "PM -> Chief Engineer -> Director 的任务目标和验收标准必须包含这些关键词。\n"
        "Director 的 target_files 中的源文件必须至少包含其中一个关键词的实际使用。\n"
    )


def build_requirements_doc(project: dict[str, Any]) -> str:
    """Frame the project brief as the requirements file the PM chain consumes."""
    checks = [str(item).strip() for item in project.get("checks", []) if str(item).strip()]
    checks_block = "\n".join(f"- {item}" for item in checks) if checks else "- 未声明额外 deterministic checks。"
    primary_language = str(project.get("primary_language") or "").strip()
    project_type = str(project.get("project_type") or "").strip()
    domain = str(project.get("domain") or "").strip()
    creative_hook = str(project.get("creative_hook") or "").strip()
    feature_keywords = _extract_feature_keywords(project)
    lang_contract = _build_language_runnable_contract(primary_language)
    source_tree_contract = _build_source_tree_contract(primary_language, project_type)
    feature_contract = _build_feature_keywords_contract(feature_keywords)
    level_contract = build_factory_bench_level_contract(project.get("level"), project=project)
    level_contract_block = format_level_contract_for_requirements(level_contract)

    domain_line = f"- 领域: {domain}\n" if domain else ""
    type_line = f"- 项目类型: {project_type}\n" if project_type else ""
    hook_line = f"- 创意钩子: {creative_hook}\n" if creative_hook else ""
    metadata_block = ""
    language_line = f"- 主语言: {primary_language}\n" if primary_language else ""
    if domain_line or type_line or hook_line or language_line:
        metadata_block = (
            "\n## Project Metadata\n"
            f"{language_line}{domain_line}{type_line}{hook_line}"
            "- PM -> Chief Engineer -> Director -> QA 必须在任务合同中保留这些元数据字段, "
            "确保目标语义不丢失。\n"
        )

    return (
        f"# Product Requirements — {project['title']}\n\n"
        "## Goal\n"
        f"- {project['brief']}\n\n"
        f"{metadata_block}\n"
        "## Acceptance Criteria\n"
        "- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。\n"
        "- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口; CLI 项目提供 package.json 脚本或可直接执行的 main 文件; API 项目提供可启动服务入口和健康检查说明。\n"
        "- package.json 脚本不得是只检查 manifest 的占位脚本; build/test/start 或等价脚本必须实际运行产品入口或核心规则验证。\n"
        "- 附 README.md 说明如何运行。\n"
        f"- 关键验收维度: {project.get('test_focus', '')}。\n"
        "\n## Deterministic Checks\n"
        "PM -> Chief Engineer -> Director -> QA 必须把以下检查转成任务目标和验收标准, 缺失任一项应视为未完成:\n"
        f"{checks_block}\n"
        "\n"
        f"{level_contract_block}"
        "\n"
        f"{source_tree_contract}\n"
        f"{feature_contract}\n"
        f"{lang_contract}\n"
    )


def _extract_feature_keywords(project: dict[str, Any]) -> list[str]:
    """Extract feature keywords from content_any checks in the project catalog.

    Returns a deduplicated list of keywords that the Director must embed in the
    generated source code to pass deterministic content checks.
    """
    keywords: list[str] = []
    seen: set[str] = set()
    for check in project.get("checks", []):
        check_str = str(check).strip()
        if check_str.startswith("content_any:"):
            raw = check_str[len("content_any:") :]
            for kw in raw.split("|"):
                kw = kw.strip()
                if kw and kw.lower() not in seen:
                    keywords.append(kw)
                    seen.add(kw.lower())
    return keywords


def _fallback_audit_bundle_from_workspace(workspace: Path) -> dict[str, Any]:
    """Build a partial audit bundle from workspace ``.polaris`` artifacts.

    Used as a fallback when the backend ``/audit-bundle`` endpoint times out or
    returns empty.  Reads dispatch logs, CE review, and plan artifacts that the
    Director writes directly into the workspace.
    """
    bundle: dict[str, Any] = {"gates": [], "events_tail": [], "artifacts": [], "summary_json": None}
    polaris_dir = workspace / ".polaris"
    if not polaris_dir.is_dir():
        return bundle

    events: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    # Collect dispatch logs
    dispatch_dir = polaris_dir / "dispatch"
    if dispatch_dir.is_dir():
        for log_file in sorted(dispatch_dir.glob("*.log.json")):
            try:
                payload = json.loads(log_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    events.append(
                        {
                            "type": "stage_completed",
                            "stage": "director_dispatch",
                            "message": f"dispatch log: {log_file.name}",
                            "result": payload,
                            "source": "workspace_fallback",
                        }
                    )
                    artifacts.append(
                        {
                            "name": log_file.name,
                            "path": str(log_file.relative_to(workspace)),
                            "size": log_file.stat().st_size,
                            "source": "workspace_fallback",
                        }
                    )
            except (OSError, ValueError):
                continue

    # Collect roles director logs
    roles_dir = polaris_dir / "roles" / "director"
    if roles_dir.is_dir():
        for log_file in sorted(roles_dir.rglob("*.log.json")):
            try:
                payload = json.loads(log_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    events.append(
                        {
                            "type": "stage_completed",
                            "stage": "director_dispatch",
                            "message": f"director log: {log_file.name}",
                            "result": payload,
                            "source": "workspace_fallback",
                        }
                    )
                    artifacts.append(
                        {
                            "name": log_file.name,
                            "path": str(log_file.relative_to(workspace)),
                            "size": log_file.stat().st_size,
                            "source": "workspace_fallback",
                        }
                    )
            except (OSError, ValueError):
                continue

    # Collect CE / blueprint review
    for ce_pattern in ("**/ce_*.json", "**/blueprint_*.json", "**/chief_engineer_*.json"):
        for review_file in sorted(polaris_dir.glob(ce_pattern)):
            if not review_file.is_file():
                continue
            try:
                payload = json.loads(review_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload.get("task_id"):
                    artifacts.append(
                        {
                            "name": review_file.name,
                            "path": str(review_file.relative_to(workspace)),
                            "size": review_file.stat().st_size,
                            "task_id": payload.get("task_id"),
                            "source": "workspace_fallback",
                        }
                    )
            except (OSError, ValueError):
                continue

    # Collect plan
    plan_path = polaris_dir / "docs" / "product" / "plan.json"
    if plan_path.is_file():
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            if isinstance(plan_data, dict):
                bundle["summary_json"] = {"plan": plan_data}
                artifacts.append(
                    {
                        "name": "plan.json",
                        "path": str(plan_path.relative_to(workspace)),
                        "size": plan_path.stat().st_size,
                        "source": "workspace_fallback",
                    }
                )
        except (OSError, ValueError):
            pass

    bundle["events_tail"] = events
    bundle["artifacts"] = artifacts
    return bundle
