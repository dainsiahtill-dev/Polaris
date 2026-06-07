import { expect, test } from "@playwright/test";
import {
  buildCoreRuntimeEvidencePlacement,
  buildExpandedCandidateRuntimeCoverage,
  CANDIDATE_RUNTIME_PROBE_IDS,
  CORE_TECH_IDS,
  assertExpandedTechEvidenceMatrix,
  findRoleSessionKernelAuditEvent,
  type ExpandedTechCandidate,
  type EvidenceRef,
  type EvidenceProbe,
  resolveBackendInfoSnapshot,
} from "./helpers/expandedTechEvidenceMatrix";

test("builds 16-by-4 core runtime evidence placement rows", () => {
  const receiptId = "receipt-core-placement";
  const auditRefs: EvidenceRef[] = [
    { type: "runtime_artifact", ref: "full-chain-audit.json", value: { core_tech_ids: CORE_TECH_IDS } },
    {
      type: "runtime_artifact",
      ref: "full-chain-expanded-tech-evidence-matrix.json",
      value: { core_runtime_evidence_placement: { core_tech_ids: CORE_TECH_IDS } },
    },
  ];
  const placement = buildCoreRuntimeEvidencePlacement({
    auditRefs,
    coreTechIds: CORE_TECH_IDS,
    handoff: {
      handoff_id: "handoff-core-placement",
      receipt_refs: [receiptId],
      turn_envelope: { receipt_ids: [receiptId] },
    },
    receipt: {
      receipt_id: receiptId,
      payload: {
        schema: "polaris.e2e.core_runtime_evidence_placement.v1",
        core_tech_ids: CORE_TECH_IDS,
      },
    },
    taskProjection: {
      tasks: [
        {
          id: "director-task-1",
          pm_task_id: "pm-task-1",
          metadata: {
            pm_task_id: "pm-task-1",
            projection_source: "director_merged",
            core_tech_ids: CORE_TECH_IDS,
          },
        },
      ],
    },
  });

  expect(placement.expected_sinks).toEqual(["audit", "receipt", "handoff", "task_projection"]);
  expect(placement.rows).toHaveLength(16);
  expect(placement.missing).toEqual([]);
  for (const row of placement.rows) {
    expect(row.sinks.audit.present, row.tech_id).toBe(true);
    expect(row.sinks.receipt.present, row.tech_id).toBe(true);
    expect(row.sinks.handoff.present, row.tech_id).toBe(true);
    expect(row.sinks.task_projection.present, row.tech_id).toBe(true);
  }
});

test("reports missing handoff and task projection sinks per core technology", () => {
  const placement = buildCoreRuntimeEvidencePlacement({
    auditRefs: [{ type: "runtime_artifact", ref: "full-chain-audit.json", value: { core_tech_ids: CORE_TECH_IDS } }],
    coreTechIds: CORE_TECH_IDS,
    handoff: { handoff_id: "handoff-without-receipt", receipt_refs: [] },
    receipt: {
      receipt_id: "receipt-core-placement",
      payload: {
        schema: "polaris.e2e.core_runtime_evidence_placement.v1",
        core_tech_ids: CORE_TECH_IDS,
      },
    },
    taskProjection: { tasks: [] },
  });

  expect(placement.rows).toHaveLength(16);
  expect(placement.missing).toHaveLength(32);
  expect(placement.missing).toContain("acga_graph_cell_governance:handoff");
  expect(placement.missing).toContain("acga_graph_cell_governance:task_projection");
});

