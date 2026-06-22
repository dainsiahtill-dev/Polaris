/**
 * Factory-bench service — drives L1-L8 batch progress from the bench
 * subprocess into the Factory front-end panel.
 *
 * Transport: HTTP state snapshots for explicit hydration plus
 * Nats-JetStream/WebSocket fanout for realtime event delivery.
 *
 * Backend pipeline (matched to the platform's runtime event subsystem):
 *   bench subprocess → POST /events  (durable JSONL + NAT JetStream fanout)
 *   bench subprocess → POST /progress / POST /complete  (status updates)
 *   NAT JetStream    → hp.runtime.bench.{session_id}  (cross-tab fanout)
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
  /** Per-session monotonic sequence number set by the backend on append. */
  seq?: number;
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

/** Append a bench event to an existing session (durable + JetStream fanout). */
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
): Promise<ApiResult<{ session_id: string; appended: boolean; published: boolean }>> {
  return apiPost<{ session_id: string; appended: boolean; published: boolean }>(
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

/** Update per-project counters so the UI sees live ``X/Y 通过``. */
export async function updateBenchProgress(
  sessionId: string,
  payload: { completed?: number; failed?: number },
): Promise<ApiResult<{ session_id: string; updated: boolean }>> {
  return apiPost<{ session_id: string; updated: boolean }>(
    `/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}/progress`,
    payload,
    '更新Factory bench progress失败',
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
