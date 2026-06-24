import { apiDelete, apiGet, apiPost } from './apiClient';
import type { ApiResult } from './api.types';

export type PolarisInstanceKind = 'project' | 'bench_project' | 'internal_test' | string;

export interface PolarisInstance {
  schema_version: number;
  instance_id: string;
  name: string;
  kind: PolarisInstanceKind;
  polaris_root: string;
  workspace: string;
  runtime_root: string;
  backend_port: number;
  frontend_port: number;
  backend_url: string;
  frontend_url: string;
  token: string;
  backend_reload: boolean;
  frontend_vite: boolean;
  start_frontend: boolean;
  status: string;
  backend_pid: number | null;
  frontend_pid: number | null;
  backend_alive: boolean;
  frontend_alive: boolean;
  created_at: string;
  updated_at: string;
  last_started_at: string;
  last_stopped_at: string;
  bench: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface InstanceListResponse {
  instances: PolarisInstance[];
}

export interface InstanceResponse {
  instance: PolarisInstance;
}

export interface InstanceLogsResponse {
  stream: 'backend' | 'frontend';
  content: string;
}

export interface StartInstancePayload {
  instance_id?: string;
  name?: string;
  kind?: PolarisInstanceKind;
  polaris_root?: string;
  workspace: string;
  runtime_root?: string;
  backend_port?: number | null;
  frontend_port?: number | null;
  token?: string;
  backend_reload?: boolean;
  frontend_vite?: boolean;
  start_frontend?: boolean;
  bench?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export async function listInstances(): Promise<ApiResult<InstanceListResponse>> {
  return apiGet<InstanceListResponse>('/v2/instances', '读取 Polaris 实例列表失败');
}

export async function startInstance(payload: StartInstancePayload): Promise<ApiResult<InstanceResponse>> {
  return apiPost<InstanceResponse>('/v2/instances/start', payload, '启动 Polaris 实例失败');
}

export async function stopInstance(instanceId: string): Promise<ApiResult<InstanceResponse>> {
  return apiPost<InstanceResponse>(
    `/v2/instances/${encodeURIComponent(instanceId)}/stop`,
    {},
    '停止 Polaris 实例失败',
  );
}

export async function restartInstance(instanceId: string): Promise<ApiResult<InstanceResponse>> {
  return apiPost<InstanceResponse>(
    `/v2/instances/${encodeURIComponent(instanceId)}/restart`,
    {},
    '重启 Polaris 实例失败',
  );
}

export async function deleteInstance(instanceId: string): Promise<ApiResult<{ ok: boolean }>> {
  return apiDelete<{ ok: boolean }>(
    `/v2/instances/${encodeURIComponent(instanceId)}`,
    '删除 Polaris 实例失败',
  );
}

export async function getInstanceLogs(
  instanceId: string,
  stream: 'backend' | 'frontend',
  tailLines = 300,
): Promise<ApiResult<InstanceLogsResponse>> {
  const params = new URLSearchParams({ stream, tail_lines: String(tailLines) });
  return apiGet<InstanceLogsResponse>(
    `/v2/instances/${encodeURIComponent(instanceId)}/logs?${params.toString()}`,
    '读取 Polaris 实例日志失败',
  );
}

export function buildInstanceWorkspaceUrl(instance: PolarisInstance): string {
  const base = instance.frontend_url || window.location.origin;
  const url = new URL(base);
  url.searchParams.set('instance', instance.instance_id);
  url.searchParams.set('backend', instance.backend_url);
  if (instance.workspace) {
    url.searchParams.set('workspace', instance.workspace);
  }
  if (instance.token) {
    url.searchParams.set('token', instance.token);
  }
  return url.toString();
}
