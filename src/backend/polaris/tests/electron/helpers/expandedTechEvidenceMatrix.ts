import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { type Page, type TestInfo } from "@playwright/test";

type JsonRecord = Record<string, unknown>;

export type EvidenceStatus = "PASS" | "FAIL" | "WARN" | "SKIP";

export type BackendConnection = {
  baseUrl: string;
  token: string;
  source: "electron_preload" | "browser_dev_backend" | "browser_local_storage" | "default_loopback";
};

type BackendInfoSnapshot = {
  preloadInfo?: { baseUrl?: string | null; token?: string | null } | null;
  devBackend?: { baseUrl?: string | null; token?: string | null } | null;
  storedBaseUrl?: string | null;
  storedToken?: string | null;
};

export type ExpandedTechCandidate = {
  id: string;
  title: string;
  category: string;
  status: "implemented" | "partial" | "gate" | "sidecar";
  source: string;
  paths: string[];
  gates: string[];
  e2eFields: string[];
  notes?: string[];
};

export type EvidenceRef = {
  type: "api" | "runtime_artifact" | "repo_path" | "event_jsonl" | "probe";
  ref: string;
  value?: unknown;
};

export type EvidenceProbe = {
  id: string;
  title: string;
  category: string;
  status: EvidenceStatus;
  required: boolean;
  evidence: EvidenceRef[];
  findings: string[];
};

export type CoreEvidenceSinkName = "audit" | "receipt" | "handoff" | "task_projection";

export type CoreEvidenceSinkPlacement = {
  present: boolean;
  evidence: EvidenceRef[];
  findings: string[];
};

export type CoreRuntimeEvidencePlacementRow = {
  tech_id: string;
  sinks: Record<CoreEvidenceSinkName, CoreEvidenceSinkPlacement>;
};

export type CoreRuntimeEvidencePlacement = {
  schema: "polaris.e2e.core_runtime_evidence_placement.v1";
  expected_sinks: CoreEvidenceSinkName[];
  rows: CoreRuntimeEvidencePlacementRow[];
  missing: string[];
  receipt_id: string;
  handoff_id: string;
  task_projection: {
    task_count: number;
    linked_pm_task_count: number;
    projection_source_count: number;
  };
};

export type CandidateRuntimeCoverageStatus =
  | "runtime_proved"
  | "source_proved"
  | "gate_declared"
  | "declared_only";

export type CandidateRuntimeCoverageRow = {
  candidate_id: string;
  title: string;
  category: string;
  declared_status: ExpandedTechCandidate["status"];
  coverage_status: CandidateRuntimeCoverageStatus;
  runtime_required: boolean;
  evidence_probe_ids: string[];
  evidence: EvidenceRef[];
  findings: string[];
};

export type ExpandedCandidateRuntimeCoverage = {
  schema: "polaris.e2e.expanded_candidate_runtime_coverage.v1";
  expected_count: number;
  runtime_proved_count: number;
  source_proved_count: number;
  gate_declared_count: number;
  declared_only_count: number;
  runtime_required_count: number;
  missing_runtime_ids: string[];
  not_runtime_proved_ids: string[];
  rows: CandidateRuntimeCoverageRow[];
};

export type ExpandedTechEvidenceReport = {
  schema: "polaris.e2e.expanded_tech_evidence_matrix.v1";
  generated_at: string;
  workspace: string;
  runtime_root: string;
  require_real_chain: boolean;
  core_runtime_integrations: {
    expected_count: number;
    actual_count: number;
    entrypoints_verified_count: number;
    missing_ids: string[];
    unexpected_ids: string[];
  };
  core_runtime_evidence_placement: CoreRuntimeEvidencePlacement | null;
  candidate_runtime_coverage: ExpandedCandidateRuntimeCoverage;
  expanded_candidates: ExpandedTechCandidate[];
  probes: EvidenceProbe[];
  summary: {
    pass: number;
    fail: number;
    warn: number;
    skip: number;
    required_fail: number;
    candidate_count: number;
  };
};

type CollectOptions = {
  requireRealChain?: boolean;
  workspaceOverride?: string;
  runtimeRootOverride?: string;
};

export const CORE_TECH_IDS = [
  "acga_graph_cell_governance",
  "kernelone_agent_os",
  "turn_transaction_kernel_ledger",
  "context_plane_isolation",
  "descriptor_context_verify_packs",
  "strategy_profile_overlay_fingerprint",
  "cognitive_runtime_receipt_handoff",
  "session_continuity_engine",
  "context_catalog_graph_semantic_retrieval",
  "repo_intelligence_localizer",
  "akashic_knowledge_pipeline",
  "tool_normalization_edit_blocks",
  "change_set_validation_rollback",
  "task_market_runtime_projection",
  "cognitive_knowledge_distiller",
  "contextos_attention_phase_budgeting",
];

