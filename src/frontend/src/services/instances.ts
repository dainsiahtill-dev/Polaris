import { apiDelete, apiGet, apiPost } from './apiClient';
import type { ApiResult } from './api.types';
import { isBackendOriginPermitted } from './backendOrigin';

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
  // The published addresses are the ones a consumer on another host can
  // dial. The local addresses stay loopback.
  public_backend_url?: string;
  public_frontend_url?: string;
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

/**
 * 工作台是否真的能打开。
 *
 * 没有实例前端时，主前端必须跨源访问实例后端 —— dev 的 connect-src 策略会
 * 拒绝，浏览器里表现为每个请求 403、runtime WebSocket 断连（实测：用
 * ?instance=...&workspace=... 打开非主实例时全部 403）。CLAUDE.md 要求
 * "禁止静默回退到默认 backend"，所以这里明确判定，而不是生成一个必失败然后
 * 静默回退的 URL。
 */
export function isLauncherWorkspaceOpenable(instance: PolarisInstance): boolean {
  if (!instance.workspace) return false;
  const frontend = instance.public_frontend_url || instance.frontend_url;
  if (frontend) return true;
  const backend = instance.public_backend_url || instance.backend_url;
  if (!backend) return false;
  try {
    return new URL(backend).origin === window.location.origin;
  } catch {
    return false;
  }
}

export function buildInstanceWorkspaceUrl(instance: PolarisInstance): string {
  // A published instance is reachable from another host, so its page is
  // opened on the published address.
  const base = instance.public_frontend_url || instance.frontend_url || window.location.origin;
  const url = new URL(base);
  url.searchParams.set('instance', instance.instance_id);
  // The instance page reaches its backend through the same-origin dev proxy,
  // which routes to the backend of the instance that serves the page. An
  // absolute cross-origin backend is carried only when the shipped
  // connect-src policy permits it, because the browser would refuse every
  // request and every runtime WebSocket frame sent to a refused origin.
  const backendUrl = instance.public_backend_url || instance.backend_url;
  if (backendUrl && isBackendOriginPermitted(backendUrl, url.origin)) {
    url.searchParams.set('backend', backendUrl);
  }
  if (instance.workspace) {
    url.searchParams.set('workspace', instance.workspace);
  }
  if (instance.token) {
    url.searchParams.set('token', instance.token);
  }
  return url.toString();
}
