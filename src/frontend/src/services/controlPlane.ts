/**
 * Platform control-plane ledger projection types.
 *
 * Run Ledger is core Polaris infrastructure. Factory Bench is only one producer
 * and stress-test consumer of this read model; formal workspaces should import
 * these platform types directly instead of depending on bench-specific services.
 */

export interface ControlPlaneEvidencePolicy {
  ok: boolean;
  enabled_modalities?: string[];
  required_modalities: string[];
  missing_required_modalities: string[];
}

export interface ControlPlaneEvidenceModalitySummary {
  total: number;
  present: number;
  ok: number;
  failed: number;
  latest_detail: string;
}

export interface ControlPlaneProjectProjection {
  project_id: string;
  ok: boolean;
  integrity_ok: boolean;
  outcome_ok: boolean;
  gate_count: number;
  failed_gate_count: number;
  latest_token_id: string;
  detail: string;
  missing: string[];
  evidence_policy?: ControlPlaneEvidencePolicy;
  evidence_modalities?: Record<string, ControlPlaneEvidenceModalitySummary>;
}

export interface ControlPlaneProjection {
  schema_version: number;
  source: string;
  available: boolean;
  ok: boolean;
  status: string;
  audit_path: string;
  total: number;
  projected: number;
  missing: number;
  failed: number;
  projects: ControlPlaneProjectProjection[];
  goal_audit?: Record<string, unknown>;
  detail: string;
  evidence_policy?: ControlPlaneEvidencePolicy;
  evidence_modalities?: Record<string, ControlPlaneEvidenceModalitySummary>;
}