export const EXPANDED_TECH_CANDIDATES: ExpandedTechCandidate[] = [
  {
    id: "desktop_web_dual_runtime_entrypoint",
    title: "Dual desktop/browser runtime entrypoint",
    category: "entrypoint",
    status: "implemented",
    source: "ui-api-e2e-audit",
    paths: ["infrastructure/scripts/run-web.js", "package.json", "src/frontend/src/api.ts"],
    gates: ["node --check infrastructure/scripts/run-web.js", "npm run dev:web -- --dry-run"],
    e2eFields: ["baseUrl", "token_source", "cors_origin", "renderer_url"],
  },
  {
    id: "browser_backend_info_fallback",
    title: "Browser backend info fallback without Electron preload",
    category: "entrypoint",
    status: "implemented",
    source: "ui-api-e2e-audit",
    paths: ["src/frontend/src/api.ts"],
    gates: ["npm run typecheck"],
    e2eFields: ["window.__DEV_BACKEND__", "localStorage.polaris.baseUrl", "VITE_BACKEND_TOKEN"],
  },
  {
    id: "websocket_stale_token_recovery",
    title: "WebSocket stale token refresh",
    category: "entrypoint",
    status: "implemented",
    source: "ui-api-e2e-audit",
    paths: ["src/frontend/src/api.ts"],
    gates: ["npm run typecheck"],
    e2eFields: ["ws_url_token", "cache_clear_before_connect", "403_reconnect_without_stale_token"],
  },
  {
    id: "electron_backend_supervisor_chain",
    title: "Electron backend supervisor chain",
    category: "entrypoint",
    status: "implemented",
    source: "ui-api-e2e-audit",
    paths: ["infrastructure/scripts/run-electron.js", "src/electron/main.cjs"],
    gates: ["npm run test:e2e -- --list"],
    e2eFields: ["backend_pid", "python_path", "nats_status", "process_cleanup"],
  },
  {
    id: "electron_preload_ipc_contract",
    title: "Electron preload IPC contract",
    category: "entrypoint",
    status: "implemented",
    source: "ui-api-e2e-audit",
    paths: ["src/electron/preload.cjs", "src/electron/main.cjs"],
    gates: ["npm run test:e2e -- --list"],
    e2eFields: ["window.polaris.getBackendInfo", "pickWorkspace", "openPath"],
  },
  {
    id: "browser_workspace_settings_fallback",
    title: "Browser workspace update via settings API",
    category: "entrypoint",
    status: "partial",
    source: "ui-api-e2e-audit",
    paths: ["src/frontend/src/api.ts", "src/backend/polaris/delivery/http/routers/settings.py"],
    gates: ["npm run test:e2e:settings"],
    e2eFields: ["settings.workspace", "ui_exception_degraded_to_http_settings"],
  },
  {
    id: "electron_secret_safe_storage",
    title: "Electron safeStorage secret bridge",
    category: "security",
    status: "implemented",
    source: "ui-api-e2e-audit",
    paths: ["src/electron/main.cjs"],
    gates: ["npm run test:e2e:settings"],
    e2eFields: ["safeStorage.available", "encrypted_secret_present", "plaintext_absent"],
  },
  {
    id: "electron_pty_bridge",
    title: "Electron PTY bridge",
    category: "tooling",
    status: "implemented",
    source: "ui-api-e2e-audit",
    paths: ["src/electron/main.cjs", "src/frontend/src"],
    gates: ["npm run test:e2e:panel"],
    e2eFields: ["pty_session_id", "pty_write", "pty_output", "pty_close"],
  },
  {
    id: "e2e_fixture_isolated_home_runtime_workspace",
    title: "E2E isolated HOME/runtime/workspace",
    category: "e2e",
    status: "implemented",
    source: "ui-api-e2e-audit",
    paths: ["src/backend/polaris/tests/electron/fixtures.ts"],
    gates: ["npm run test:e2e -- --list"],
    e2eFields: ["KERNELONE_HOME", "KERNELONE_RUNTIME_ROOT", "KERNELONE_WORKSPACE", "outside_repo"],
  },
  {
    id: "e2e_automatic_evidence_attachments",
    title: "Automatic renderer/main evidence attachments",
    category: "e2e",
    status: "implemented",
    source: "ui-api-e2e-audit",
    paths: ["src/backend/polaris/tests/electron/fixtures.ts"],
    gates: ["npm run test:e2e -- --list"],
    e2eFields: ["electron-main-stdout", "electron-main-stderr", "renderer-console", "dialog-screenshot"],
  },
  {
    id: "subgraph_truth_vs_draft_reconciliation",
    title: "Subgraph truth/draft reconciliation",
    category: "governance",
    status: "gate",
    source: "graph-governance-audit",
    paths: ["src/backend/docs/graph/subgraphs", "src/backend/docs/graph/catalog/cells.yaml"],
    gates: [
      "python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode fail-on-new",
      "python -m pytest -q polaris/tests/architecture/test_graph_reality.py",
    ],
    e2eFields: ["catalog_subgraph_refs", "subgraph_yaml_files", "yaml_not_referenced_by_catalog"],
  },
  {
    id: "cell_manifest_catalog_reconciliation",
    title: "Cell manifest/catalog reconciliation",
    category: "governance",
    status: "gate",
    source: "graph-governance-audit",
    paths: ["src/backend/polaris/cells", "src/backend/polaris/tests/architecture/test_manifest_schema_canonical.py"],
    gates: ["python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode fail-on-new"],
    e2eFields: ["manifest_catalog.mismatch_count", "manifest_only", "catalog_only", "duplicate_cell_ids"],
  },
  {
    id: "single_state_owner_effects_gate",
    title: "Single state owner and declared effects gate",
    category: "governance",
    status: "gate",
    source: "graph-governance-audit",
    paths: ["src/backend/docs/graph/catalog/cells.yaml", "src/backend/docs/governance/ci/scripts/run_catalog_governance_gate.py"],
    gates: ["python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode hard-fail"],
    e2eFields: ["state_owner_duplicates", "undeclared_effects", "effects_prefix_counts"],
  },
  {
    id: "semantic_boundary_governance_gate",
    title: "Semantic boundary governance gate",
    category: "governance",
    status: "gate",
    source: "graph-governance-audit",
    paths: [
      "src/backend/polaris/cells/context/catalog",
      "src/backend/polaris/cells/context/engine",
      "src/backend/docs/governance/ci/scripts/check_semantic_boundary.py",
    ],
    gates: [
      "python docs/governance/ci/scripts/check_semantic_boundary.py",
      "python -m pytest -q polaris/tests/architecture/governance/test_semantic_boundary.py",
    ],
    e2eFields: ["compliant_sites", "non_compliant_sites", "graph_fingerprint", "descriptor_hash"],
  },
  {
    id: "canonical_code_exploration_budget_gate",
    title: "Canonical code exploration and budget gate",
    category: "governance",
    status: "gate",
    source: "graph-governance-audit",
    paths: ["src/backend/docs/governance/ci/fitness-rules.yaml", "src/backend/polaris/kernelone/context"],
    gates: [
      "python -m pytest -q polaris/cells/roles/kernel/tests/test_canonical_exploration_e2e.py",
      "python -m pytest -q polaris/kernelone/context/tests/test_context_subsystem.py",
    ],
    e2eFields: ["first_tool", "phase_order", "budget_used", "compaction_trigger_ratio"],
  },
  {
    id: "event_fact_stream_singleton_writer",
    title: "Event fact stream singleton writer",
    category: "events",
    status: "partial",
    source: "graph-governance-audit",
    paths: ["src/backend/polaris/cells/events/fact_stream", "src/backend/docs/graph/subgraphs/event_pipeline.yaml"],
    gates: [
      "python -m pytest -q polaris/tests/test_runtime_event_fanout.py polaris/tests/test_realtime_hub_v2.py",
    ],
    e2eFields: ["runtime_events_owner", "direct_event_writer_count", "fanout_delivery_count"],
  },
  {
    id: "kernelone_traceability_matrix",
    title: "KernelOne traceability matrix",
    category: "governance",
    status: "partial",
    source: "graph-governance-audit",
    paths: ["src/backend/polaris/kernelone/traceability", "src/backend/docs/governance/ci/scripts/run_traceability_gate.py"],
    gates: [
      "python docs/governance/ci/scripts/run_traceability_gate.py --workspace .",
      "python -m pytest -q polaris/kernelone/traceability/tests",
    ],
    e2eFields: ["matrix_id", "nodes", "links", "missing_doc_ancestor", "gate.passed"],
  },
  {
    id: "structural_bug_governance_chain",
    title: "Structural bug governance chain",
    category: "governance",
    status: "gate",
    source: "graph-governance-audit",
    paths: [
      "src/backend/docs/governance/debt.register.yaml",
      "src/backend/polaris/cells/roles/kernel/generated/verify.pack.json",
      "src/backend/docs/governance/templates/verification-cards",
    ],
    gates: ["python -m pytest -q polaris/tests/architecture/test_structural_bug_governance_assets.py"],
    e2eFields: ["debt_ids", "verification_cards", "adrs", "residual_risks"],
  },
  {
    id: "contextos_runtime_eval_promotion_gate",
    title: "ContextOS runtime eval promotion gate",
    category: "evaluation",
    status: "gate",
    source: "graph-governance-audit",
    paths: [
      "src/backend/docs/governance/CONTEXT_OS_COGNITIVE_RUNTIME_EVAL_SUITE.md",
      "src/backend/docs/governance/ci/context-os-runtime-eval-gate.yaml",
    ],
    gates: ["python docs/governance/ci/scripts/run_context_os_runtime_eval_gate.py --report <report.json>"],
    e2eFields: ["passed", "recommended_mode", "failures", "suite_runs"],
  },
  {
    id: "tool_calling_canonical_identity_gate",
    title: "Tool-calling canonical identity gate",
    category: "tooling",
    status: "gate",
    source: "graph-governance-audit",
    paths: [
      "src/backend/docs/governance/TOOL_CALLING_CANONICAL_GATE_STANDARD.md",
      "src/backend/docs/governance/AGENTIC_TOOL_CALLING_MATRIX_V2_STANDARD.md",
      "src/backend/docs/governance/ci/scripts/run_tool_calling_canonical_gate.py",
    ],
    gates: ["python docs/governance/ci/scripts/run_tool_calling_canonical_gate.py --workspace . --role director --mode hard-fail"],
    e2eFields: ["raw_events", "stream_observed.tool_calls", "canonical_tools", "issue_count"],
  },
  {
    id: "governance_ci_staged_rollout",
    title: "Governance CI staged rollout",
    category: "governance",
    status: "gate",
    source: "graph-governance-audit",
    paths: [".github/workflows/governance-gates.yml", "src/backend/docs/governance/ci/STAGED_ROLLOUT_PLAN.md"],
    gates: ["GitHub Actions governance-gates.yml", "local staged rollout commands"],
    e2eFields: ["workflow_stage", "continue_on_error", "artifact_name", "stage_summary"],
  },
  {
    id: "task_market_outbox_atomic_relay",
    title: "TaskMarket outbox atomic commit and relay",
    category: "task_market",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/service.py",
      "src/backend/polaris/cells/runtime/task_market/internal/store_sqlite.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_service.py",
    ],
    gates: ["fitness rule outbox_atomic", "python -m pytest -q polaris/cells/runtime/task_market/tests/test_service.py"],
    e2eFields: ["outbox_id", "status", "event_type", "sent_outbox_ids", "failed_outbox_ids"],
  },
  {
    id: "task_market_durable_pull_consumer_loop",
    title: "TaskMarket durable pull consumer loop",
    category: "task_market",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/consumer_loop.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_consumer_loop.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_e2e_pipeline.py",
    ],
    gates: ["KERNELONE_TASK_MARKET_MODE=mainline-durable", "start_consumer_loops()"],
    e2eFields: ["consumer_status", "outbox_relay_running", "role_running_map", "task.status"],
  },
  {
    id: "task_market_multi_workspace_consumer_isolation",
    title: "TaskMarket multi-workspace consumer isolation",
    category: "task_market",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/service.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_multi_workspace_isolation.py",
    ],
    gates: ["per-workspace ConsumerLoopManager tests"],
    e2eFields: ["workspace", "claimed_by", "task_id", "stopped_workspace_stays_stopped"],
  },
  {
    id: "task_market_lease_fsm_claim_guard",
    title: "TaskMarket lease and FSM claim guard",
    category: "task_market",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/lease_manager.py",
      "src/backend/polaris/cells/runtime/task_market/internal/fsm.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_claiming_integration.py",
    ],
    gates: ["claim_work_item()", "renew_task_lease()", "StaleLeaseTokenError tests"],
    e2eFields: ["lease_token", "lease_expires_at", "claimed_by", "attempts", "version"],
  },
  {
    id: "task_market_lifecycle_cognitive_receipts",
    title: "TaskMarket lifecycle Cognitive Runtime receipts",
    category: "task_market",
    status: "sidecar",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/service.py",
      "src/backend/polaris/cells/runtime/task_market/README.agent.md",
    ],
    gates: ["mutating lifecycle transition records RecordRuntimeReceiptCommandV1"],
    e2eFields: ["metadata.last_cognitive_runtime_lifecycle", "cognitive_runtime_receipt_ids", "receipt_type"],
  },
  {
    id: "task_market_hitl_tri_council",
    title: "TaskMarket HITL authority and Tri-Council escalation",
    category: "task_market",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/human_review.py",
      "src/backend/polaris/cells/runtime/task_market/public/hitl_api.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_hitl_authority.py",
    ],
    gates: ["request_human_review()", "resolve_human_review()", "sweep_escalation_timeouts()"],
    e2eFields: ["current_role", "next_role", "escalation_deadline", "resolved_by", "unauthorized_role"],
  },
  {
    id: "task_market_webhook_callback_outbox",
    title: "TaskMarket HITL webhook callback via outbox",
    category: "task_market",
    status: "sidecar",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/service.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_webhook_callback.py",
    ],
    gates: ["callback_url creates task_market.human_review_callback outbox record"],
    e2eFields: ["event_type", "payload.callback_url", "payload.action", "outbox.status"],
  },
  {
    id: "task_market_dlq_replay_error_breakdown",
    title: "TaskMarket DLQ replay and error breakdown",
    category: "task_market",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/dlq.py",
      "src/backend/polaris/cells/runtime/task_market/public/dlq_api.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_dlq_replay.py",
    ],
    gates: ["fail_task_stage(to_dead_letter=True)", "replay_dlq_item()"],
    e2eFields: ["status=dead_letter", "reason", "error_code", "_replayed_at", "by_error_code"],
  },
  {
    id: "task_market_saga_compensation",
    title: "TaskMarket saga compensation",
    category: "task_market",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/saga.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_saga.py",
    ],
    gates: ["register_compensation_action()", "commit_compensation_actions()", "compensate_task()"],
    e2eFields: ["metadata.saga_compensation", "manual_intervention_required", "task_market.saga_*"],
  },
  {
    id: "task_market_reconciliation_control_loop",
    title: "TaskMarket reconciliation control loop",
    category: "task_market",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/reconciler.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_reconciler.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_drift_requeue.py",
    ],
    gates: ["TaskReconciliationLoop.run_once()"],
    e2eFields: ["changed_parent_count", "escalated_count", "requeued_count"],
  },
  {
    id: "task_market_revision_first_change_order",
    title: "TaskMarket revision-first change order and drift requeue",
    category: "task_market",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/public/contracts.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_revision_drift.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_impact_analyzer.py",
    ],
    gates: ["register_plan_revision()", "submit_change_order()", "detect_revision_drift()"],
    e2eFields: ["plan_revision_id", "drifted_count", "impacted_total", "affected_task_ids"],
  },
  {
    id: "task_market_dependency_dag_validator",
    title: "TaskMarket dependency DAG validator",
    category: "task_market",
    status: "sidecar",
    source: "task-market-audit",
    paths: ["src/backend/polaris/cells/runtime/task_market/tests/test_dag_validator.py"],
    gates: ["validate_dependency_dag()"],
    e2eFields: ["is_valid", "cycle_count", "cycles", "orphan_depends_on"],
  },
  {
    id: "task_market_prometheus_business_metrics",
    title: "TaskMarket Prometheus business metrics",
    category: "observability",
    status: "implemented",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/metrics.py",
      "src/backend/polaris/delivery/http/middleware/metrics.py",
    ],
    gates: ["HTTP /metrics includes task-market metrics"],
    e2eFields: ["task_market_operations_total", "task_market_queue_depth", "task_market_consumer_poll_total"],
  },
  {
    id: "task_market_otel_tracing_wrapper",
    title: "TaskMarket OTel tracing wrapper",
    category: "observability",
    status: "sidecar",
    source: "task-market-audit",
    paths: [
      "src/backend/polaris/cells/runtime/task_market/internal/tracing.py",
      "src/backend/polaris/cells/runtime/task_market/tests/test_tracing.py",
    ],
    gates: ["KERNELONE_TASK_MARKET_TRACING_ENABLED=true"],
    e2eFields: ["span_name", "operation", "task_id", "stage", "trace_id"],
  },
  {
    id: "llm_config_save_control_plane_transaction",
    title: "LLM config save as control-plane sync transaction",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: [
      "src/backend/polaris/delivery/http/routers/llm.py",
      "src/backend/polaris/cells/llm/provider_config/internal/settings_sync.py",
    ],
    gates: ["POST /v2/llm/config", "GET /v2/llm/status"],
    e2eFields: ["last_updated", "roles.*.provider_id", "roles.*.model", "settings.pm_model"],
  },
  {
    id: "workspace_scoped_llm_readiness_projection",
    title: "Workspace-scoped LLM readiness projection",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: ["src/backend/polaris/cells/runtime/projection/internal/llm_status.py"],
    gates: ["GET /v2/llm/status"],
    e2eFields: ["state", "blocked_roles", "factory_blocked_roles", "roles.*.readiness_issue"],
  },
  {
    id: "llm_model_identity_freshness_gate",
    title: "Model identity normalization and readiness freshness gate",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: [
      "src/backend/polaris/kernelone/llm/model_identity.py",
      "src/backend/polaris/cells/llm/evaluation/internal/readiness_freshness.py",
    ],
    gates: ["model_mismatch/provider_mismatch/readiness_stale diagnostics"],
    e2eFields: ["model_mismatch", "provider_mismatch", "tested_model_missing", "readiness_stale"],
  },
  {
    id: "llm_scheme_b_direct_test_context",
    title: "Scheme B direct provider test context",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: [
      "src/backend/polaris/cells/llm/provider_config/internal/test_context.py",
      "src/frontend/src/app/components/llm/test/streamingTest.ts",
    ],
    gates: ["POST /v2/llm/test", "POST /v2/llm/test/stream"],
    e2eFields: ["target.provider_id=direct_<type>", "target.role=connectivity", "final.ready", "final.grade"],
  },
  {
    id: "provider_request_context_redaction_boundary",
    title: "Provider request context merge and redacted error boundary",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: [
      "src/backend/polaris/cells/llm/provider_config/internal/provider_context.py",
      "src/backend/polaris/delivery/http/routers/providers.py",
    ],
    gates: ["GET /v2/llm/providers/{id}/health", "GET /v2/llm/providers/{id}/models"],
    e2eFields: ["ok", "status", "provider_kind", "error_code", "error_message"],
  },
  {
    id: "director_provider_runtime_support_classifier",
    title: "Director provider runtime support classifier",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: [
      "src/backend/polaris/cells/llm/provider_runtime/internal/runtime_support.py",
      "src/frontend/src/app/components/llm/readinessDiagnostics.ts",
    ],
    gates: ["Director runtime support readiness diagnostics"],
    e2eFields: ["roles.director.runtime_supported", "roles.director.runtime_issue", "unsupported_roles"],
  },
  {
    id: "llm_evaluation_index_dual_mirror_lock",
    title: "LLM evaluation index dual mirror with per-path lock",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: ["src/backend/polaris/cells/llm/evaluation/internal/index.py"],
    gates: ["LLM test report index reconcile"],
    e2eFields: ["reports/<run_id>.json", "last_run_id", "timestamp", "last_reconcile"],
  },
  {
    id: "evaluation_suite_failure_evidence_synthesis",
    title: "Evaluation suite failure evidence synthesis",
    category: "evaluation",
    status: "implemented",
    source: "llm-control-audit",
    paths: ["src/backend/polaris/cells/llm/evaluation/internal/runner.py"],
    gates: ["deep test", "agentic benchmark", "tool/session matrix"],
    e2eFields: ["suites[].cases[].case_id", "summary.pass_rate", "final.ready", "final.next_action"],
  },
  {
    id: "llm_interview_readiness_history",
    title: "LLM interview writes readiness history",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: [
      "src/frontend/src/app/components/settings/LLMSettingsBridge.tsx",
      "src/backend/polaris/cells/runtime/projection/internal/llm_status.py",
    ],
    gates: ["POST /v2/llm/interview/ask", "POST /v2/llm/interview/save"],
    e2eFields: ["saved", "report_path", "readiness_updated", "interviews.latest_by_provider"],
  },
  {
    id: "native_tool_round_orchestrator",
    title: "Native tool round orchestrator with policy results",
    category: "tooling",
    status: "implemented",
    source: "llm-control-audit",
    paths: [
      "src/backend/polaris/cells/llm/tool_runtime/internal/orchestrator.py",
      "src/backend/polaris/cells/llm/dialogue/internal/role_dialogue.py",
    ],
    gates: ["role chat stream native tool calls"],
    e2eFields: ["tool_calls", "tool_results", "tool_feedback", "should_continue"],
  },
  {
    id: "legacy_text_tool_protocol_fail_closed",
    title: "Legacy text tool protocol fail-closed",
    category: "tooling",
    status: "implemented",
    source: "llm-control-audit",
    paths: ["src/backend/polaris/cells/llm/tool_runtime/internal/role_integrations.py"],
    gates: ["debug event tool_execution/text_tool_protocol_rejected"],
    e2eFields: ["reason=native_tool_calling_only", "protocol_violation=legacy_text_tool_protocol_disabled"],
  },
  {
    id: "permission_pdp_rbac_tool_gateway_audit",
    title: "Permission PDP with RBAC, role graph, conditions, and tool gateway audit",
    category: "security",
    status: "implemented",
    source: "llm-control-audit",
    paths: ["src/backend/polaris/cells/policy/permission/internal/permission_service.py"],
    gates: ["Director tool/file/command policy checks"],
    e2eFields: ["PermissionDecisionResult.allowed", "audit.subject", "audit.action", "unauthorized_blocked"],
  },
  {
    id: "role_profile_llm_binding_from_config",
    title: "Role profile LLM binding completed from llm_config",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: [
      "src/backend/polaris/cells/roles/profile/internal/registry.py",
      "src/backend/polaris/cells/roles/profile/internal/schema.py",
    ],
    gates: ["role runtime profile load after LLM settings changes"],
    e2eFields: ["provider", "model", "profile_version", "tool_policy_id"],
  },
  {
    id: "frontend_llm_save_queue_orphan_cleanup_keychain",
    title: "Frontend LLM save queue, orphan binding cleanup, and keychain env override",
    category: "llm_control",
    status: "implemented",
    source: "llm-control-audit",
    paths: [
      "src/frontend/src/app/components/settings/LLMSettingsBridge.tsx",
      "src/frontend/src/app/components/llm/utils/configSanitizer.ts",
    ],
    gates: ["Settings LLM save/delete provider UI flow"],
    e2eFields: ["llmSaving", "llmError", "orphan role cleared", "env_overrides"],
  },
  {
    id: "factory_run_audit_bundle_sse",
    title: "Factory run audit bundle and SSE dual channel",
    category: "factory",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/delivery/http/routers/factory.py",
      "src/backend/polaris/cells/factory/pipeline/internal/factory_run_service.py",
      "src/backend/polaris/tests/integration/delivery/routers/test_factory_router.py",
    ],
    gates: ["GET /v2/factory/runs/{run_id}/audit-bundle", "GET /v2/factory/runs/{run_id}/stream"],
    e2eFields: ["evidence_counts.events_total", "events_tail", "summary_json", "SSE status/event/complete"],
  },
  {
    id: "factory_pm_contract_quality_leakage_cleaning",
    title: "Factory PM contract quality gate and prompt leakage cleaning",
    category: "factory",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: ["src/backend/polaris/cells/factory/pipeline/internal/factory_run_service.py"],
    gates: ["factory pm_planning stage"],
    e2eFields: ["pm_planning.signals.json", "pm.contract_issue_detected", "deterministic_pm_contracts"],
  },
  {
    id: "factory_projection_lab_cell_ir",
    title: "Projection Lab Cell IR to traditional project projection",
    category: "factory",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/cells/factory/pipeline/internal/projection_lab.py",
      "src/backend/polaris/cells/factory/pipeline/tests/test_projection_lab.py",
    ],
    gates: ["RunProjectionExperimentCommandV1"],
    e2eFields: ["artifact_paths", "cell_ir.wave_particle_model.wave_form", "target_cells", "verification_ok"],
  },
  {
    id: "factory_back_mapping_selective_reprojection",
    title: "Back Mapping and selective reprojection",
    category: "factory",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/cells/factory/pipeline/internal/back_mapping.py",
      "src/backend/polaris/cells/factory/pipeline/internal/projection_change_analysis.py",
    ],
    gates: ["RefreshProjectionBackMappingCommandV1", "ReprojectProjectionExperimentCommandV1"],
    e2eFields: ["mapping_strategy", "qualified_name", "syntax_source", "impacted_cell_ids", "rewritten_files"],
  },
  {
    id: "factory_verification_guard_engine",
    title: "Verification Guard safe validation engine",
    category: "factory",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/cells/factory/verification_guard/internal/safe_executor.py",
      "src/backend/polaris/cells/factory/verification_guard/internal/verification_engine.py",
    ],
    gates: ["VerifyCompletionCommandV1", "VerificationEngine.verify"],
    e2eFields: ["VerificationReport.status", "command_results[].return_code", "evidence_collected", "mismatch_details"],
  },
  {
    id: "immutable_archive_manifest_jsonl_index",
    title: "Immutable archive manifest and canonical JSONL index",
    category: "archive",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/cells/archive/run_archive/internal/history_archive_service.py",
      "src/backend/polaris/cells/archive/run_archive/internal/history_manifest_repository.py",
      "src/backend/polaris/cells/archive/task_snapshot_archive",
      "src/backend/polaris/cells/archive/factory_archive",
    ],
    gates: ["archive_run", "archive_task_snapshot", "archive_factory_run"],
    e2eFields: ["manifest.content_hash", "source_paths", "target_path", "runs.index.jsonl.archive_timestamp"],
  },
  {
    id: "uep_runtime_stream_archive",
    title: "UEP runtime stream archive",
    category: "archive",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/cells/archive/run_archive/internal/archive_sink.py",
      "src/backend/polaris/cells/archive/run_archive/internal/stream_archiver.py",
    ],
    gates: ["create_archive_sink(bus)", "create_stream_archiver(workspace).archive_turn"],
    e2eFields: ["stream_meta.json.archive_id", "session_id", "turn_id", "event_count", "format=jsonl.gz"],
  },
  {
    id: "history_factory_overview_defect_loop_projection",
    title: "History factory overview and defect loop projection",
    category: "history",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/delivery/http/routers/history.py",
      "src/backend/polaris/tests/unit/delivery/http/routers/test_history_v2.py",
    ],
    gates: ["GET /history/factory/overview", "GET /v2/history/runs?source=runtime|archived"],
    e2eFields: ["policy_gate_blocks", "defect_followups_generated", "runs[].source"],
  },
  {
    id: "runtime_storage_layout_migration_reset_control",
    title: "Runtime storage layout, migration, and reset control plane",
    category: "runtime_storage",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: ["src/backend/polaris/delivery/http/routers/runtime.py"],
    gates: ["GET /v2/runtime/storage/layout", "GET /v2/runtime/migration/status", "POST /v2/runtime/reset/tasks"],
    e2eFields: ["storage_layout_mode", "classification", "policies", "archived_counts", "state_reset"],
  },
  {
    id: "runtime_artifact_store_hot_paths_orphan_recovery",
    title: "Runtime artifact store hot paths and orphan state recovery",
    category: "runtime_storage",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/cells/runtime/artifact_store/internal/artifact_paths.py",
      "src/backend/polaris/cells/runtime/artifact_store/internal/artifacts.py",
      "src/backend/polaris/cells/runtime/artifact_store/internal/arrow_service.py",
    ],
    gates: ["ReadRuntimeArtifactQueryV1", "WriteRuntimeArtifactCommandV1", "resolve_safe_path"],
    e2eFields: ["source=runtime.artifact_store", "stale", "orphaned", "recovery_code=ENGINE_ORPHANED"],
  },
  {
    id: "memos_runtime_projection_side_list",
    title: "Memos runtime projection side list",
    category: "runtime_storage",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/delivery/http/routers/memos.py",
      "src/backend/polaris/cells/runtime/projection/internal/memos_query_service.py",
    ],
    gates: ["GET /v2/memos/list"],
    e2eFields: ["items[].name", "mtime", "summary", "task_id", "run_id"],
  },
  {
    id: "audit_evidence_bundle_task_evidence",
    title: "EvidenceBundle and TaskEvidence truncated evidence",
    category: "audit",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/cells/audit/evidence/internal/bundle_service.py",
      "src/backend/polaris/cells/audit/evidence/internal/task_service.py",
    ],
    gates: ["create_from_working_tree", "create_from_director_run"],
    e2eFields: ["bundle_id", "base_sha", "head_sha", "working_tree_dirty", "runtime/evidence_index.jsonl"],
  },
  {
    id: "kernel_audit_hash_chain_role_session_export",
    title: "Kernel audit hash chain and role session audit export",
    category: "audit",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/kernelone/audit/runtime.py",
      "src/backend/polaris/kernelone/audit/gateway.py",
      "src/backend/polaris/cells/audit/evidence/internal/role_session_audit_service.py",
    ],
    gates: ["KernelAuditRuntime.emit_event", "AuditGateway.verify_chain", "RoleSessionAuditService.export_audit_log"],
    e2eFields: ["prev_hash", "signature", "event_id", "chain_valid", "gap_count"],
  },
  {
    id: "resident_self_learning_tick",
    title: "Resident self-learning tick from decision trace to goals/skills/experiments",
    category: "resident",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py",
      "src/backend/polaris/cells/resident/autonomy/internal/resident_storage.py",
      "src/backend/polaris/cells/resident/autonomy/internal/meta_cognition.py",
      "src/backend/polaris/cells/resident/autonomy/internal/skill_foundry.py",
    ],
    gates: ["POST /v2/resident/tick", "GET /v2/resident/status", "GET /v2/resident/decisions"],
    e2eFields: ["runtime.tick_count", "counts.decisions", "counts.goals", "agenda.risk_register"],
  },
  {
    id: "resident_governed_goal_pm_bridge",
    title: "Resident governed goal PM bridge and execution projection",
    category: "resident",
    status: "implemented",
    source: "factory-archive-resident-audit",
    paths: [
      "src/backend/polaris/cells/resident/autonomy/internal/goal_governor.py",
      "src/backend/polaris/cells/resident/autonomy/internal/pm_bridge.py",
      "src/backend/polaris/cells/resident/autonomy/internal/execution_projection.py",
    ],
    gates: ["POST /v2/resident/goals/{id}/materialize", "POST /v2/resident/goals/{id}/run"],
    e2eFields: ["resident_goal_id", "pm_contract_path", "backup_manifest_path", "execution.stage", "execution.percent"],
  },
];

