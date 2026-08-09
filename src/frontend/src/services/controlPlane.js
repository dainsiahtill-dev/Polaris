/**
 * Platform control-plane ledger projection types.
 *
 * Run Ledger is core Polaris infrastructure. Internal stress harnesses are only
 * producers/consumers of this read model; formal workspaces should import these
 * platform types directly instead of depending on test-harness services.
 */
import { apiGet, apiPost } from './apiClient';
function isRecord(value) {
    return typeof value === 'object' && value !== null;
}
function runtimeEnvelopeFromMessage(message) {
    if (!isRecord(message))
        return null;
    if (message.type === 'EVENT' &&
        message.protocol === 'runtime.v2' &&
        isRecord(message.event)) {
        return message.event;
    }
    return message;
}
export function controlPlaneProjectionFromRuntimeMessage(message) {
    const envelope = runtimeEnvelopeFromMessage(message);
    if (!envelope)
        return null;
    if (String(envelope.channel || '').trim() !== 'status.control_plane')
        return null;
    const payload = isRecord(envelope.payload) ? envelope.payload : null;
    const projection = isRecord(payload?.projection)
        ? payload.projection
        : isRecord(envelope.projection)
            ? envelope.projection
            : null;
    if (!projection)
        return null;
    if (String(projection.source || '').trim() !== 'run_ledger_projection')
        return null;
    if (!Array.isArray(projection.projects))
        return null;
    return projection;
}
export async function getControlPlaneProjection(options = {}) {
    const params = new URLSearchParams();
    if (options.workspace)
        params.set('workspace', options.workspace);
    if (options.runId)
        params.set('run_id', options.runId);
    if (options.maxRuns !== undefined)
        params.set('max_runs', String(options.maxRuns));
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return apiGet(`/v2/control-plane/ledger/projection${suffix}`, '获取 Control Plane 账本投影失败');
}
export async function getVerifierPolicy(options = {}) {
    const params = new URLSearchParams();
    if (options.workspace)
        params.set('workspace', options.workspace);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return apiGet(`/v2/control-plane/verifier-policy${suffix}`, '获取 Control Plane 验收策略失败');
}
export async function updateVerifierPolicy(payload, options = {}) {
    const params = new URLSearchParams();
    if (options.workspace)
        params.set('workspace', options.workspace);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return apiPost(`/v2/control-plane/verifier-policy${suffix}`, payload, '保存 Control Plane 验收策略失败');
}
