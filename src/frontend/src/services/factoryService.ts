/**
 * Factory Run Service
 *
 * Canonical API client for Factory run lifecycle and SSE streaming.
 */

import { getBackendInfo } from '@/api';
import { apiGet, apiPost, buildQueryString } from './apiClient';
import type { ApiResult } from './api.types';
import type {
  FactoryAuditEvent,
  FactoryRunStatus,
  FactoryStartOptions,
} from './api.types';

export type { FactoryAuditEvent, FactoryRunStatus, FactoryStartOptions };

export interface FactoryStreamHandlers {
  onOpen?: () => void;
  onStatus?: (run: FactoryRunStatus) => void;
  onEvent?: (event: FactoryAuditEvent) => void;
  onDone?: (run: FactoryRunStatus) => void;
  onError?: (data: Record<string, unknown>) => void;
  onConnectionError?: () => void;
}

export interface FactoryStreamConnection {
  eventSource: EventSource;
  close: () => void;
}

/**
 * 启动 Factory Run
 */
export async function startFactoryRun(
  options: FactoryStartOptions
): Promise<ApiResult<FactoryRunStatus>> {
  return apiPost<FactoryRunStatus>('/v2/factory/runs', options, '启动Factory失败');
}

/**
 * 取消 Factory Run
 */
export async function stopFactoryRun(
  runId: string,
  reason?: string
): Promise<ApiResult<FactoryRunStatus>> {
  return apiPost<FactoryRunStatus>(
    `/v2/factory/runs/${encodeURIComponent(runId)}/control`,
    { action: 'cancel', reason },
    '停止Factory失败'
  );
}

/**
 * 获取 Factory Run 状态
 */
export async function getFactoryRun(
  runId: string
): Promise<ApiResult<FactoryRunStatus>> {
  return apiGet<FactoryRunStatus>(
    `/v2/factory/runs/${encodeURIComponent(runId)}`,
    '获取Factory状态失败'
  );
}

/**
 * 获取 Factory Run 列表
 */
export async function listFactoryRuns(
  limit = 20
): Promise<ApiResult<FactoryRunStatus[]>> {
  const query = buildQueryString({ limit });
  const result = await apiGet<{ runs: FactoryRunStatus[] }>(
    `/v2/factory/runs${query}`,
    '获取Factory列表失败'
  );

  if (result.ok && result.data) {
    return { ok: true, data: result.data.runs || [] };
  }

  return { ok: false, error: result.error || '获取Factory列表失败' };
}

function parseJsonPayload<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

/**
 * 创建 Factory Run SSE 连接
 */
export async function connectFactoryStream(
  runId: string,
  handlers: FactoryStreamHandlers
): Promise<FactoryStreamConnection> {
  const backend = await getBackendInfo();
  const url = new URL(
    `/v2/factory/runs/${encodeURIComponent(runId)}/stream`,
    backend.baseUrl || window.location.origin
  );
  if (backend.token) {
    url.searchParams.set('token', backend.token);
  }

  const eventSource = new EventSource(url.toString());

  eventSource.onopen = () => {
    handlers.onOpen?.();
  };

  eventSource.addEventListener('status', (event: MessageEvent) => {
    handlers.onStatus?.(parseJsonPayload<FactoryRunStatus>(event.data, {} as FactoryRunStatus));
  });

  eventSource.addEventListener('event', (event: MessageEvent) => {
    handlers.onEvent?.(
      parseJsonPayload<FactoryAuditEvent>(event.data, {
        type: 'unknown',
        timestamp: new Date().toISOString(),
      })
    );
  });

  eventSource.addEventListener('done', (event: MessageEvent) => {
    handlers.onDone?.(parseJsonPayload<FactoryRunStatus>(event.data, {} as FactoryRunStatus));
  });

  eventSource.addEventListener('error', (event: MessageEvent) => {
    handlers.onError?.(parseJsonPayload<Record<string, unknown>>(event.data || '{}', {}));
  });

  eventSource.onerror = () => {
    handlers.onConnectionError?.();
  };

  return {
    eventSource,
    close: () => {
      eventSource.close();
    },
  };
}