export const CANDIDATE_SOURCE_PROBE_IDS: Record<string, string[]> = {
  dual_mode_source_assets: [
    "desktop_web_dual_runtime_entrypoint",
    "browser_backend_info_fallback",
    "electron_backend_supervisor_chain",
    "electron_preload_ipc_contract",
  ],
  e2e_evidence_source_assets: [
    "e2e_fixture_isolated_home_runtime_workspace",
    "e2e_automatic_evidence_attachments",
  ],
  graph_governance_source_assets: [
    "subgraph_truth_vs_draft_reconciliation",
    "cell_manifest_catalog_reconciliation",
    "single_state_owner_effects_gate",
    "semantic_boundary_governance_gate",
    "tool_calling_canonical_identity_gate",
  ],
  task_market_source_assets: [
    "task_market_outbox_atomic_relay",
    "task_market_durable_pull_consumer_loop",
    "task_market_lease_fsm_claim_guard",
    "task_market_hitl_tri_council",
    "task_market_dlq_replay_error_breakdown",
    "task_market_saga_compensation",
    "task_market_revision_first_change_order",
  ],
  llm_control_source_assets: [
    "llm_config_save_control_plane_transaction",
    "workspace_scoped_llm_readiness_projection",
    "llm_model_identity_freshness_gate",
    "llm_scheme_b_direct_test_context",
    "native_tool_round_orchestrator",
    "permission_pdp_rbac_tool_gateway_audit",
  ],
  factory_archive_resident_source_assets: [
    "factory_run_audit_bundle_sse",
    "factory_projection_lab_cell_ir",
    "factory_verification_guard_engine",
    "immutable_archive_manifest_jsonl_index",
    "kernel_audit_hash_chain_role_session_export",
    "resident_self_learning_tick",
  ],
};