test("does not treat generic audit refs or PM-linked tasks as per-tech placement evidence", () => {
  const receiptId = "receipt-generic-placement";
  const placement = buildCoreRuntimeEvidencePlacement({
    auditRefs: [{ type: "runtime_artifact", ref: "full-chain-audit.json" }],
    coreTechIds: CORE_TECH_IDS,
    handoff: {
      handoff_id: "handoff-generic-placement",
      receipt_refs: [receiptId],
      turn_envelope: { receipt_ids: [receiptId] },
    },
    receipt: {
      receipt_id: receiptId,
      payload: {
        schema: "polaris.e2e.core_runtime_evidence_placement.v1",
        core_tech_ids: CORE_TECH_IDS,
      },
    },
    taskProjection: {
      tasks: [
        {
          id: "director-task-1",
          metadata: {
            pm_task_id: "pm-task-1",
            projection_source: "director_merged",
          },
        },
      ],
    },
  });

  expect(placement.missing).toContain("acga_graph_cell_governance:audit");
  expect(placement.missing).toContain("acga_graph_cell_governance:task_projection");
  expect(placement.rows[0].sinks.receipt.present).toBe(true);
  expect(placement.rows[0].sinks.handoff.present).toBe(true);
});

test("resolves browser backend info without Electron preload", () => {
  const backend = resolveBackendInfoSnapshot({
    devBackend: {
      baseUrl: "http://127.0.0.1:49977/",
      token: "browser-token",
    },
  });

  expect(backend).toEqual({
    baseUrl: "http://127.0.0.1:49977",
    token: "browser-token",
    source: "browser_dev_backend",
  });
});

test("real-chain matrix assertion requires core runtime evidence placement", () => {
  expect(() =>
    assertExpandedTechEvidenceMatrix({
      schema: "polaris.e2e.expanded_tech_evidence_matrix.v1",
      generated_at: "2026-06-07T00:00:00.000Z",
      workspace: "/tmp/workspace",
      runtime_root: "/tmp/runtime",
      require_real_chain: true,
      core_runtime_integrations: {
        expected_count: 16,
        actual_count: 16,
        entrypoints_verified_count: 16,
        missing_ids: [],
        unexpected_ids: [],
      },
      core_runtime_evidence_placement: null,
      candidate_runtime_coverage: {
        schema: "polaris.e2e.expanded_candidate_runtime_coverage.v1",
        expected_count: 0,
        runtime_proved_count: 0,
        source_proved_count: 0,
        gate_declared_count: 0,
        declared_only_count: 0,
        runtime_required_count: 0,
        missing_runtime_ids: [],
        not_runtime_proved_ids: [],
        rows: [],
      },
      expanded_candidates: [],
      probes: [],
      summary: {
        pass: 0,
        fail: 0,
        warn: 0,
        skip: 0,
        required_fail: 0,
        candidate_count: 0,
      },
    }),
  ).toThrow(/missing core runtime evidence placement/);
});

test("candidate runtime coverage separates runtime proof from source and gate declarations", () => {
  const candidates: ExpandedTechCandidate[] = [
    {
      id: "runtime_candidate",
      title: "Runtime Candidate",
      category: "runtime",
      status: "implemented",
      source: "test",
      paths: ["runtime.py"],
      gates: ["runtime gate"],
      e2eFields: ["runtime.value"],
    },
    {
      id: "source_candidate",
      title: "Source Candidate",
      category: "source",
      status: "implemented",
      source: "test",
      paths: ["source.py"],
      gates: ["source gate"],
      e2eFields: ["source.value"],
    },
    {
      id: "gate_candidate",
      title: "Gate Candidate",
      category: "governance",
      status: "gate",
      source: "test",
      paths: ["gate.py"],
      gates: ["governance gate"],
      e2eFields: ["gate.value"],
    },
  ];
  const probes: EvidenceProbe[] = [
    {
      id: "runtime_probe",
      title: "Runtime probe",
      category: "runtime",
      status: "PASS",
      required: true,
      evidence: [{ type: "api", ref: "/runtime" }],
      findings: [],
    },
    {
      id: "source_probe",
      title: "Source probe",
      category: "source",
      status: "PASS",
      required: true,
      evidence: [{ type: "repo_path", ref: "source.py" }],
      findings: [],
    },
  ];

  const coverage = buildExpandedCandidateRuntimeCoverage({
    candidates,
    probes,
    runtimeProbeCandidateIds: { runtime_probe: ["runtime_candidate"] },
    sourceProbeCandidateIds: { source_probe: ["source_candidate"] },
  });

  expect(coverage.expected_count).toBe(3);
  expect(coverage.runtime_proved_count).toBe(1);
  expect(coverage.source_proved_count).toBe(1);
  expect(coverage.gate_declared_count).toBe(1);
  expect(coverage.runtime_required_count).toBe(2);
  expect(coverage.missing_runtime_ids).toEqual(["source_candidate"]);
  expect(coverage.not_runtime_proved_ids).toEqual(["source_candidate", "gate_candidate"]);
  expect(coverage.rows.map((row) => [row.candidate_id, row.coverage_status])).toEqual([
    ["runtime_candidate", "runtime_proved"],
    ["source_candidate", "source_proved"],
    ["gate_candidate", "gate_declared"],
  ]);
});

