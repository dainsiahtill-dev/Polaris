import { expect, test } from "@playwright/test";
import {
  buildCoreRuntimeEvidencePlacement,
  buildExpandedCandidateRuntimeCoverage,
  CANDIDATE_RUNTIME_PROBE_IDS,
  CORE_TECH_IDS,
  assertExpandedTechEvidenceMatrix,
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
      id: "history_archive_readonly_runtime_probe",
      title: "History/archive read-only runtime probe",
      category: "history",
      status: "PASS",
      required: false,
      evidence: [{ type: "api", ref: "/v2/history/runs" }],
      findings: [],
    },
  ];

  const coverage = buildExpandedCandidateRuntimeCoverage({
    candidates,
    probes,
    runtimeProbeCandidateIds: CANDIDATE_RUNTIME_PROBE_IDS,
    sourceProbeCandidateIds: {},
  });

  expect(coverage.runtime_proved_count).toBe(3);
  expect(coverage.missing_runtime_ids).toEqual([]);
  expect(coverage.not_runtime_proved_ids).toEqual([]);
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