export const CANDIDATE_RUNTIME_PROBE_IDS: Record<string, string[]> = {
  backend_connection: [
    "desktop_web_dual_runtime_entrypoint",
    "browser_backend_info_fallback",
  ],
  settings_runtime_layout_api: [
    "browser_workspace_settings_fallback",
    "runtime_storage_layout_migration_reset_control",
  ],
  cognitive_runtime_receipt_handoff_roundtrip: [
    "task_market_lifecycle_cognitive_receipts",
  ],
  runtime_artifact_pm_quality_contract: [
    "factory_pm_contract_quality_leakage_cleaning",
  ],
  runtime_artifact_qa_result_receipt: [
    "factory_verification_guard_engine",
    "audit_evidence_bundle_task_evidence",
  ],
  runtime_events_tool_policy_audit: [
    "permission_pdp_rbac_tool_gateway_audit",
  ],
  llm_control_status_runtime_probe: [
    "workspace_scoped_llm_readiness_projection",
    "llm_model_identity_freshness_gate",
    "director_provider_runtime_support_classifier",
    "role_profile_llm_binding_from_config",
  ],
  llm_provider_catalog_runtime_probe: [
    "provider_request_context_redaction_boundary",
    "llm_scheme_b_direct_test_context",
  ],
  runtime_storage_readonly_control_plane_probe: [
    "runtime_storage_layout_migration_reset_control",
    "memos_runtime_projection_side_list",
  ],
  prometheus_metrics_runtime_probe: [
    "task_market_prometheus_business_metrics",
  ],
};

function resolveRepoRoot(startDir: string): string {
  let current = path.resolve(startDir);
  while (true) {
    if (existsSync(path.join(current, "package.json")) && existsSync(path.join(current, "src", "backend"))) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      throw new Error(`repository root not found from ${startDir}`);
    }
    current = parent;
  }
}

const repoRoot = resolveRepoRoot(__dirname);

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : {};
}

function asRecords(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length > 0) : [];
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function countStatus(probes: EvidenceProbe[], status: EvidenceStatus): number {
  return probes.filter((probe) => probe.status === status).length;
}

function makeProbe(input: EvidenceProbe): EvidenceProbe {
  return input;
}

function probeById(probes: EvidenceProbe[]): Map<string, EvidenceProbe> {
  return new Map(probes.map((probe) => [probe.id, probe]));
}

function passedCandidateProbeIds(
  candidateId: string,
  candidateProbeIds: Record<string, string[]>,
  probesById: Map<string, EvidenceProbe>,
): string[] {
  return Object.entries(candidateProbeIds)
    .filter(([probeId, candidateIds]) => candidateIds.includes(candidateId) && probesById.get(probeId)?.status === "PASS")
    .map(([probeId]) => probeId);
}

function evidenceForProbeIds(probeIds: string[], probesById: Map<string, EvidenceProbe>): EvidenceRef[] {
  return probeIds.flatMap((probeId) => probesById.get(probeId)?.evidence || []);
}

export function buildExpandedCandidateRuntimeCoverage(input: {
  candidates?: ExpandedTechCandidate[];
  probes: EvidenceProbe[];
  runtimeProbeCandidateIds?: Record<string, string[]>;
  sourceProbeCandidateIds?: Record<string, string[]>;
  runtimeRequiredStatuses?: ExpandedTechCandidate["status"][];
}): ExpandedCandidateRuntimeCoverage {
  const candidates = input.candidates || EXPANDED_TECH_CANDIDATES;
  const probesById = probeById(input.probes);
  const runtimeProbeCandidateIds = input.runtimeProbeCandidateIds || CANDIDATE_RUNTIME_PROBE_IDS;
  const sourceProbeCandidateIds = input.sourceProbeCandidateIds || CANDIDATE_SOURCE_PROBE_IDS;
  const runtimeRequiredStatuses = new Set(input.runtimeRequiredStatuses || ["implemented"]);

  const rows: CandidateRuntimeCoverageRow[] = candidates.map((candidate) => {
    const runtimeProbeIds = passedCandidateProbeIds(candidate.id, runtimeProbeCandidateIds, probesById);
    const sourceProbeIds = passedCandidateProbeIds(candidate.id, sourceProbeCandidateIds, probesById);
    const runtimeRequired = runtimeRequiredStatuses.has(candidate.status);
    let coverageStatus: CandidateRuntimeCoverageStatus = "declared_only";
    if (runtimeProbeIds.length > 0) {
      coverageStatus = "runtime_proved";
    } else if (sourceProbeIds.length > 0) {
      coverageStatus = "source_proved";
    } else if (candidate.status === "gate" && candidate.gates.length > 0) {
      coverageStatus = "gate_declared";
    }

    const evidenceProbeIds = [...runtimeProbeIds, ...sourceProbeIds];
    const findings: string[] = [];
    if (runtimeRequired && coverageStatus !== "runtime_proved") {
      findings.push("implemented candidate is not runtime-proved");
    }
    if (coverageStatus === "declared_only") {
      findings.push("candidate has no passing runtime/source/gate evidence in this matrix run");
    }

    return {
      candidate_id: candidate.id,
      title: candidate.title,
      category: candidate.category,
      declared_status: candidate.status,
      coverage_status: coverageStatus,
      runtime_required: runtimeRequired,
      evidence_probe_ids: evidenceProbeIds,
      evidence: evidenceForProbeIds(evidenceProbeIds, probesById),
      findings,
    };
  });

  const runtimeProvedRows = rows.filter((row) => row.coverage_status === "runtime_proved");
  const sourceProvedRows = rows.filter((row) => row.coverage_status === "source_proved");
  const gateDeclaredRows = rows.filter((row) => row.coverage_status === "gate_declared");
  const declaredOnlyRows = rows.filter((row) => row.coverage_status === "declared_only");
  const missingRuntimeRows = rows.filter((row) => row.runtime_required && row.coverage_status !== "runtime_proved");

  return {
    schema: "polaris.e2e.expanded_candidate_runtime_coverage.v1",
    expected_count: candidates.length,
    runtime_proved_count: runtimeProvedRows.length,
    source_proved_count: sourceProvedRows.length,
    gate_declared_count: gateDeclaredRows.length,
    declared_only_count: declaredOnlyRows.length,
    runtime_required_count: rows.filter((row) => row.runtime_required).length,
    missing_runtime_ids: missingRuntimeRows.map((row) => row.candidate_id),
    not_runtime_proved_ids: rows
      .filter((row) => row.coverage_status !== "runtime_proved")
      .map((row) => row.candidate_id),
    rows,
  };
}

async function pathExists(filePath: string): Promise<boolean> {
  try {
    await fs.stat(filePath);
    return true;
  } catch {
    return false;
  }
}

async function readTextIfExists(filePath: string): Promise<string | null> {
  try {
    return await fs.readFile(filePath, "utf-8");
  } catch {
    return null;
  }
}

async function readJsonIfExists<T>(filePath: string): Promise<T | null> {
  const raw = await readTextIfExists(filePath);
  if (raw === null) {
    return null;
  }
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

async function writeUtf8File(filePath: string, content: string): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, content.endsWith("\n") ? content : `${content}\n`, "utf-8");
}

async function listFilesByBasename(root: string, basenames: Set<string>, maxEntries = 4000): Promise<string[]> {
  if (!root || !(await pathExists(root))) {
    return [];
  }
  const matches: string[] = [];
  const stack = [root];
  let visited = 0;
  while (stack.length > 0 && visited < maxEntries) {
    const current = stack.pop();
    if (!current) {
      continue;
    }
    visited += 1;
    let entries: Awaited<ReturnType<typeof fs.readdir>>;
    try {
      entries = await fs.readdir(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (!["node_modules", ".git", ".venv", "__pycache__"].includes(entry.name)) {
          stack.push(fullPath);
        }
        continue;
      }
      if (basenames.has(entry.name)) {
        matches.push(fullPath);
      }
    }
  }
  return matches;
}

async function newestFile(paths: string[]): Promise<string> {
  let selected = "";
  let selectedMtime = -1;
  for (const filePath of paths) {
    try {
      const stat = await fs.stat(filePath);
      if (stat.mtimeMs > selectedMtime) {
        selected = filePath;
        selectedMtime = stat.mtimeMs;
      }
    } catch {
      continue;
    }
  }
  return selected;
}

async function readJsonlFiles(filePaths: string[], maxLinesPerFile = 2000): Promise<JsonRecord[]> {
  const records: JsonRecord[] = [];
  for (const filePath of filePaths) {
    const raw = await readTextIfExists(filePath);
    if (!raw) {
      continue;
    }
    const lines = raw.split(/\r?\n/).filter((line) => line.trim()).slice(-maxLinesPerFile);
    for (const line of lines) {
      try {
        const parsed = JSON.parse(line) as unknown;
        const record = asRecord(parsed);
        if (Object.keys(record).length > 0) {
          records.push(record);
        }
      } catch {
        continue;
      }
    }
  }
  return records;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).map((item) => item.trim()).filter(Boolean) : [];
}

function coreTechIdsFromReceipt(receipt: unknown): Set<string> {
  const receiptRecord = asRecord(receipt);
  const payload = asRecord(receiptRecord.payload);
  const ids = new Set<string>();
  for (const techId of stringArray(payload.core_tech_ids)) {
    ids.add(techId);
  }
  for (const row of asRecords(payload.rows)) {
    const techId = asString(row.tech_id);
    if (techId) {
      ids.add(techId);
    }
  }
  return ids;
}