test("candidate runtime coverage maps runtime isolation and history archive probes by default", () => {
  const candidates: ExpandedTechCandidate[] = [
    {
      id: "e2e_fixture_isolated_home_runtime_workspace",
      title: "E2E fixture isolated home/runtime workspace",
      category: "e2e",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/tests/electron"],
      gates: ["runtime isolation probe"],
      e2eFields: ["workspace", "runtime_root"],
    },
    {
      id: "e2e_automatic_evidence_attachments",
      title: "E2E automatic evidence attachments",
      category: "e2e",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/tests/electron/fixtures.ts", "src/backend/polaris/tests/electron/webFixtures.ts"],
      gates: ["Playwright testInfo.attach manifest"],
      e2eFields: ["attachment_manifest_path", "attachment_names"],
    },
    {
      id: "history_factory_overview_defect_loop_projection",
      title: "History factory overview defect-loop projection",
      category: "history",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/delivery/http/routers/history.py"],
      gates: ["GET /history/factory/overview"],
      e2eFields: ["summary", "rounds"],
    },
    {
      id: "immutable_archive_manifest_jsonl_index",
      title: "Immutable archive manifest/jsonl index",
      category: "archive",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/delivery/http/routers/history.py"],
      gates: ["GET /v2/history/runs", "GET /v2/history/tasks/snapshots"],
      e2eFields: ["runs", "snapshots", "factory_runs"],
    },
    {
      id: "resident_self_learning_tick",
      title: "Resident self-learning tick",
      category: "resident",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py"],
      gates: ["POST /v2/resident/tick", "GET /v2/resident/status", "GET /v2/resident/decisions"],
      e2eFields: ["runtime.tick_count", "counts.decisions", "counts.goals", "agenda.risk_register"],
    },
    {
      id: "resident_governed_goal_pm_bridge",
      title: "Resident governed goal PM bridge",
      category: "resident",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/cells/resident/autonomy/internal/pm_bridge.py"],
      gates: ["POST /v2/resident/goals/{id}/materialize", "POST /v2/resident/goals/{id}/run"],
      e2eFields: ["resident_goal_id", "pm_contract_path", "backup_manifest_path", "execution.stage"],
    },
    {
      id: "llm_interview_readiness_history",
      title: "LLM interview readiness history",
      category: "llm_control",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/cells/llm/evaluation/internal/interview.py"],
      gates: ["POST /v2/llm/interview/save"],
      e2eFields: ["saved", "report_path", "readiness_updated"],
    },
    {
      id: "llm_evaluation_index_dual_mirror_lock",
      title: "LLM evaluation index dual mirror lock",
      category: "llm_control",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/cells/llm/evaluation/internal/index.py"],
      gates: ["LLM test report index reconcile"],
      e2eFields: ["last_run_id", "timestamp", "last_update"],
    },
    {
      id: "kernel_audit_hash_chain_role_session_export",
      title: "Kernel audit hash chain role session export",
      category: "audit",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/cells/audit/evidence/internal/role_session_audit_service.py"],
      gates: ["RoleSessionAuditService.export_audit_log"],
      e2eFields: ["prev_hash", "signature", "event_id", "chain_valid"],
    },
    {
      id: "websocket_stale_token_recovery",
      title: "WebSocket stale token recovery",
      category: "runtime",
      status: "implemented",
      source: "test",
      paths: ["src/frontend/src/api.ts", "src/backend/polaris/delivery/ws/endpoints/websocket_core.py"],
      gates: ["invalid token closes with 1008", "fresh token receives runtime status"],
      e2eFields: ["stale_close_code", "fresh_status_type", "token_refreshed"],
    },
    {
      id: "electron_backend_supervisor_chain",
      title: "Electron backend supervisor chain",
      category: "entrypoint",
      status: "implemented",
      source: "test",
      paths: ["src/electron/main.cjs"],
      gates: ["window.polaris.getBackendStatus"],
      e2eFields: ["backend_status.ready", "backend_status.info.baseUrl"],
    },
    {
      id: "electron_preload_ipc_contract",
      title: "Electron preload IPC contract",
      category: "entrypoint",
      status: "implemented",
      source: "test",
      paths: ["src/electron/preload.cjs"],
      gates: ["window.polaris.getBackendInfo"],
      e2eFields: ["window.polaris keys", "backend_source=electron_preload"],
    },
    {
      id: "electron_secret_safe_storage",
      title: "Electron safeStorage secret bridge",
      category: "security",
      status: "implemented",
      source: "test",
      paths: ["src/electron/main.cjs", "src/electron/preload.cjs"],
      gates: ["window.polaris.secrets set/get/remove"],
      e2eFields: ["available", "set_ok", "readback_ok", "remove_ok"],
    },
    {
      id: "electron_pty_bridge",
      title: "Electron PTY bridge",
      category: "tooling",
      status: "implemented",
      source: "test",
      paths: ["src/electron/main.cjs", "src/electron/preload.cjs"],
      gates: ["window.polaris.pty start/output/resize/close"],
      e2eFields: ["session_id", "output_marker", "resize_ok", "close_ok"],
    },
    {
      id: "subgraph_truth_vs_draft_reconciliation",
      title: "Subgraph truth/draft reconciliation",
      category: "governance",
      status: "gate",
      source: "test",
      paths: ["src/backend/docs/graph/catalog/cells.yaml", "src/backend/docs/graph/subgraphs"],
      gates: ["catalog references are backed by subgraph YAML"],
      e2eFields: ["catalog_refs_missing_yaml", "draft_subgraphs"],
    },
    {
      id: "structural_bug_governance_chain",
      title: "Structural bug governance chain",
      category: "governance",
      status: "gate",
      source: "test",
      paths: ["src/backend/docs/governance/debt.register.yaml", "src/backend/polaris/cells/roles/kernel/generated/verify.pack.json"],
      gates: ["debt register + verify pack + ADR + schema closed loop"],
      e2eFields: ["asset_count", "missing_assets"],
    },
    {
      id: "semantic_boundary_governance_gate",
      title: "Semantic boundary governance gate",
      category: "governance",
      status: "gate",
      source: "test",
      paths: ["src/backend/docs/governance/ci/scripts/check_semantic_boundary.py"],
      gates: ["python docs/governance/ci/scripts/check_semantic_boundary.py"],
      e2eFields: ["total_sites", "compliant_sites", "status_line"],
    },
    {
      id: "event_fact_stream_singleton_writer",
      title: "Event fact stream singleton writer",
      category: "events",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/cells/events/fact_stream/public/service.py"],
      gates: ["POST /v2/runtime/fact-stream/probe"],
      e2eFields: ["event_id", "storage_path", "queried_total", "artifact_exists"],
    },
    {
      id: "kernelone_traceability_matrix",
      title: "KernelOne traceability matrix",
      category: "governance",
      status: "implemented",
      source: "test",
      paths: ["src/backend/polaris/kernelone/traceability"],
      gates: ["POST /v2/runtime/traceability/probe"],
      e2eFields: ["matrix_id", "node_count", "link_count", "artifact_exists"],
    },
  ];
  const probes: EvidenceProbe[] = [
    {
      id: "e2e_runtime_isolation_probe",
      title: "E2E runtime isolation probe",
      category: "e2e",
      status: "PASS",
      required: false,
      evidence: [{ type: "probe", ref: "workspace_runtime_isolation" }],
      findings: [],
    },
    {
      id: "e2e_attachment_runtime_probe",
      title: "E2E automatic attachment runtime probe",
      category: "e2e",
      status: "PASS",
      required: false,
      evidence: [{ type: "runtime_artifact", ref: "e2e-auto-attachment-manifest.json" }],
      findings: [],
    },
    {
      id: "history_archive_readonly_runtime_probe",
      title: "History/archive read-only runtime probe",
      category: "history",
      status: "PASS",
      required: false,
      evidence: [{ type: "api", ref: "/v2/history/runs" }],
      findings: [],
    },
    {
      id: "resident_self_learning_runtime_probe",
      title: "Resident self-learning runtime probe",
      category: "resident",
      status: "PASS",
      required: false,
      evidence: [{ type: "api", ref: "/v2/resident/tick" }],
      findings: [],
    },
    {
      id: "resident_goal_pm_bridge_runtime_probe",
      title: "Resident governed goal PM bridge runtime probe",
      category: "resident",
      status: "PASS",
      required: false,
      evidence: [{ type: "api", ref: "/v2/resident/goals/{id}/stage" }],
      findings: [],
    },
    {
      id: "llm_interview_save_runtime_probe",
      title: "LLM interview save runtime probe",
      category: "llm_control",
      status: "PASS",
      required: false,
      evidence: [{ type: "api", ref: "/v2/llm/interview/save" }],
      findings: [],
    },
    {
      id: "role_session_audit_export_runtime_probe",
      title: "Role session audit export runtime probe",
      category: "audit",
      status: "PASS",
      required: false,
      evidence: [{ type: "api", ref: "/v2/roles/sessions/{id}/audit/export" }],
      findings: [],
    },
    {
      id: "websocket_stale_token_runtime_probe",
      title: "WebSocket stale token runtime probe",
      category: "runtime",
      status: "PASS",
      required: false,
      evidence: [{ type: "probe", ref: "/v2/ws/runtime" }],
      findings: [],
    },
    {
      id: "electron_preload_supervisor_runtime_probe",
      title: "Electron preload/supervisor runtime probe",
      category: "entrypoint",
      status: "PASS",
      required: false,
      evidence: [{ type: "probe", ref: "window.polaris.getBackendStatus" }],
      findings: [],
    },
    {
      id: "electron_secret_safe_storage_runtime_probe",
      title: "Electron safeStorage runtime probe",
      category: "security",
      status: "PASS",
      required: false,
      evidence: [{ type: "probe", ref: "window.polaris.secrets" }],
      findings: [],
    },
    {
      id: "electron_pty_runtime_probe",
      title: "Electron PTY runtime probe",
      category: "tooling",
      status: "PASS",
      required: false,
      evidence: [{ type: "probe", ref: "window.polaris.pty" }],
      findings: [],
    },
    {
      id: "graph_subgraph_reconciliation_runtime_probe",
      title: "Graph subgraph reconciliation runtime probe",
      category: "governance",
      status: "PASS",
      required: false,
      evidence: [{ type: "runtime_artifact", ref: "src/backend/docs/graph/catalog/cells.yaml" }],
      findings: [],
    },
    {
      id: "structural_bug_governance_runtime_probe",
      title: "Structural bug governance runtime probe",
      category: "governance",
      status: "PASS",
      required: false,
      evidence: [{ type: "runtime_artifact", ref: "src/backend/docs/governance/debt.register.yaml" }],
      findings: [],
    },
    {
      id: "event_fact_stream_runtime_probe",
      title: "Event fact stream runtime probe",
      category: "events",
      status: "PASS",
      required: false,
      evidence: [{ type: "api", ref: "/v2/runtime/fact-stream/probe" }],
      findings: [],
    },
    {
      id: "semantic_boundary_governance_runtime_probe",
      title: "Semantic boundary governance runtime probe",
      category: "governance",
      status: "PASS",
      required: false,
      evidence: [{ type: "probe", ref: "python docs/governance/ci/scripts/check_semantic_boundary.py" }],
      findings: [],
    },
    {
      id: "kernelone_traceability_runtime_probe",
      title: "KernelOne traceability runtime probe",
      category: "governance",
      status: "PASS",
      required: false,
      evidence: [{ type: "api", ref: "/v2/runtime/traceability/probe" }],
      findings: [],
    },
  ];

  const coverage = buildExpandedCandidateRuntimeCoverage({
    candidates,
    probes,
    runtimeProbeCandidateIds: CANDIDATE_RUNTIME_PROBE_IDS,
    sourceProbeCandidateIds: {},
  });

  expect(coverage.runtime_proved_count).toBe(19);
  expect(coverage.missing_runtime_ids).toEqual([]);
  expect(coverage.not_runtime_proved_ids).toEqual([]);
});

