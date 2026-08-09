import { apiDelete, apiGet, apiPost } from './apiClient';
export async function listInstances() {
    return apiGet('/v2/instances', '读取 Polaris 实例列表失败');
}
export async function startInstance(payload) {
    return apiPost('/v2/instances/start', payload, '启动 Polaris 实例失败');
}
export async function stopInstance(instanceId) {
    return apiPost(`/v2/instances/${encodeURIComponent(instanceId)}/stop`, {}, '停止 Polaris 实例失败');
}
export async function restartInstance(instanceId) {
    return apiPost(`/v2/instances/${encodeURIComponent(instanceId)}/restart`, {}, '重启 Polaris 实例失败');
}
export async function deleteInstance(instanceId) {
    return apiDelete(`/v2/instances/${encodeURIComponent(instanceId)}`, '删除 Polaris 实例失败');
}
export async function getInstanceLogs(instanceId, stream, tailLines = 300) {
    const params = new URLSearchParams({ stream, tail_lines: String(tailLines) });
    return apiGet(`/v2/instances/${encodeURIComponent(instanceId)}/logs?${params.toString()}`, '读取 Polaris 实例日志失败');
}
export function buildInstanceWorkspaceUrl(instance) {
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