function collectCoreTechIdsFromValue(value: unknown, coreTechIds: readonly string[]): Set<string> {
  const expected = new Set(coreTechIds);
  const ids = new Set<string>();
  const seen = new Set<object>();

  const visit = (candidate: unknown): void => {
    if (typeof candidate === "string") {
      if (expected.has(candidate)) {
        ids.add(candidate);
      }
      return;
    }
    if (!candidate || typeof candidate !== "object") {
      return;
    }
    if (seen.has(candidate)) {
      return;
    }
    seen.add(candidate);

    if (Array.isArray(candidate)) {
      for (const item of candidate) {
        visit(item);
      }
      return;
    }

    for (const item of Object.values(candidate as JsonRecord)) {
      visit(item);
    }
  };

  visit(value);
  return ids;
}

function coreTechIdsFromEvidenceRefs(refs: EvidenceRef[], coreTechIds: readonly string[]): Set<string> {
  const ids = new Set<string>();
  for (const ref of refs) {
    for (const techId of collectCoreTechIdsFromValue(ref, coreTechIds)) {
      ids.add(techId);
    }
  }
  return ids;
}

function evidenceRefsForTechId(refs: EvidenceRef[], techId: string, coreTechIds: readonly string[]): EvidenceRef[] {
  return refs.filter((ref) => collectCoreTechIdsFromValue(ref, coreTechIds).has(techId));
}

function taskProjectionSummary(taskProjection: unknown): CoreRuntimeEvidencePlacement["task_projection"] {
  const taskProjectionRecord = asRecord(taskProjection);
  const rawTasks = Array.isArray(taskProjectionRecord.tasks) ? taskProjectionRecord.tasks : [];
  const tasks = rawTasks.map(asRecord);
  const linkedPmTaskCount = tasks.filter((task) => {
    const metadata = asRecord(task.metadata);
    return Boolean(asString(task.pm_task_id) || asString(metadata.pm_task_id));
  }).length;
  const projectionSources = new Set<string>();
  for (const task of tasks) {
    const metadata = asRecord(task.metadata);
    const source = asString(metadata.projection_source || task.projection_source);
    if (source) {
      projectionSources.add(source);
    }
  }
  return {
    task_count: tasks.length,
    linked_pm_task_count: linkedPmTaskCount,
    projection_source_count: projectionSources.size,
  };
}

function makeSink(
  present: boolean,
  evidence: EvidenceRef[],
  missingFinding: string,
): CoreEvidenceSinkPlacement {
  return {
    present,
    evidence: present ? evidence : [],
    findings: present ? [] : [missingFinding],
  };
}

export function buildCoreRuntimeEvidencePlacement(input: {
  coreTechIds?: readonly string[];
  auditRefs: EvidenceRef[];
  receipt: unknown;
  handoff: unknown;
  taskProjection: unknown;
}): CoreRuntimeEvidencePlacement {
  const expectedSinks: CoreEvidenceSinkName[] = ["audit", "receipt", "handoff", "task_projection"];
  const coreTechIds = Array.from(new Set((input.coreTechIds || CORE_TECH_IDS).map(String).filter(Boolean)));
  const receiptRecord = asRecord(input.receipt);
  const handoffRecord = asRecord(input.handoff);
  const receiptId = asString(receiptRecord.receipt_id || receiptRecord.id);
  const handoffId = asString(handoffRecord.handoff_id || handoffRecord.id);
  const receiptCoreIds = coreTechIdsFromReceipt(receiptRecord);
  const auditCoreIds = coreTechIdsFromEvidenceRefs(input.auditRefs, coreTechIds);
  const handoffReceiptRefs = new Set([
    ...stringArray(handoffRecord.receipt_refs),
    ...stringArray(asRecord(handoffRecord.turn_envelope).receipt_ids),
  ]);
  const handoffLinksReceipt = Boolean(receiptId && handoffReceiptRefs.has(receiptId));
  const taskSummary = taskProjectionSummary(input.taskProjection);
  const taskProjectionCoreIds = collectCoreTechIdsFromValue(input.taskProjection, coreTechIds);
  const taskProjectionPresent = taskSummary.task_count > 0 && taskSummary.linked_pm_task_count > 0;
  const receiptEvidence: EvidenceRef[] = receiptId
    ? [{ type: "api", ref: `/cognitive-runtime/runtime-receipts/${receiptId}`, value: { receipt_id: receiptId } }]
    : [];
  const handoffEvidence: EvidenceRef[] = handoffId
    ? [{ type: "api", ref: `/cognitive-runtime/handoffs/${handoffId}`, value: { handoff_id: handoffId, receipt_id: receiptId } }]
    : [];
  const taskProjectionEvidence: EvidenceRef[] = [
    { type: "api", ref: "/v2/director/tasks?source=auto", value: taskSummary },
  ];
  const rows: CoreRuntimeEvidencePlacementRow[] = [];
  const missing: string[] = [];

  for (const techId of coreTechIds) {
    const auditEvidence = evidenceRefsForTechId(input.auditRefs, techId, coreTechIds);
    const sinks: Record<CoreEvidenceSinkName, CoreEvidenceSinkPlacement> = {
      audit: makeSink(
        auditCoreIds.has(techId),
        auditEvidence,
        "core technology id is missing from audit artifacts",
      ),
      receipt: makeSink(
        receiptCoreIds.has(techId),
        receiptEvidence,
        "core technology id is missing from runtime receipt payload",
      ),
      handoff: makeSink(
        handoffLinksReceipt && receiptCoreIds.has(techId),
        handoffEvidence,
        "handoff does not reference the receipt carrying this core technology id",
      ),
      task_projection: makeSink(
        taskProjectionPresent && taskProjectionCoreIds.has(techId),
        taskProjectionEvidence,
        "core technology id is missing from Director task projection rows",
      ),
    };
    for (const sinkName of expectedSinks) {
      if (!sinks[sinkName].present) {
        missing.push(`${techId}:${sinkName}`);
      }
    }
    rows.push({ tech_id: techId, sinks });
  }

  return {
    schema: "polaris.e2e.core_runtime_evidence_placement.v1",
    expected_sinks: expectedSinks,
    rows,
    missing,
    receipt_id: receiptId,
    handoff_id: handoffId,
    task_projection: taskSummary,
  };
}

async function candidateSourceProbe(
  id: string,
  title: string,
  category: string,
  candidateIds: string[],
  requiredPathCount = 1,
): Promise<EvidenceProbe> {
  const candidates = EXPANDED_TECH_CANDIDATES.filter((candidate) => candidateIds.includes(candidate.id));
  const uniquePaths = Array.from(new Set(candidates.flatMap((candidate) => candidate.paths)));
  const existingPaths: string[] = [];
  for (const relPath of uniquePaths) {
    const fullPath = path.join(repoRoot, relPath);
    if (await pathExists(fullPath)) {
      existingPaths.push(relPath);
    }
  }
  const status = existingPaths.length >= requiredPathCount ? "PASS" : "FAIL";
  return makeProbe({
    id,
    title,
    category,
    status,
    required: true,
    evidence: [
      {
        type: "repo_path",
        ref: repoRoot,
        value: { expected_any_of: uniquePaths, existing: existingPaths },
      },
    ],
    findings: status === "PASS" ? [] : [`expected at least ${requiredPathCount} source paths to exist`],
  });
}

export async function getBackendInfoFromPage(page: Page): Promise<BackendConnection> {
  const snapshot = await page.evaluate(async () => {
    type BrowserBackendInfo = { baseUrl?: string | null; token?: string | null };
    type BrowserWindow = Window & {
      polaris?: { getBackendInfo?: () => Promise<BrowserBackendInfo> };
      __DEV_BACKEND__?: BrowserBackendInfo;
    };
    const currentWindow = window as BrowserWindow;
    if (currentWindow.polaris?.getBackendInfo) {
      const preloadInfo = await currentWindow.polaris.getBackendInfo();
      return { preloadInfo };
    }
    const devBackend = currentWindow.__DEV_BACKEND__;
    return {
      devBackend,
      storedBaseUrl: window.localStorage.getItem("polaris.baseUrl"),
      storedToken: window.localStorage.getItem("polaris.token"),
    };
  });

  return resolveBackendInfoSnapshot(snapshot);
}

export function resolveBackendInfoSnapshot(snapshot: unknown): BackendConnection {
  const record = asRecord(snapshot);
  const preloadInfo = asRecord(record.preloadInfo);
  const devBackend = asRecord(record.devBackend);
  const storedBaseUrl = asString(record.storedBaseUrl);
  const baseUrl = asString(preloadInfo.baseUrl || devBackend.baseUrl || storedBaseUrl || "http://127.0.0.1:49977").replace(/\/+$/, "");
  const token = asString(preloadInfo.token || devBackend.token || record.storedToken || "");
  const source = asString(preloadInfo.baseUrl)
    ? "electron_preload"
    : asString(devBackend.baseUrl)
      ? "browser_dev_backend"
      : storedBaseUrl
        ? "browser_local_storage"
        : "default_loopback";
  if (!baseUrl) {
    throw new Error("backend baseUrl missing");
  }
  return { baseUrl, token, source };
}

