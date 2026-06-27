/**
 * Platform control-plane ledger projection types.
 *
 * Run Ledger is core Polaris infrastructure. Internal stress harnesses are only
 * producers/consumers of this read model; formal workspaces should import these
 * platform types directly instead of depending on test-harness services.
 */

import { apiGet, apiPost } from './apiClient';
import type { ApiResult } from './api.types';

export interface ControlPlaneEvidencePolicy {
  ok: boolean;
  enabled_modalities?: string[];
  required_modalities: string[];
  missing_required_modalities: string[];
  failed_required_modalities?: string[];
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
  compat_ledgers_included: boolean;
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function runtimeEnvelopeFromMessage(message: unknown): Record<string, unknown> | null {
  if (!isRecord(message)) return null;
  if (
    message.type === 'EVENT' &&
    message.protocol === 'runtime.v2' &&
    isRecord(message.event)
  ) {
    return message.event;
  }
  return message;
}

export function controlPlaneProjectionFromRuntimeMessage(
  message: unknown
): ControlPlaneProjection | null {
  const envelope = runtimeEnvelopeFromMessage(message);
  if (!envelope) return null;
  if (String(envelope.channel || '').trim() !== 'status.control_plane') return null;

  const payload = isRecord(envelope.payload) ? envelope.payload : null;
  const projection = isRecord(payload?.projection)
    ? payload.projection
    : isRecord(envelope.projection)
      ? envelope.projection
      : null;
  if (!projection) return null;
  if (String(projection.source || '').trim() !== 'run_ledger_projection') return null;
  if (!Array.isArray(projection.projects)) return null;
  return projection as unknown as ControlPlaneProjection;
}

export interface VerifierCapabilityStatus {
  enabled: boolean;
  required: boolean;
  available: boolean;
  reason: string;
}

export interface VerifierPolicyScript {
  id: string;
  path: string;
  modality: string;
  enabled: boolean;
  required: boolean;
}

export interface VerifierPolicy {
  schema_version: number;
  source: string;
  workspace: string;
  config_path: string;
  enabled_modalities: string[];
  required_modalities: string[];
  custom_scripts: VerifierPolicyScript[];
  capabilities: Record<'browser' | 'visual' | 'llm_judge' | 'custom_script', VerifierCapabilityStatus>;
  environment: Record<string, { available: boolean; reason: string }>;
  safety: {
    optional_by_default: boolean;
    internal_harness_owned: boolean;
    executes_verifiers: boolean;
    requires_explicit_user_enablement: boolean;
  };
}

export interface UpdateVerifierPolicyPayload {
  browser_enabled?: boolean;
  visual_enabled?: boolean;
  llm_judge_enabled?: boolean;
  custom_script_enabled?: boolean;
  required_modalities?: string[];
  custom_scripts?: VerifierPolicyScript[];
}

export async function getControlPlaneProjection(options: {
  workspace?: string;
  runId?: string;
  maxRuns?: number;
} = {}): Promise<ApiResult<ControlPlaneProjection>> {
  const params = new URLSearchParams();
  if (options.workspace) params.set('workspace', options.workspace);
  if (options.runId) params.set('run_id', options.runId);
  if (options.maxRuns !== undefined) params.set('max_runs', String(options.maxRuns));
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return apiGet<ControlPlaneProjection>(
    `/v2/control-plane/ledger/projection${suffix}`,
    '获取 Control Plane 账本投影失败'
  );
}

export async function getVerifierPolicy(options: {
  workspace?: string;
} = {}): Promise<ApiResult<VerifierPolicy>> {
  const params = new URLSearchParams();
  if (options.workspace) params.set('workspace', options.workspace);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return apiGet<VerifierPolicy>(
    `/v2/control-plane/verifier-policy${suffix}`,
    '获取 Control Plane 验收策略失败'
  );
}

export async function updateVerifierPolicy(
  payload: UpdateVerifierPolicyPayload,
  options: { workspace?: string } = {}
): Promise<ApiResult<VerifierPolicy>> {
  const params = new URLSearchParams();
  if (options.workspace) params.set('workspace', options.workspace);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return apiPost<VerifierPolicy>(
    `/v2/control-plane/verifier-policy${suffix}`,
    payload,
    '保存 Control Plane 验收策略失败'
  );
}
