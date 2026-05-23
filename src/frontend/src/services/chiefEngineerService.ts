import { apiGet, apiPost } from './apiClient';
import type { ApiResult } from './api.types';
import type {
  ChiefEngineerBlueprintDetailV1,
  ChiefEngineerBlueprintListV1,
} from '@/types/roleContracts';

export type ChiefEngineerBlueprintListResponse = ChiefEngineerBlueprintListV1;
export type ChiefEngineerBlueprintDetailResponse = ChiefEngineerBlueprintDetailV1;

export interface ChiefEngineerDiagnosticsWorkspaceStatus {
  ok: boolean;
  status: string;
  workspace: string;
  exists: boolean;
  error: string | null;
}

export interface ChiefEngineerDiagnosticsBlueprintStatus {
  ok: boolean;
  status: string;
  source: string;
  total: number;
  loadable: number;
  invalid_payloads: number;
  director_handoff_ready: boolean;
  latest_updated_at: string | null;
  error: string | null;
}

export interface ChiefEngineerDiagnosticsResponse {
  ok: boolean;
  role: 'chief_engineer';
  generated_at: string;
  workspace: ChiefEngineerDiagnosticsWorkspaceStatus;
  blueprints: ChiefEngineerDiagnosticsBlueprintStatus;
  issues: string[];
}

export interface GenerateChiefEngineerBlueprintPayload {
  task_id: string;
  objective: string;
  run_id?: string | null;
  constraints?: Record<string, unknown>;
  context?: Record<string, unknown>;
}

export interface ChiefEngineerTaskBlueprintResultResponse {
  ok: boolean;
  task_id: string;
  workspace: string;
  status: string;
  blueprint_id: string | null;
  blueprint_path: string | null;
  source: string;
  summary: string;
  recommendations: string[];
  risks: string[];
  blueprint: Record<string, unknown>;
}

export async function getChiefEngineerDiagnostics(): Promise<ApiResult<ChiefEngineerDiagnosticsResponse>> {
  return apiGet<ChiefEngineerDiagnosticsResponse>(
    '/v2/chief-engineer/diagnostics',
    'Failed to load Chief Engineer diagnostics',
  );
}

export async function generateChiefEngineerBlueprint(
  payload: GenerateChiefEngineerBlueprintPayload,
): Promise<ApiResult<ChiefEngineerTaskBlueprintResultResponse>> {
  return apiPost<ChiefEngineerTaskBlueprintResultResponse>(
    '/v2/chief-engineer/blueprints',
    payload,
    'Failed to generate Chief Engineer blueprint',
  );
}

export async function getChiefEngineerBlueprintStatus(
  taskId: string,
  runId?: string | null,
): Promise<ApiResult<ChiefEngineerTaskBlueprintResultResponse>> {
  const query = new URLSearchParams({ task_id: taskId });
  if (runId) {
    query.set('run_id', runId);
  }
  return apiGet<ChiefEngineerTaskBlueprintResultResponse>(
    `/v2/chief-engineer/blueprints/status?${query.toString()}`,
    'Failed to load Chief Engineer blueprint status',
  );
}

export async function listChiefEngineerBlueprints(): Promise<ApiResult<ChiefEngineerBlueprintListResponse>> {
  return apiGet<ChiefEngineerBlueprintListResponse>(
    '/v2/chief-engineer/blueprints',
    'Failed to list Chief Engineer blueprints',
  );
}

export async function getChiefEngineerBlueprint(
  blueprintId: string,
): Promise<ApiResult<ChiefEngineerBlueprintDetailResponse>> {
  return apiGet<ChiefEngineerBlueprintDetailResponse>(
    `/v2/chief-engineer/blueprints/${encodeURIComponent(blueprintId)}`,
    'Failed to load Chief Engineer blueprint',
  );
}