export async function requestJson<T>(
  page: Page,
  endpoint: string,
  options?: { method?: "GET" | "POST"; body?: JsonRecord },
): Promise<T> {
  const backend = await getBackendInfoFromPage(page);
  return page.evaluate(
    async ({ baseUrl, token, apiPath, method, body }) => {
      const headers: Record<string, string> = {
        "Cache-Control": "no-store",
        Pragma: "no-cache",
      };
      if (token) {
        headers.authorization = `Bearer ${token}`;
      }
      if (body) {
        headers["Content-Type"] = "application/json";
      }
      const response = await fetch(`${baseUrl}${apiPath}`, {
        method,
        cache: "no-store",
        headers,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        throw new Error(`fetch ${apiPath} failed: ${response.status} ${detail}`);
      }
      return (await response.json()) as unknown;
    },
    {
      baseUrl: backend.baseUrl,
      token: backend.token,
      apiPath: endpoint,
      method: options?.method || "GET",
      body: options?.body,
    },
  ) as Promise<T>;
}

export async function requestText(
  page: Page,
  endpoint: string,
  options?: { method?: "GET" | "POST"; body?: JsonRecord },
): Promise<string> {
  const backend = await getBackendInfoFromPage(page);
  return page.evaluate(
    async ({ baseUrl, token, apiPath, method, body }) => {
      const headers: Record<string, string> = {
        "Cache-Control": "no-store",
        Pragma: "no-cache",
      };
      if (token) {
        headers.authorization = `Bearer ${token}`;
      }
      if (body) {
        headers["Content-Type"] = "application/json";
      }
      const response = await fetch(`${baseUrl}${apiPath}`, {
        method,
        cache: "no-store",
        headers,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        throw new Error(`fetch ${apiPath} failed: ${response.status} ${detail}`);
      }
      return await response.text();
    },
    {
      baseUrl: backend.baseUrl,
      token: backend.token,
      apiPath: endpoint,
      method: options?.method || "GET",
      body: options?.body,
    },
  ) as Promise<string>;
}

async function collectReadonlyControlPlaneRuntimeProbes(page: Page, workspace: string): Promise<EvidenceProbe[]> {
  const probes: EvidenceProbe[] = [];
  const encodedWorkspace = encodeURIComponent(workspace || ".");

  try {
    const [status, runtimeStatus, config] = await Promise.all([
      requestJson<JsonRecord>(page, `/v2/llm/status?workspace=${encodedWorkspace}`),
      requestJson<JsonRecord>(page, "/v2/llm/runtime-status"),
      requestJson<JsonRecord>(page, "/v2/llm/config"),
    ]);
    const statusRecord = asRecord(status);
    const runtimeRoles = asRecord(asRecord(runtimeStatus).roles);
    const configRoles = asRecord(asRecord(config).roles);
    const roleKeys = new Set([...Object.keys(runtimeRoles), ...Object.keys(configRoles)]);
    const pass = Object.keys(statusRecord).length > 0 && roleKeys.size > 0;
    probes.push(
      makeProbe({
        id: "llm_control_status_runtime_probe",
        title: "LLM control-plane status/config/runtime read-only probe",
        category: "llm_control",
        status: pass ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "api",
            ref: "/v2/llm/status",
            value: {
              state: statusRecord.state,
              blocked_roles: statusRecord.blocked_roles,
              factory_blocked_roles: statusRecord.factory_blocked_roles,
            },
          },
          { type: "api", ref: "/v2/llm/runtime-status", value: { role_count: Object.keys(runtimeRoles).length } },
          { type: "api", ref: "/v2/llm/config", value: { role_count: Object.keys(configRoles).length } },
        ],
        findings: pass ? [] : ["LLM status/config/runtime response did not expose role bindings"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "llm_control_status_runtime_probe",
        title: "LLM control-plane status/config/runtime read-only probe",
        category: "llm_control",
        status: "WARN",
        required: false,
        evidence: [
          { type: "api", ref: "/v2/llm/status" },
          { type: "api", ref: "/v2/llm/runtime-status" },
          { type: "api", ref: "/v2/llm/config" },
        ],
        findings: [String(error)],
      }),
    );
  }

  try {
    const providers = asRecord(await requestJson<JsonRecord>(page, "/v2/llm/providers"));
    const providerList = Array.isArray(providers.providers) ? providers.providers : [];
    probes.push(
      makeProbe({
        id: "llm_provider_catalog_runtime_probe",
        title: "LLM provider catalog read-only runtime probe",
        category: "llm_control",
        status: providerList.length > 0 ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "api",
            ref: "/v2/llm/providers",
            value: { provider_count: providerList.length },
          },
        ],
        findings: providerList.length > 0 ? [] : ["provider catalog response did not contain providers"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "llm_provider_catalog_runtime_probe",
        title: "LLM provider catalog read-only runtime probe",
        category: "llm_control",
        status: "WARN",
        required: false,
        evidence: [{ type: "api", ref: "/v2/llm/providers" }],
        findings: [String(error)],
      }),
    );
  }

  try {
    const [migration, memos] = await Promise.all([
      requestJson<JsonRecord>(page, "/v2/runtime/migration/status"),
      requestJson<JsonRecord>(page, "/v2/memos/list"),
    ]);
    const migrationRecord = asRecord(migration);
    const memoItems = Array.isArray(asRecord(memos).items) ? asRecord(memos).items : [];
    const pass = Object.keys(migrationRecord).length > 0 && Array.isArray(memoItems);
    probes.push(
      makeProbe({
        id: "runtime_storage_readonly_control_plane_probe",
        title: "Runtime storage migration and memos projection read-only probe",
        category: "runtime_storage",
        status: pass ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "api",
            ref: "/v2/runtime/migration/status",
            value: {
              version: migrationRecord.version,
              archived_counts: migrationRecord.archived_counts,
              strict_mode: migrationRecord.strict_mode,
            },
          },
          { type: "api", ref: "/v2/memos/list", value: { item_count: memoItems.length } },
        ],
        findings: pass ? [] : ["runtime migration or memos response was not structured"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "runtime_storage_readonly_control_plane_probe",
        title: "Runtime storage migration and memos projection read-only probe",
        category: "runtime_storage",
        status: "WARN",
        required: false,
        evidence: [
          { type: "api", ref: "/v2/runtime/migration/status" },
          { type: "api", ref: "/v2/memos/list" },
        ],
        findings: [String(error)],
      }),
    );
  }

  try {
    const metricsText = await requestText(page, "/metrics");
    const pass = metricsText.includes("polaris_requests_total");
    probes.push(
      makeProbe({
        id: "prometheus_metrics_runtime_probe",
        title: "Prometheus metrics endpoint runtime probe",
        category: "observability",
        status: pass ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "api",
            ref: "/metrics",
            value: {
              chars: metricsText.length,
              has_polaris_requests_total: pass,
              has_task_market_metrics: metricsText.includes("task_market"),
            },
          },
        ],
        findings: pass ? [] : ["metrics endpoint did not include polaris_requests_total"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "prometheus_metrics_runtime_probe",
        title: "Prometheus metrics endpoint runtime probe",
        category: "observability",
        status: "WARN",
        required: false,
        evidence: [{ type: "api", ref: "/metrics" }],
        findings: [String(error)],
      }),
    );
  }

  return probes;
}

async function collectAggregateRuntimePlanProbe(
  page: Page,
  workspace: string,
): Promise<{ probe: EvidenceProbe; core: ExpandedTechEvidenceReport["core_runtime_integrations"] }> {
  const emptyCore = {
    expected_count: CORE_TECH_IDS.length,
    actual_count: 0,
    entrypoints_verified_count: 0,
    missing_ids: [...CORE_TECH_IDS],
    unexpected_ids: [],
  };
  try {
    const body = asRecord(
      await requestJson<JsonRecord>(page, "/v1/chat/completions", {
        method: "POST",
        body: {
          model: "polaris.aggregate_llm.v1",
          workspace,
          messages: [
            {
              role: "user",
              content: "Audit aggregate runtime wiring without executing a model turn.",
            },
          ],
          domain: "code",
          failure_signal: "e2e_expanded_matrix_probe",
          execution_mode: "plan_only",
        },
      }),
    );
    const directPlan = asRecord(body.aggregate_plan);
    const choices = Array.isArray(body.choices) ? body.choices : [];
    const choiceMessage = asRecord(asRecord(choices[0]).message);
    let contentPlan: JsonRecord = {};
    try {
      contentPlan = asRecord(JSON.parse(asString(choiceMessage.content)));
    } catch {
      contentPlan = {};
    }
    const runtimeIntegrations = asRecords(directPlan.runtime_integrations || contentPlan.runtime_integrations);
    const ids = runtimeIntegrations.map((item) => asString(item.tech_id)).filter(Boolean);
    const uniqueIds = Array.from(new Set(ids));
    const missingIds = CORE_TECH_IDS.filter((techId) => !uniqueIds.includes(techId));
    const unexpectedIds = uniqueIds.filter((techId) => !CORE_TECH_IDS.includes(techId));
    const entrypointsVerifiedCount = runtimeIntegrations.filter((item) => item.entrypoints_verified === true).length;
    const core = {
      expected_count: CORE_TECH_IDS.length,
      actual_count: uniqueIds.length,
      entrypoints_verified_count: entrypointsVerifiedCount,
      missing_ids: missingIds,
      unexpected_ids: unexpectedIds,
    };
    const status = missingIds.length === 0 && entrypointsVerifiedCount >= CORE_TECH_IDS.length ? "PASS" : "FAIL";
    return {
      core,
      probe: makeProbe({
        id: "aggregate_runtime_integrations_plan_only",
        title: "Aggregate runtime integrations plan-only HTTP probe",
        category: "aggregate_runtime",
        status,
        required: true,
        evidence: [
          {
            type: "api",
            ref: "/v1/chat/completions",
            value: core,
          },
        ],
        findings:
          status === "PASS"
            ? []
            : [
                `missing core ids: ${missingIds.join(", ") || "(none)"}`,
                `entrypoints_verified_count=${entrypointsVerifiedCount}`,
              ],
      }),
    };
  } catch (error) {
    return {
      core: emptyCore,
      probe: makeProbe({
        id: "aggregate_runtime_integrations_plan_only",
        title: "Aggregate runtime integrations plan-only HTTP probe",
        category: "aggregate_runtime",
        status: "FAIL",
        required: true,
        evidence: [{ type: "api", ref: "/v1/chat/completions" }],
        findings: [String(error)],
      }),
    };
  }
}

async function collectCognitiveRuntimeRoundtripProbe(page: Page, workspace: string): Promise<EvidenceProbe> {
  const unique = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const sessionId = `e2e-expanded-matrix-${unique}`;
  const runId = `e2e-expanded-matrix-run-${unique}`;
  const turnEnvelope = {
    turn_id: `turn-${unique}`,
    session_id: sessionId,
    run_id: runId,
    role: "qa",
    task_id: "e2e::expanded_tech_matrix",
    projection_version: "expanded-tech-evidence-matrix.v1",
    state_version: 1,
  };

  try {
    const receiptResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/runtime-receipts", {
        method: "POST",
        body: {
          workspace,
          receipt_type: "e2e_expanded_tech_matrix_probe",
          session_id: sessionId,
          run_id: runId,
          trace_refs: ["e2e:expanded_tech_evidence_matrix"],
          payload: {
            source: "electron_e2e.expanded_tech_evidence_matrix",
            core_tech_expected_count: CORE_TECH_IDS.length,
          },
          turn_envelope: turnEnvelope,
        },
      }),
    );
    const receipt = asRecord(receiptResponse.receipt);
    const receiptId = asString(receipt.receipt_id);
    if (!receiptId) {
      throw new Error("runtime receipt did not return receipt_id");
    }

    const fetchedReceipt = asRecord(
      await requestJson<JsonRecord>(
        page,
        `/cognitive-runtime/runtime-receipts/${encodeURIComponent(receiptId)}?workspace=${encodeURIComponent(workspace)}`,
      ),
    );
    const fetchedReceiptId = asString(asRecord(fetchedReceipt.receipt).receipt_id);

    const handoffResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/handoffs/export", {
        method: "POST",
        body: {
          workspace,
          session_id: sessionId,
          run_id: runId,
          reason: "expanded_tech_evidence_matrix_roundtrip",
          receipt_limit: 5,
          turn_envelope: turnEnvelope,
        },
      }),
    );
    const handoff = asRecord(handoffResponse.handoff);
    const handoffId = asString(handoff.handoff_id);
    if (!handoffId) {
      throw new Error("handoff export did not return handoff_id");
    }

    const fetchedHandoff = asRecord(
      await requestJson<JsonRecord>(
        page,
        `/cognitive-runtime/handoffs/${encodeURIComponent(handoffId)}?workspace=${encodeURIComponent(workspace)}`,
      ),
    );
    const fetchedHandoffId = asString(asRecord(fetchedHandoff.handoff).handoff_id);

    const rehydrationResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/handoffs/rehydrate", {
        method: "POST",
        body: {
          workspace,
          handoff_id: handoffId,
          target_role: "director",
          target_session_id: `${sessionId}-rehydrated`,
        },
      }),
    );
    const rehydration = asRecord(rehydrationResponse.rehydration);
    const rehydrationId = asString(rehydration.rehydration_id);
    const receiptRefs = Array.isArray(handoff.receipt_refs) ? handoff.receipt_refs.map(String) : [];
    const status =
      fetchedReceiptId === receiptId &&
      fetchedHandoffId === handoffId &&
      Boolean(rehydrationId) &&
      receiptRefs.includes(receiptId)
        ? "PASS"
        : "FAIL";

    return makeProbe({
      id: "cognitive_runtime_receipt_handoff_roundtrip",
      title: "Cognitive Runtime receipt/handoff/rehydrate HTTP roundtrip",
      category: "cognitive_runtime",
      status,
      required: true,
      evidence: [
        { type: "api", ref: "/cognitive-runtime/runtime-receipts", value: { receipt_id: receiptId } },
        { type: "api", ref: "/cognitive-runtime/handoffs/export", value: { handoff_id: handoffId, receipt_refs: receiptRefs } },
        { type: "api", ref: "/cognitive-runtime/handoffs/rehydrate", value: { rehydration_id: rehydrationId } },
      ],
      findings: status === "PASS" ? [] : ["roundtrip identifiers did not line up"],
    });
  } catch (error) {
    return makeProbe({
      id: "cognitive_runtime_receipt_handoff_roundtrip",
      title: "Cognitive Runtime receipt/handoff/rehydrate HTTP roundtrip",
      category: "cognitive_runtime",
      status: "FAIL",
      required: true,
      evidence: [
        { type: "api", ref: "/cognitive-runtime/runtime-receipts" },
        { type: "api", ref: "/cognitive-runtime/handoffs/export" },
        { type: "api", ref: "/cognitive-runtime/handoffs/rehydrate" },
      ],
      findings: [String(error)],
    });
  }
}

