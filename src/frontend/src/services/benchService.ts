/**
 * Factory-bench service — drives L1-L8 batch progress from the bench
 * subprocess into the Factory front-end panel.
 *
 * Mirrors `factoryService.ts` patterns: HTTP REST + SSE streaming. The bench
 * subprocess is what produces the events; the Factory panel just observes.
 */

import { getBackendInfo } from '@/api';
import { apiGet, apiPost } from './apiClient';
import type { ApiResult } from './api.types';

export interface FactoryBenchSessionSummary {
  session_id: string;
  work_dir: string;
  project_ids: string[];
  total: number;
  completed: number;
  failed: number;
  status: 'running' | 'completed' | 'failed' | string;
  created_at: string;
  updated_at: string;
  completed_at?: string;
  metadata: Record<string, unknown>;
}

export interface FactoryBenchEvent {
  type: string;
  name?: string | null;
  actor?: string | null;
  summary?: string | null;
  ok?: boolean | null;
  meta?: Record<string, unknown>;
  ts?: string;
  session_id?: string;
}

export interface FactoryBenchSessionDetail extends FactoryBenchSessionSummary {
  events_path: string;
  events: FactoryBenchEvent[];
}

export interface FactoryBenchStreamHandlers {
  onOpen?: () => void;
  onStatus?: (session: FactoryBenchSessionSummary) => void;
  onEvent?: (event: FactoryBenchEvent) => void;
  onDone?: (session: FactoryBenchSessionSummary) => void;
  onError?: (data: Record<string, unknown>) => void;
  onConnectionError?: () => void;
}

export interface FactoryBenchStreamConnection {
  eventSource: EventSource;
  close: () => void;
}

function parseJsonPayload<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

/** Register a new bench session (typically called by the bench subprocess). */
export async function startBenchSession(payload: {
  work_dir: string;
  project_ids: string[];
  total: number;
  metadata?: Record<string, unknown>;
  session_id?: string;
}): Promise<ApiResult<{ session_id: string; status: string }>> {
  return apiPost<{ session_id: string; status: string }>(
    '/v2/factory/bench/sessions',
    payload,
    '注册Factory bench session失败',
  );
}

/** Append a bench event to an existing session. */
export async function appendBenchEvent(
  sessionId: string,
  payload: {
    type: string;
    name?: string;
    actor?: string;
    summary?: string;
    ok?: boolean;
    meta?: Record<string, unknown>;
  },
): Promise<ApiResult<{ session_id: string; appended: boolean }>> {
  return apiPost<{ session_id: string; appended: boolean }>(
    `/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}/events`,
    payload,
    '推送Factory bench event失败',
  );
}

/** Mark a bench session complete (or failed). */
export async function completeBenchSession(
  sessionId: string,
  payload: { success: boolean; summary?: Record<string, unknown> },
): Promise<ApiResult<{ session_id: string; updated: boolean }>> {
  return apiPost<{ session_id: string; updated: boolean }>(
    `/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}/complete`,
    payload,
    '结束Factory bench session失败',
  );
}

/** List recent bench sessions for the Factory panel UI. */
export async function listBenchSessions(
  limit = 20,
): Promise<ApiResult<FactoryBenchSessionSummary[]>> {
  const result = await apiGet<{ total: number; sessions: FactoryBenchSessionSummary[] }>(
    `/v2/factory/bench/sessions?limit=${limit}`,
    '获取Factory bench sessions失败',
  );
  if (result.ok && result.data) {
    return { ok: true, data: result.data.sessions || [] };
  }
  return { ok: false, error: result.error || '获取Factory bench sessions失败' };
}

/** Get a single bench session's full detail (status + recent events). */
export async function getBenchSession(
  sessionId: string,
): Promise<ApiResult<FactoryBenchSessionDetail>> {
  return apiGet<FactoryBenchSessionDetail>(
    `/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}`,
    '获取Factory bench session失败',
  );
}

/** Open an SSE stream for live bench events. */
export async function connectBenchStream(
  sessionId: string,
  handlers: FactoryBenchStreamHandlers,
): Promise<FactoryBenchStreamConnection> {
  const backend = await getBackendInfo();
  const url = new URL(
    `/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}/stream`,
    backend.baseUrl || window.location.origin,
  );
  if (backend.token) {
    url.searchParams.set('token', backend.token);
  }
  const eventSource = new EventSource(url.toString());
  eventSource.onopen = () => handlers.onOpen?.();
  eventSource.addEventListener('status', (event: MessageEvent) => {
    const payload = parseJsonPayload<FactoryBenchSessionSummary>(event.data, {} as FactoryBenchSessionSummary);
    handlers.onStatus?.(payload);
  });
  eventSource.addEventListener('event', (event: MessageEvent) => {
    const payload = parseJsonPayload<FactoryBenchEvent>(event.data, {
      type: 'unknown',
      ts: new Date().toISOString(),
    });
    handlers.onEvent?.(payload);
  });
  eventSource.addEventListener('done', (event: MessageEvent) => {
    const payload = parseJsonPayload<FactoryBenchSessionSummary>(event.data, {} as FactoryBenchSessionSummary);
    handlers.onDone?.(payload);
  });
  eventSource.addEventListener('error', (event: MessageEvent) => {
    handlers.onError?.(parseJsonPayload<Record<string, unknown>>(event.data || '{}', {}));
  });
  eventSource.onerror = () => handlers.onConnectionError?.();
  return {
    eventSource,
    close: () => eventSource.close(),
  };
}
