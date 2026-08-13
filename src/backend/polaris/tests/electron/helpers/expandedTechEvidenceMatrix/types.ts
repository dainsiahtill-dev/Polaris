import { execFile } from "node:child_process";
import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { type Page, type TestInfo } from "@playwright/test";

export type JsonRecord = Record<string, unknown>;

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

export type CollectOptions = {
  requireRealChain?: boolean;
  workspaceOverride?: string;
  runtimeRootOverride?: string;
};