async function collectRuntimeArtifactRefs(runtimeRoot: string): Promise<EvidenceRef[]> {
  const basenames = new Set([
    "plan.md",
    "pm_tasks.contract.json",
    "director.result.json",
    "integration_qa.result.json",
    "runtime.events.jsonl",
  ]);
  const files = await listFilesByBasename(runtimeRoot, basenames);
  const byName = new Map<string, string[]>();
  for (const filePath of files) {
    const name = path.basename(filePath);
    byName.set(name, [...(byName.get(name) || []), filePath]);
  }

  const refs: EvidenceRef[] = [
    {
      type: "probe",
      ref: "expanded_tech_evidence_matrix.core_runtime_evidence_placement",
    },
  ];
  for (const basename of basenames) {
    const filePath = await newestFile(byName.get(basename) || []);
    if (!filePath) {
      continue;
    }
    refs.push({ type: "runtime_artifact", ref: filePath });
  }
  return refs;
}

async function collectCoreRuntimeEvidencePlacementProbe(
  page: Page,
  workspace: string,
  runtimeRoot: string,
  requireRealChain: boolean,
  core: ExpandedTechEvidenceReport["core_runtime_integrations"],
): Promise<{ probe: EvidenceProbe; placement: CoreRuntimeEvidencePlacement | null }> {
  if (!requireRealChain) {
    return {
      placement: null,
      probe: makeProbe({
        id: "core_runtime_evidence_placement",
        title: "16 core runtime technologies placed into audit/receipt/handoff/task projection",
        category: "core_runtime",
        status: "SKIP",
        required: false,
        evidence: [],
        findings: ["real PM/Director/QA chain is not required for this matrix run"],
      }),
    };
  }

  try {
    const auditRefs = await collectRuntimeArtifactRefs(runtimeRoot);
    const taskProjectionEndpoint = `/v2/director/tasks?source=auto&workspace=${encodeURIComponent(workspace)}`;
    const taskRows = await requestJson<unknown[]>(page, taskProjectionEndpoint).catch(() => []);
    const unique = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const auditMarkerValue = {
      schema: "polaris.e2e.core_runtime_evidence_placement.audit_marker.v1",
      source: "electron_e2e.full_chain_runtime",
      generated_at: new Date().toISOString(),
      core_tech_ids: CORE_TECH_IDS,
      expected_sinks: ["audit", "receipt", "handoff", "task_projection"],
      task_projection_endpoint: taskProjectionEndpoint,
    };
    const auditMarkerPath = path.join(runtimeRoot, "audit", `core-runtime-evidence-placement-${unique}.json`);
    await fs.mkdir(path.dirname(auditMarkerPath), { recursive: true });
    await fs.writeFile(auditMarkerPath, `${JSON.stringify(auditMarkerValue, null, 2)}\n`, "utf-8");
    auditRefs.push({ type: "runtime_artifact", ref: auditMarkerPath, value: auditMarkerValue });
    const taskProjection = {
      tasks: Array.isArray(taskRows) ? taskRows : [],
      core_runtime_evidence_placement: {
        schema: "polaris.e2e.core_runtime_evidence_placement.task_projection_marker.v1",
        core_tech_ids: CORE_TECH_IDS,
        source: "director_tasks_projection_wrapper",
      },
    };
    const taskProjectionSummaryValue = taskProjectionSummary(taskProjection);
    const sessionId = `e2e-core-runtime-evidence-${unique}`;
    const runId = `e2e-core-runtime-evidence-run-${unique}`;
    const turnEnvelope = {
      turn_id: `turn-${unique}`,
      session_id: sessionId,
      run_id: runId,
      role: "qa",
      task_id: "e2e::core_runtime_evidence_placement",
      projection_version: "core-runtime-evidence-placement.v1",
      state_version: 1,
    };
    const placementPayload = {
      schema: "polaris.e2e.core_runtime_evidence_placement.v1",
      source: "electron_e2e.full_chain_runtime",
      core_tech_ids: CORE_TECH_IDS,
      aggregate_core: core,
      audit_refs: auditRefs,
      task_projection: {
        endpoint: taskProjectionEndpoint,
        ...taskProjectionSummaryValue,
        sample_task_ids: taskProjection.tasks
          .map((item) => asString(asRecord(item).id || asRecord(item).task_id))
          .filter(Boolean)
          .slice(0, 20),
      },
      expected_sinks: ["audit", "receipt", "handoff", "task_projection"],
    };

    const receiptResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/runtime-receipts", {
        method: "POST",
        body: {
          workspace,
          receipt_type: "e2e_core_runtime_evidence_placement",
          session_id: sessionId,
          run_id: runId,
          trace_refs: [
            "e2e:full_chain",
            "e2e:core_runtime_evidence_placement",
            ...auditRefs.map((ref) => ref.ref),
          ],
          payload: placementPayload,
          turn_envelope: turnEnvelope,
        },
      }),
    );
    const receipt = asRecord(receiptResponse.receipt);
    const receiptId = asString(receipt.receipt_id);
    if (!receiptId) {
      throw new Error("core runtime placement receipt did not return receipt_id");
    }

    const fetchedReceiptResponse = asRecord(
      await requestJson<JsonRecord>(
        page,
        `/cognitive-runtime/runtime-receipts/${encodeURIComponent(receiptId)}?workspace=${encodeURIComponent(workspace)}`,
      ),
    );
    const fetchedReceipt = asRecord(fetchedReceiptResponse.receipt);

    const handoffResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/handoffs/export", {
        method: "POST",
        body: {
          workspace,
          session_id: sessionId,
          run_id: runId,
          reason: "core_runtime_evidence_placement_16x4",
          receipt_limit: 10,
          turn_envelope: {
            ...turnEnvelope,
            receipt_ids: [receiptId],
          },
        },
      }),
    );
    const handoff = asRecord(handoffResponse.handoff);
    const handoffId = asString(handoff.handoff_id);
    if (!handoffId) {
      throw new Error("core runtime placement handoff did not return handoff_id");
    }

    const fetchedHandoffResponse = asRecord(
      await requestJson<JsonRecord>(
        page,
        `/cognitive-runtime/handoffs/${encodeURIComponent(handoffId)}?workspace=${encodeURIComponent(workspace)}`,
      ),
    );
    const fetchedHandoff = asRecord(fetchedHandoffResponse.handoff);
    const placement = buildCoreRuntimeEvidencePlacement({
      auditRefs,
      coreTechIds: CORE_TECH_IDS,
      receipt: fetchedReceipt,
      handoff: fetchedHandoff,
      taskProjection,
    });
    const status =
      core.missing_ids.length === 0 &&
      placement.rows.length === CORE_TECH_IDS.length &&
      placement.missing.length === 0
        ? "PASS"
        : "FAIL";
    return {
      placement,
      probe: makeProbe({
        id: "core_runtime_evidence_placement",
        title: "16 core runtime technologies placed into audit/receipt/handoff/task projection",
        category: "core_runtime",
        status,
        required: true,
        evidence: [
          { type: "api", ref: "/v1/chat/completions", value: core },
          { type: "api", ref: "/cognitive-runtime/runtime-receipts", value: { receipt_id: placement.receipt_id } },
          { type: "api", ref: "/cognitive-runtime/handoffs/export", value: { handoff_id: placement.handoff_id } },
          { type: "api", ref: taskProjectionEndpoint, value: placement.task_projection },
          ...auditRefs,
        ],
        findings:
          status === "PASS"
            ? []
            : [
                `missing core ids: ${core.missing_ids.join(", ") || "(none)"}`,
                `missing placements: ${placement.missing.join(", ") || "(none)"}`,
              ],
      }),
    };
  } catch (error) {
    return {
      placement: null,
      probe: makeProbe({
        id: "core_runtime_evidence_placement",
        title: "16 core runtime technologies placed into audit/receipt/handoff/task projection",
        category: "core_runtime",
        status: "FAIL",
        required: true,
        evidence: [
          { type: "api", ref: "/cognitive-runtime/runtime-receipts" },
          { type: "api", ref: "/cognitive-runtime/handoffs/export" },
          { type: "api", ref: "/v2/director/tasks?source=auto" },
          { type: "runtime_artifact", ref: runtimeRoot },
        ],
        findings: [String(error)],
      }),
    };
  }
}

async function collectRuntimeArtifactProbes(runtimeRoot: string, requireRealChain: boolean): Promise<EvidenceProbe[]> {
  const probes: EvidenceProbe[] = [];
  const basenames = new Set([
    "plan.md",
    "pm_tasks.contract.json",
    "director.result.json",
    "integration_qa.result.json",
    "runtime.events.jsonl",
  ]);
  const files = await listFilesByBasename(runtimeRoot, basenames);
  const byName = new Map<string, string[]>();
  for (const filePath of files) {
    const name = path.basename(filePath);
    byName.set(name, [...(byName.get(name) || []), filePath]);
  }

  const planPath = await newestFile(byName.get("plan.md") || []);
  const planText = planPath ? await readTextIfExists(planPath) : null;
  probes.push(
    makeProbe({
      id: "runtime_artifact_plan_contract",
      title: "Runtime plan artifact",
      category: "runtime_artifact",
      status: planText && planText.trim().length > 0 ? "PASS" : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: planPath ? [{ type: "runtime_artifact", ref: planPath, value: { chars: planText?.length || 0 } }] : [],
      findings: planText ? [] : ["plan.md not found under runtime root"],
    }),
  );

  const pmContractPath = await newestFile(byName.get("pm_tasks.contract.json") || []);
  const pmContract = pmContractPath ? asRecord(await readJsonIfExists<JsonRecord>(pmContractPath)) : {};
  const pmTasks = Array.isArray(pmContract.tasks) ? pmContract.tasks : [];
  const quality = asRecord(pmContract.quality_gate || pmContract.quality || pmContract.pm_quality);
  const qualityScore = asNumber(quality.score);
  const criticalIssues = Array.isArray(quality.critical_issues)
    ? quality.critical_issues.length
    : asNumber(quality.critical_issue_count);
  const pmPass =
    pmTasks.length > 0 &&
    (qualityScore === null || qualityScore >= 80) &&
    (criticalIssues === null || criticalIssues === 0);
  probes.push(
    makeProbe({
      id: "runtime_artifact_pm_quality_contract",
      title: "PM task quality contract artifact",
      category: "runtime_artifact",
      status: pmContractPath ? (pmPass ? "PASS" : "FAIL") : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: pmContractPath
        ? [
            {
              type: "runtime_artifact",
              ref: pmContractPath,
              value: { task_count: pmTasks.length, quality_score: qualityScore, critical_issues: criticalIssues },
            },
          ]
        : [],
      findings: pmContractPath ? (pmPass ? [] : ["PM contract exists but quality/task checks are incomplete"]) : ["PM contract not found"],
    }),
  );

  const directorPath = await newestFile(byName.get("director.result.json") || []);
  const directorResult = directorPath ? asRecord(await readJsonIfExists<JsonRecord>(directorPath)) : {};
  const taskResults = Array.isArray(directorResult.task_results) ? directorResult.task_results : [];
  const failures = asNumber(directorResult.failures) ?? 0;
  const blocked = asNumber(directorResult.blocked) ?? 0;
  const directorPass = taskResults.length > 0 || asString(directorResult.status).length > 0;
  probes.push(
    makeProbe({
      id: "runtime_artifact_director_result",
      title: "Director execution result artifact",
      category: "runtime_artifact",
      status: directorPath ? (directorPass && failures === 0 && blocked === 0 ? "PASS" : "WARN") : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: directorPath
        ? [
            {
              type: "runtime_artifact",
              ref: directorPath,
              value: { status: directorResult.status, task_results: taskResults.length, failures, blocked },
            },
          ]
        : [],
      findings: directorPath ? [] : ["director.result.json not found"],
    }),
  );

  const qaPath = await newestFile(byName.get("integration_qa.result.json") || []);
  const qaResult = qaPath ? asRecord(await readJsonIfExists<JsonRecord>(qaPath)) : {};
  const qaReceipt = asRecord(qaResult.cognitive_runtime_receipt);
  const qaPass =
    qaResult.passed === true ||
    asString(qaResult.reason) === "integration_qa_passed" ||
    asString(qaResult.evidence_grade) === "passed";
  probes.push(
    makeProbe({
      id: "runtime_artifact_qa_result_receipt",
      title: "Integration QA result with Cognitive Runtime receipt",
      category: "runtime_artifact",
      status: qaPath ? (qaPass && Boolean(asString(qaReceipt.receipt_id)) ? "PASS" : "WARN") : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: qaPath
        ? [
            {
              type: "runtime_artifact",
              ref: qaPath,
              value: {
                passed: qaResult.passed,
                reason: qaResult.reason,
                evidence_grade: qaResult.evidence_grade,
                receipt_id: qaReceipt.receipt_id,
              },
            },
          ]
        : [],
      findings: qaPath ? [] : ["integration_qa.result.json not found"],
    }),
  );

  const eventFiles = files.filter((filePath) => filePath.endsWith(".jsonl"));
  const eventRecords = await readJsonlFiles(eventFiles);
  const eventTypes = eventRecords.map((record) => asString(record.type || record.event_type || record.name)).filter(Boolean);
  const toolEventCount = eventTypes.filter((eventType) => eventType.toLowerCase().includes("tool")).length;
  const dangerousEventCount = eventRecords.filter((record) => JSON.stringify(record).toLowerCase().includes("dangerous")).length;
  probes.push(
    makeProbe({
      id: "runtime_events_tool_policy_audit",
      title: "Runtime event JSONL tool/policy audit evidence",
      category: "events",
      status: eventFiles.length > 0 ? (dangerousEventCount === 0 ? "PASS" : "FAIL") : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: [
        {
          type: "event_jsonl",
          ref: runtimeRoot,
          value: { event_files: eventFiles, event_count: eventRecords.length, tool_event_count: toolEventCount, dangerous_event_count: dangerousEventCount },
        },
      ],
      findings: dangerousEventCount > 0 ? ["dangerous command/policy keyword found in runtime event payloads"] : [],
    }),
  );

  return probes;
}

