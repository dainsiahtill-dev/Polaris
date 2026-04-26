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
  const result = await apiGet<DirectorStatusPayload>('/v2/director/status', 'Failed to load Director status');

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
  return apiPostEmpty<void>('/v2/pm/run_once', 'PM run once failed');
}

/**
 * 启动Director
 */
export async function startDirector(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/director/start', 'Failed to start Chief Engineer');
}

/**
 * 停止Director
 */
export async function stopDirector(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/director/stop', 'Failed to stop Chief Engineer');
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
  priority: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  timeout_seconds: number;
  metadata: {
    pm_task_id: string;
    pm_task_title: string;
    pm_task_status: string;
    acceptance: string[];
  };
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
