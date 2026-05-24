import { apiDelete, apiGet, apiPost } from './apiClient';
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

export interface ChiefEngineerDiagnosticsLLMStatus {
  ok: boolean;
  state: string;
  role: 'chief_engineer';
  blocked_roles: string[];
  unsupported_roles: string[];
  required_ready_roles: string[];
  provider_id: string | null;
  model: string | null;
  error: string | null;
  details: Record<string, unknown>;
}

export interface ChiefEngineerDiagnosticsBlueprintStatus {
  ok: boolean;
  status: string;
  source: string;
  total: number;
  loadable: number;
  invalid_payloads: number;
  planned_tasks: number;
  covered_tasks: number;
  missing_task_ids: string[];
  director_handoff_ready: boolean;
  latest_updated_at: string | null;
  error: string | null;
}

export interface ChiefEngineerDiagnosticsResponse {
  ok: boolean;
  can_handoff?: boolean;
  role: 'chief_engineer';
  generated_at: string;
  workspace: ChiefEngineerDiagnosticsWorkspaceStatus;
  llm: ChiefEngineerDiagnosticsLLMStatus;
  blueprints: ChiefEngineerDiagnosticsBlueprintStatus;
  can_generate?: boolean;
  issues: string[];
  generate_blockers?: string[];
  handoff_blockers?: string[];
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

export interface ChiefEngineerBlueprintDeleteResponse {
  ok: boolean;
  blueprint_id: string;
  deleted: boolean;
  source: string;
}

function workspaceQuerySuffix(workspace = ''): string {
  return workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
}

function appendWorkspaceQuery(path: string, workspace = ''): string {
  if (!workspace) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}workspace=${encodeURIComponent(workspace)}`;
}

export async function getChiefEngineerDiagnostics(
  workspace = '',
): Promise<ApiResult<ChiefEngineerDiagnosticsResponse>> {
  return apiGet<ChiefEngineerDiagnosticsResponse>(
    `/v2/chief-engineer/diagnostics${workspaceQuerySuffix(workspace)}`,
    'Failed to load Chief Engineer diagnostics',
  );
}

export async function generateChiefEngineerBlueprint(
  payload: GenerateChiefEngineerBlueprintPayload,
  workspace = '',
): Promise<ApiResult<ChiefEngineerTaskBlueprintResultResponse>> {
  return apiPost<ChiefEngineerTaskBlueprintResultResponse>(
    `/v2/chief-engineer/blueprints${workspaceQuerySuffix(workspace)}`,
    payload,
    'Failed to generate Chief Engineer blueprint',
  );
}

export async function getChiefEngineerBlueprintStatus(
  taskId: string,
  runId?: string | null,
  workspace = '',
): Promise<ApiResult<ChiefEngineerTaskBlueprintResultResponse>> {
  const query = new URLSearchParams({ task_id: taskId });
  if (runId) {
    query.set('run_id', runId);
  }
  if (workspace) {
    query.set('workspace', workspace);
  }
  return apiGet<ChiefEngineerTaskBlueprintResultResponse>(
    `/v2/chief-engineer/blueprints/status?${query.toString()}`,
    'Failed to load Chief Engineer blueprint status',
  );
}

export async function listChiefEngineerBlueprints(
  workspace = '',
): Promise<ApiResult<ChiefEngineerBlueprintListResponse>> {
  return apiGet<ChiefEngineerBlueprintListResponse>(
    `/v2/chief-engineer/blueprints${workspaceQuerySuffix(workspace)}`,
    'Failed to list Chief Engineer blueprints',
  );
}

export async function getChiefEngineerBlueprint(
  blueprintId: string,
  workspace = '',
): Promise<ApiResult<ChiefEngineerBlueprintDetailResponse>> {
  return apiGet<ChiefEngineerBlueprintDetailResponse>(
    appendWorkspaceQuery(`/v2/chief-engineer/blueprints/${encodeURIComponent(blueprintId)}`, workspace),
    'Failed to load Chief Engineer blueprint',
  );
}

export async function deleteChiefEngineerBlueprint(
  blueprintId: string,
  workspace = '',
): Promise<ApiResult<ChiefEngineerBlueprintDeleteResponse>> {
  return apiDelete<ChiefEngineerBlueprintDeleteResponse>(
    appendWorkspaceQuery(`/v2/chief-engineer/blueprints/${encodeURIComponent(blueprintId)}`, workspace),
    'Failed to delete Chief Engineer blueprint',
  );
}