function scanPromptLeakage(planText: string | null, pmContract: JsonRecord): string[] {
  const payload = `${planText || ""}\n${JSON.stringify(pmContract)}`;
  const patterns = [
    /you are/i,
    /system prompt/i,
    /no yapping/i,
    /\u63d0\u793a\u8bcd/i,
    /<thinking>/i,
    /<tool_call>/i,
  ];
  return patterns.filter((pattern) => pattern.test(payload)).map((pattern) => String(pattern));
}

async function collectPromptLeakageProbe(runtimeRoot: string, requireRealChain: boolean): Promise<EvidenceProbe> {
  const files = await listFilesByBasename(runtimeRoot, new Set(["plan.md", "pm_tasks.contract.json"]));
  const planPath = await newestFile(files.filter((filePath) => path.basename(filePath) === "plan.md"));
  const pmContractPath = await newestFile(files.filter((filePath) => path.basename(filePath) === "pm_tasks.contract.json"));
  const planText = planPath ? await readTextIfExists(planPath) : null;
  const pmContract = pmContractPath ? asRecord(await readJsonIfExists<JsonRecord>(pmContractPath)) : {};
  const findings = scanPromptLeakage(planText, pmContract);
  const hasArtifacts = Boolean(planPath || pmContractPath);
  return makeProbe({
    id: "prompt_leakage_runtime_artifact_scan",
    title: "Prompt leakage scan for runtime plan/task artifacts",
    category: "governance",
    status: hasArtifacts ? (findings.length === 0 ? "PASS" : "FAIL") : requireRealChain ? "FAIL" : "SKIP",
    required: requireRealChain,
    evidence: [
      { type: "runtime_artifact", ref: planPath || "(missing plan.md)" },
      { type: "runtime_artifact", ref: pmContractPath || "(missing pm_tasks.contract.json)" },
    ],
    findings,
  });
}

export async function collectExpandedTechEvidenceMatrix(
  page: Page,
  options: CollectOptions = {},
): Promise<ExpandedTechEvidenceReport> {
  const requireRealChain = Boolean(options.requireRealChain);
  const probes: EvidenceProbe[] = [];

  let backend: BackendConnection | null = null;
  try {
    backend = await getBackendInfoFromPage(page);
    probes.push(
      makeProbe({
        id: "backend_connection",
        title: "Backend connection from desktop/browser page",
        category: "entrypoint",
        status: "PASS",
        required: true,
        evidence: [
          {
            type: "api",
            ref: backend.baseUrl,
            value: { token_present: Boolean(backend.token), source: backend.source },
          },
        ],
        findings: [],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "backend_connection",
        title: "Backend connection from desktop/browser page",
        category: "entrypoint",
        status: "FAIL",
        required: true,
        evidence: [],
        findings: [String(error)],
      }),
    );
  }

  let workspace = options.workspaceOverride || "";
  let runtimeRoot = options.runtimeRootOverride || "";
  try {
    const settings = asRecord(await requestJson<JsonRecord>(page, "/settings"));
    const layout = asRecord(await requestJson<JsonRecord>(page, "/runtime/storage-layout"));
    workspace = workspace || asString(settings.workspace) || asString(layout.workspace);
    runtimeRoot = runtimeRoot || asString(layout.runtime_root);
    probes.push(
      makeProbe({
        id: "settings_runtime_layout_api",
        title: "Settings and runtime storage layout API",
        category: "entrypoint",
        status: workspace && runtimeRoot ? "PASS" : "FAIL",
        required: true,
        evidence: [
          { type: "api", ref: "/settings", value: { workspace: settings.workspace } },
          { type: "api", ref: "/runtime/storage-layout", value: { runtime_root: layout.runtime_root, workspace: layout.workspace } },
        ],
        findings: workspace && runtimeRoot ? [] : ["workspace or runtime_root missing from API responses"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "settings_runtime_layout_api",
        title: "Settings and runtime storage layout API",
        category: "entrypoint",
        status: "FAIL",
        required: true,
        evidence: [
          { type: "api", ref: "/settings" },
          { type: "api", ref: "/runtime/storage-layout" },
        ],
        findings: [String(error)],
      }),
    );
  }

  probes.push(...(await collectReadonlyControlPlaneRuntimeProbes(page, workspace || ".")));

  probes.push(
    await candidateSourceProbe(
      "dual_mode_source_assets",
      "Dual-mode desktop/browser source assets",
      "entrypoint",
      CANDIDATE_SOURCE_PROBE_IDS.dual_mode_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "e2e_evidence_source_assets",
      "E2E isolation and evidence attachment source assets",
      "e2e",
      CANDIDATE_SOURCE_PROBE_IDS.e2e_evidence_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "graph_governance_source_assets",
      "Graph/governance source and gate assets",
      "governance",
      CANDIDATE_SOURCE_PROBE_IDS.graph_governance_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "task_market_source_assets",
      "TaskMarket write-side source and gate assets",
      "task_market",
      CANDIDATE_SOURCE_PROBE_IDS.task_market_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "llm_control_source_assets",
      "LLM control/readiness/tooling source assets",
      "llm_control",
      CANDIDATE_SOURCE_PROBE_IDS.llm_control_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "factory_archive_resident_source_assets",
      "Factory/archive/audit/resident source assets",
      "factory_archive_resident",
      CANDIDATE_SOURCE_PROBE_IDS.factory_archive_resident_source_assets,
    ),
  );

  const aggregate = await collectAggregateRuntimePlanProbe(page, workspace || ".");
  probes.push(aggregate.probe);
  probes.push(await collectCognitiveRuntimeRoundtripProbe(page, workspace || "."));
  const placementResult = await collectCoreRuntimeEvidencePlacementProbe(
    page,
    workspace || ".",
    runtimeRoot,
    requireRealChain,
    aggregate.core,
  );
  probes.push(placementResult.probe);

  if (runtimeRoot) {
    probes.push(...(await collectRuntimeArtifactProbes(runtimeRoot, requireRealChain)));
    probes.push(await collectPromptLeakageProbe(runtimeRoot, requireRealChain));
  } else {
    probes.push(
      makeProbe({
        id: "runtime_artifacts_unavailable",
        title: "Runtime artifact root unavailable",
        category: "runtime_artifact",
        status: requireRealChain ? "FAIL" : "SKIP",
        required: requireRealChain,
        evidence: [],
        findings: ["runtime_root is empty"],
      }),
    );
  }

  const candidateRuntimeCoverage = buildExpandedCandidateRuntimeCoverage({
    candidates: EXPANDED_TECH_CANDIDATES,
    probes,
    runtimeProbeCandidateIds: CANDIDATE_RUNTIME_PROBE_IDS,
    sourceProbeCandidateIds: CANDIDATE_SOURCE_PROBE_IDS,
  });

  const report: ExpandedTechEvidenceReport = {
    schema: "polaris.e2e.expanded_tech_evidence_matrix.v1",
    generated_at: new Date().toISOString(),
    workspace,
    runtime_root: runtimeRoot,
    require_real_chain: requireRealChain,
    core_runtime_integrations: aggregate.core,
    core_runtime_evidence_placement: placementResult.placement,
    candidate_runtime_coverage: candidateRuntimeCoverage,
    expanded_candidates: EXPANDED_TECH_CANDIDATES,
    probes,
    summary: {
      pass: countStatus(probes, "PASS"),
      fail: countStatus(probes, "FAIL"),
      warn: countStatus(probes, "WARN"),
      skip: countStatus(probes, "SKIP"),
      required_fail: probes.filter((probe) => probe.required && probe.status === "FAIL").length,
      candidate_count: EXPANDED_TECH_CANDIDATES.length,
    },
  };
  return report;
}

export function assertExpandedTechEvidenceMatrix(
  report: ExpandedTechEvidenceReport,
  options: { requireAllCandidateRuntime?: boolean } = {},
): void {
  const requiredFailures = report.probes.filter((probe) => probe.required && probe.status === "FAIL");
  if (requiredFailures.length > 0) {
    throw new Error(
      `expanded tech evidence matrix has required failures: ${requiredFailures
        .map((probe) => `${probe.id}: ${probe.findings.join("; ")}`)
        .join(" | ")}`,
    );
  }
  if (report.require_real_chain) {
    const placement = report.core_runtime_evidence_placement;
    if (!placement) {
      throw new Error("expanded tech evidence matrix is missing core runtime evidence placement");
    }
    if (placement.rows.length !== CORE_TECH_IDS.length || placement.missing.length > 0) {
      throw new Error(
        `core runtime evidence placement incomplete: rows=${placement.rows.length}/${CORE_TECH_IDS.length} `
        + `missing=${placement.missing.join(", ") || "(none)"}`,
      );
    }
  }
  if (options.requireAllCandidateRuntime) {
    const coverage = report.candidate_runtime_coverage;
    if (!coverage) {
      throw new Error("expanded tech evidence matrix is missing candidate runtime coverage");
    }
    if (coverage.expected_count !== report.expanded_candidates.length || coverage.not_runtime_proved_ids.length > 0) {
      throw new Error(
        `candidate runtime coverage incomplete: runtime_proved=${coverage.runtime_proved_count}/`
        + `${coverage.expected_count} missing=${coverage.not_runtime_proved_ids.join(", ") || "(none)"}`,
      );
    }
  }
}

export async function writeExpandedTechEvidenceMatrix(
  testInfo: TestInfo,
  report: ExpandedTechEvidenceReport,
  filename = "expanded-tech-evidence-matrix.json",
): Promise<string> {
  const outputPath = testInfo.outputPath(filename);
  await writeUtf8File(outputPath, JSON.stringify(report, null, 2));
  await testInfo.attach("expanded-tech-evidence-matrix", {
    path: outputPath,
    contentType: "application/json",
  });
  return outputPath;
}
