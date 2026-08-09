/**
 * Factory Run Service
 *
 * Canonical API client for Factory run lifecycle. Real-time updates for
 * factory runs flow through the platform's unified WebSocket + NAT
 * JetStream pipeline; see ``useFactory`` and
 * ``RuntimeTransportProvider``.
 */
import { apiGet, apiPost, buildQueryString } from './apiClient';
/**
 * 启动 Factory Run
 */
export async function startFactoryRun(options) {
    return apiPost('/v2/factory/runs', options, '启动Factory失败');
}
/**
 * 取消 Factory Run
 */
export async function stopFactoryRun(runId, reason) {
    return controlFactoryRun(runId, { action: 'cancel', reason });
}
/**
 * 控制 Factory Run 生命周期。
 */
export async function controlFactoryRun(runId, options) {
    return apiPost(`/v2/factory/runs/${encodeURIComponent(runId)}/control`, options, '控制Factory失败');
}
export async function pauseFactoryRun(runId, reason) {
    return controlFactoryRun(runId, { action: 'pause', reason });
}
export async function resumeFactoryRun(runId, reason) {
    return controlFactoryRun(runId, { action: 'resume', reason });
}
export async function retryFactoryRunFromCheckpoint(runId, reason) {
    return controlFactoryRun(runId, { action: 'retry_from_checkpoint', reason });
}
/**
 * 获取 Factory Run 状态
 */
export async function getFactoryRun(runId) {
    return apiGet(`/v2/factory/runs/${encodeURIComponent(runId)}`, '获取Factory状态失败');
}
/**
 * 获取 Factory Run 审计产物与交付摘要
 */
export async function getFactoryRunArtifacts(runId) {
    return apiGet(`/v2/factory/runs/${encodeURIComponent(runId)}/artifacts`, '获取Factory产物失败');
}
/**
 * 获取 Factory Run 列表
 */
export async function listFactoryRuns(limit = 20) {
    const query = buildQueryString({ limit });
    const result = await apiGet(`/v2/factory/runs${query}`, '获取Factory列表失败');
    if (result.ok && result.data) {
        return { ok: true, data: result.data.runs || [] };
    }
    return { ok: false, error: result.error || '获取Factory列表失败' };
}
