/**
 * PM Service
 *
 * 封装所有PM相关的API调用，提供类型安全的接口
 */

import { apiGet, apiPost, apiPostEmpty } from './apiClient';
import type { ApiResult } from './api.types';
import type { ProcessStatus, DirectorStatusPayload } from './api.types';

// ============================================================================
// Status Types
// ============================================================================

export interface PmStatus extends ProcessStatus {}
export interface DirectorStatus extends ProcessStatus {}

// ============================================================================
// Status Services
// ============================================================================

/**
 * 获取PM状态
 */
export async function getPmStatus(): Promise<ApiResult<PmStatus>> {
  return apiGet<PmStatus>('/v2/pm/status', 'Failed to load PM status');
}

/**
 * 获取Director状态
 */
export async function getDirectorStatus(): Promise<ApiResult<DirectorStatus>> {
  const result = await apiGet<DirectorStatusPayload>('/v2/director/status?source=auto', 'Failed to load Director status');

  if (!result.ok || !result.data) {
    return { ok: false, error: result.error || 'Failed to load Director status' };
  }

  // Normalize director status payload to standard ProcessStatus
  const raw = result.data;

  if (typeof raw.running === 'boolean') {
    return {
      ok: true,
      data: {
        running: raw.running,
        pid: typeof raw.pid === 'number' ? raw.pid : null,
        started_at: typeof raw.started_at === 'number' ? raw.started_at : null,
        mode: raw.mode,
        log_path: raw.log_path,
        source: raw.source,
        status: raw.status ?? null,
      } as DirectorStatus,
    };
  }

  // Handle state-based response
  const state = String(raw.state || '').trim().toUpperCase();
  return {
    ok: true,
    data: {
      running: state === 'RUNNING',
      pid: null,
      started_at: null,
      mode: 'v2_service',
      source: 'v2_service',
      status: raw as Record<string, unknown>,
    } as DirectorStatus,
  };
}

/**
 * 获取所有进程状态
 */
export async function getAllStatuses(): Promise<{
  pm: ApiResult<PmStatus>;
  director: ApiResult<DirectorStatus>;
}> {
  const [pm, director] = await Promise.all([
    getPmStatus(),
    getDirectorStatus(),
  ]);

  return { pm, director };
}

// ============================================================================
// Process Control Services
// ============================================================================

/**
 * 启动PM
 * @param resume 是否恢复之前的运行
 */
export async function startPm(resume = false): Promise<ApiResult<void>> {
  const path = resume ? '/v2/pm/start?resume=true' : '/v2/pm/start';
  return apiPostEmpty<void>(path, 'Failed to start PM');
}

/**
 * 停止PM
 */
export async function stopPm(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/pm/stop', 'Failed to stop PM');
}

/**
 * 单次运行PM
 */
export async function runPmOnce(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/pm/run_once', 'PM Run Once failed');
}

/**
 * 启动Director
 */
export async function startDirector(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/director/start', 'Failed to start Director');
}

/**
 * 停止Director
 */
export async function stopDirector(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/director/stop', 'Failed to stop Director');
}

// ============================================================================
// Director Task Queue Services
// ============================================================================

export interface DirectorTask {
  id: string;
  subject: string;
  description?: string;
  status?: string;
  metadata?: {
    pm_task_id?: string;
    workflow_state?: string;
    [key: string]: unknown;
  };
}

export interface CreateDirectorTaskPayload {
  subject: string;
  description: string;
  command?: string | null;
  priority: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  timeout_seconds: number;
  metadata: {
    pm_task_id: string;
    pm_task_title: string;
    pm_task_status: string;
    acceptance: string[];
    blueprint_id?: string | null;
    blueprint_path?: string | null;
    runtime_blueprint_path?: string | null;
    guardrails?: unknown;
    no_touch_zones?: unknown;
    context_snapshot_ref?: string | null;
    [key: string]: unknown;
  };
}

export interface RunDirectorPayload {
  workspace: string;
  task_id?: string | null;
  task_filter?: string | null;
  max_workers?: number;
  execution_mode?: 'serial' | 'parallel';
}

export interface RunDirectorResponse {
  run_id: string;
  status: string;
  workspace: string;
  tasks_queued: number;
  message: string;
}

/**
 * 获取Director任务列表
 */
export async function listDirectorTasks(source?: string): Promise<ApiResult<DirectorTask[]>> {
  const query = source ? `?source=${encodeURIComponent(source)}` : '';
  return apiGet<DirectorTask[]>(`/v2/director/tasks${query}`, 'Failed to list Director tasks');
}

/**
 * 创建Director任务
 */
export async function createDirectorTask(payload: CreateDirectorTaskPayload): Promise<ApiResult<DirectorTask>> {
  return apiPost<DirectorTask>('/v2/director/tasks', payload, 'Failed to create Director task');
}

/**
 * 通过统一编排入口运行 Director，可选绑定单个任务。
 */
export async function runDirector(payload: RunDirectorPayload): Promise<ApiResult<RunDirectorResponse>> {
  return apiPost<RunDirectorResponse>('/v2/director/run', payload, 'Failed to run Director');
}

// ============================================================================
// PM Document Services
// ============================================================================

export interface PmDocumentInfo {
  path: string;
  current_version: string | number;
  version_count: number;
  last_modified: string;
  created_at: string;
}

export interface PmDocumentListResponse {
  documents: PmDocumentInfo[];
  pagination: Record<string, unknown>;
}

export interface PmDocumentDetailResponse extends PmDocumentInfo {
  content?: string | null;
  versions?: Array<Record<string, unknown>> | null;
  analysis?: Record<string, unknown> | null;
}

export interface PmDocumentWriteResponse {
  success: boolean;
  path: string;
  version?: string | null;
  checksum?: string | null;
}

function encodeDocumentPath(path: string): string {
  return path
    .replace(/\\/g, '/')
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/');
}

export const pmDocumentService = {
  list(): Promise<ApiResult<PmDocumentListResponse>> {
    return apiGet<PmDocumentListResponse>('/v2/pm/documents', 'Failed to list PM documents');
  },

  get(path: string): Promise<ApiResult<PmDocumentDetailResponse>> {
    return apiGet<PmDocumentDetailResponse>(
      `/v2/pm/documents/${encodeDocumentPath(path)}`,
      'Failed to read PM document',
    );
  },

  save(path: string, content: string, changeSummary: string): Promise<ApiResult<PmDocumentWriteResponse>> {
    return apiPost<PmDocumentWriteResponse>(
      `/v2/pm/documents/${encodeDocumentPath(path)}`,
      { content, change_summary: changeSummary },
      'Failed to save PM document',
    );
  },
};
