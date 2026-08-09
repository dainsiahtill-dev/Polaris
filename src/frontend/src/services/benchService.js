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
import { apiGet, apiPost } from './apiClient';
/** Register a new bench session (typically called by the bench subprocess). */
export async function startBenchSession(payload) {
    return apiPost('/v2/factory/bench/sessions', payload, '注册Factory bench session失败');
}
/** Append a bench event to an existing session (durable + JetStream fanout). */
export async function appendBenchEvent(sessionId, payload) {
    return apiPost(`/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}/events`, payload, '推送Factory bench event失败');
}
/** Mark a bench session complete (or failed). */
export async function completeBenchSession(sessionId, payload) {
    return apiPost(`/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}/complete`, payload, '结束Factory bench session失败');
}
/** Update per-project counters so the UI sees live ``X/Y 通过``. */
export async function updateBenchProgress(sessionId, payload) {
    return apiPost(`/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}/progress`, payload, '更新Factory bench progress失败');
}
/** List recent bench sessions for the Factory panel UI. */
export async function listBenchSessions(limit = 20) {
    const result = await apiGet(`/v2/factory/bench/sessions?limit=${limit}`, '获取Factory bench sessions失败');
    if (result.ok && result.data) {
        return { ok: true, data: result.data.sessions || [] };
    }
    return { ok: false, error: result.error || '获取Factory bench sessions失败' };
}
/** Get a single bench session's full detail (status + recent events). */
export async function getBenchSession(sessionId) {
    return apiGet(`/v2/factory/bench/sessions/${encodeURIComponent(sessionId)}`, '获取Factory bench session失败');
}