test("role-session kernel audit matching accepts canonical and raw audit JSONL records", () => {
  const sessionId = "session-123";
  const rawEvent = {
    event_id: "event-123",
    event_type: "role.message_sent",
    task: {
      task_id: `role-session-${sessionId}`,
      run_id: `role-session-${sessionId}`,
    },
    data: {
      session_id: sessionId,
    },
    prev_hash: "previous-hash",
    signature: "signature-value",
  };

  expect(
    findRoleSessionKernelAuditEvent(
      [
        { event_id: "unrelated", task: { task_id: "other" }, data: { session_id: "other" } },
        { channel: "system", raw: rawEvent },
      ],
      sessionId,
    ),
  ).toMatchObject({
    canonicalWrapped: true,
    rawEvent,
  });

  expect(findRoleSessionKernelAuditEvent([rawEvent], sessionId)).toMatchObject({
    canonicalWrapped: false,
    rawEvent,
  });
});

test("strict matrix assertion rejects implemented candidates without runtime proof", () => {
  expect(() =>
    assertExpandedTechEvidenceMatrix(
      {
        schema: "polaris.e2e.expanded_tech_evidence_matrix.v1",
        generated_at: "2026-06-07T00:00:00.000Z",
        workspace: "/tmp/workspace",
        runtime_root: "/tmp/runtime",
        require_real_chain: false,
        core_runtime_integrations: {
          expected_count: 16,
          actual_count: 16,
          entrypoints_verified_count: 16,
          missing_ids: [],
          unexpected_ids: [],
        },
        core_runtime_evidence_placement: null,
        candidate_runtime_coverage: {
          schema: "polaris.e2e.expanded_candidate_runtime_coverage.v1",
          expected_count: 2,
          runtime_proved_count: 1,
          source_proved_count: 1,
          gate_declared_count: 0,
          declared_only_count: 0,
          runtime_required_count: 2,
          missing_runtime_ids: ["source_candidate"],
          not_runtime_proved_ids: ["source_candidate"],
          rows: [],
        },
        expanded_candidates: [],
        probes: [],
        summary: {
          pass: 0,
          fail: 0,
          warn: 0,
          skip: 0,
          required_fail: 0,
          candidate_count: 0,
        },
      },
      { requireAllCandidateRuntime: true },
    ),
  ).toThrow(/candidate runtime coverage incomplete/);
});
