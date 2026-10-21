/** One-shot engineering commands/queries; realtime belongs to the shared transport. */
import { apiGet, apiPost } from './apiClient';
import type { ApiResult, EngineeringRole, EngineeringRunSnapshot, EngineeringStartOptions } from './api.types';
import { normalizeRuntimeWorkspacePath } from '@/app/hooks/_runtimeEventFilter';
import { parseEngineeringSnapshot } from '@/runtime/engineeringRunProjection';
async function snapshotResult(request: Promise<ApiResult<unknown>>): Promise<ApiResult<EngineeringRunSnapshot>> {
  const result = await request;
  if (!result.ok)
    return { ok: false, error: result.error || '工程运行不可用' };
  try {
    return { ok: true, data: parseEngineeringSnapshot(result.data) };
  }
  catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : '工程快照不可用' };
  }
}
export function startEngineeringRun(options: EngineeringStartOptions): Promise<ApiResult<EngineeringRunSnapshot>> {
  return snapshotResult(apiPost<unknown>('/v2/engineering/runs', options, '启动工程运行失败'));
}
export function getEngineeringRun(id: string, workspace: string): Promise<ApiResult<EngineeringRunSnapshot>> {
  return snapshotResult(apiGet<unknown>(`/v2/engineering/runs/${encodeURIComponent(id)}?workspace=${encodeURIComponent(workspace)}`, '工程运行不可用'));
}
export async function getCurrentEngineeringRun(workspace: string): Promise<ApiResult<EngineeringRunSnapshot | null>> {
  const result = await apiGet<unknown>(`/v2/engineering/runs/current?workspace=${encodeURIComponent(workspace)}`, '工程运行不可用');
  if (result.ok && result.data === null) return { ok: true, data: null };
  const parsed = await snapshotResult(Promise.resolve(result));
  if (parsed.ok && parsed.data && normalizeRuntimeWorkspacePath(parsed.data.workspace) !== normalizeRuntimeWorkspacePath(workspace))
    return { ok: false, error: 'Engineering snapshot workspace/session mismatch' };
  return parsed;
}
export function cancelEngineeringRun(id: string, workspace: string): Promise<ApiResult<EngineeringRunSnapshot>> {
  return snapshotResult(apiPost<unknown>(`/v2/engineering/runs/${encodeURIComponent(id)}/cancel`, { workspace }, '取消工程运行失败'));
}
export function resumeEngineeringRun(id: string, workspace: string): Promise<ApiResult<EngineeringRunSnapshot>> {
  return snapshotResult(apiPost<unknown>(`/v2/engineering/runs/${encodeURIComponent(id)}/resume`, { workspace }, '继续工程运行失败'));
}
export interface EngineeringDiscussion { response: string; metadata?: Record<string, unknown>; }
export async function discussEngineeringRole(role: EngineeringRole, message: string, workspace: string): Promise<ApiResult<EngineeringDiscussion>> {
  const result = await apiPost<unknown>(`/v2/role/${role}/chat`, { message, workspace, intent: 'discuss', engine_version: 'engineering_v2' }, '角色讨论失败');
  if (!result.ok) return { ok: false, error: result.error };
  if (!result.data || typeof result.data !== 'object' || typeof (result.data as Record<string, unknown>).response !== 'string') return { ok: false, error: '角色讨论返回不可用' };
  const data = result.data as Record<string, unknown>;
  const metadata = data.metadata && typeof data.metadata === 'object' && !Array.isArray(data.metadata) ? data.metadata as Record<string, unknown> : undefined;
  return { ok: true, data: { response: data.response as string, metadata } };
}
