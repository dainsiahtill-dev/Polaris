import { apiGet, apiPost } from './apiClient';
function responseError(payload, fallback) {
    return String(payload?.error || payload?.detail || payload?.message || fallback);
}
function fail(error) {
    return { ok: false, error };
}
function isRoleSessionListItem(value) {
    return Boolean(value && typeof value === 'object' && String(value.id || '').trim());
}
function normalizeRoleSessionList(items) {
    if (!Array.isArray(items)) {
        return [];
    }
    return items
        .filter(isRoleSessionListItem)
        .map((item) => item);
}
function normalizeArtifacts(items) {
    if (!Array.isArray(items)) {
        return [];
    }
    return items
        .map((item) => item && typeof item === 'object' ? item : null)
        .filter((artifact) => Boolean(artifact?.id));
}
function normalizeAuditEvents(items) {
    if (!Array.isArray(items)) {
        return [];
    }
    return items
        .map((item) => item && typeof item === 'object' ? item : null)
        .filter((event) => Boolean(event));
}
function normalizeMemoryItems(items) {
    if (!Array.isArray(items)) {
        return [];
    }
    return items
        .map((item) => item && typeof item === 'object' ? item : null)
        .filter((item) => Boolean(item));
}
function normalizeTotal(value, fallback) {
    if (typeof value === 'boolean') {
        return fallback;
    }
    const numeric = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : Number.NaN;
    return Number.isFinite(numeric) && numeric >= 0 ? Math.floor(numeric) : fallback;
}
export function resolveRoleCapabilities(payload, hostKind) {
    const capabilities = payload?.capabilities;
    if (Array.isArray(capabilities)) {
        return capabilities.map((item) => String(item || '').trim()).filter(Boolean);
    }
    if (!capabilities || typeof capabilities !== 'object') {
        return [];
    }
    const record = capabilities;
    const hostCapabilities = record[hostKind] || record.default;
    return Array.isArray(hostCapabilities)
        ? hostCapabilities.map((item) => String(item || '').trim()).filter(Boolean)
        : [];
}
export async function getRoleCapabilities(role, hostKind) {
    return apiGet(`/v2/roles/capabilities/${encodeURIComponent(role)}?host_kind=${encodeURIComponent(hostKind)}`, 'Failed to load role capabilities');
}
export async function getRoleSession(sessionId) {
    const result = await apiGet(`/v2/roles/sessions/${encodeURIComponent(sessionId)}`, 'Failed to load RoleSession');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to load RoleSession');
    }
    if (result.data.ok === false || !result.data.session || typeof result.data.session !== 'object') {
        return fail(responseError(result.data, 'RoleSession response missing session'));
    }
    return { ok: true, data: result.data.session };
}
export async function createRoleSession(payload) {
    const result = await apiPost('/v2/roles/sessions', payload, 'Failed to create RoleSession');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to create RoleSession');
    }
    if (result.data.ok === false || !result.data.session || typeof result.data.session !== 'object') {
        return fail(responseError(result.data, 'RoleSession create response missing session id'));
    }
    return { ok: true, data: result.data.session };
}
export async function attachRoleSession(sessionId, payload) {
    const result = await apiPost(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/actions/attach`, payload, 'Failed to attach RoleSession');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to attach RoleSession');
    }
    if (result.data.ok === false) {
        return fail(responseError(result.data, 'Failed to attach RoleSession'));
    }
    return { ok: true, data: result.data };
}
export async function detachRoleSession(sessionId) {
    const result = await apiPost(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/actions/detach`, {}, 'Failed to detach RoleSession');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to detach RoleSession');
    }
    if (result.data.ok === false) {
        return fail(responseError(result.data, 'Failed to detach RoleSession'));
    }
    return { ok: true, data: result.data.session ?? null };
}
export async function listRoleSessions(params) {
    const query = new URLSearchParams({
        role: params.role,
        host_kind: params.hostKind,
        limit: String(params.limit ?? 20),
    });
    if (typeof params.offset === 'number') {
        query.set('offset', String(params.offset));
    }
    if (params.workspace) {
        query.set('workspace', params.workspace);
    }
    const result = await apiGet(`/v2/roles/sessions?${query.toString()}`, 'Failed to list RoleSessions');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to list RoleSessions');
    }
    if (result.data.ok === false || !Array.isArray(result.data.sessions)) {
        return fail(responseError(result.data, 'RoleSession list response missing sessions'));
    }
    return { ok: true, data: normalizeRoleSessionList(result.data.sessions) };
}
export async function listRoleSessionMessages(sessionId, params = {}) {
    const result = await listRoleSessionMessageEvidence(sessionId, params);
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to list RoleSession messages');
    }
    return { ok: true, data: result.data.items };
}
export async function listRoleSessionMessageEvidence(sessionId, params = {}) {
    const query = new URLSearchParams({
        limit: String(params.limit ?? 100),
        offset: String(params.offset ?? 0),
    });
    const result = await apiGet(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/messages?${query.toString()}`, 'Failed to list RoleSession messages');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to list RoleSession messages');
    }
    if (result.data.ok === false || !Array.isArray(result.data.messages)) {
        return fail(responseError(result.data, 'RoleSession messages response missing messages'));
    }
    const items = result.data.messages;
    const total = normalizeTotal(result.data.total ?? result.data.session?.message_count, items.length);
    return {
        ok: true,
        data: {
            items,
            total,
            session: result.data.session ?? null,
        },
    };
}
export async function listRoleSessionArtifacts(sessionId, artifactType) {
    const result = await listRoleSessionArtifactEvidence(sessionId, artifactType);
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to list RoleSession artifacts');
    }
    return { ok: true, data: result.data.items };
}
export async function listRoleSessionArtifactEvidence(sessionId, artifactType) {
    const query = artifactType ? `?artifact_type=${encodeURIComponent(artifactType)}` : '';
    const result = await apiGet(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/artifacts${query}`, 'Failed to list RoleSession artifacts');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to list RoleSession artifacts');
    }
    if (result.data.ok === false || !Array.isArray(result.data.artifacts)) {
        return fail(responseError(result.data, 'RoleSession artifacts response missing artifacts'));
    }
    const items = normalizeArtifacts(result.data.artifacts);
    return {
        ok: true,
        data: {
            items,
            total: normalizeTotal(result.data.total, items.length),
        },
    };
}
export async function listRoleSessionAuditEvents(sessionId, params = {}) {
    const result = await listRoleSessionAuditEvidence(sessionId, params);
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to list RoleSession audit events');
    }
    return { ok: true, data: result.data.items };
}
export async function listRoleSessionAuditEvidence(sessionId, params = {}) {
    const query = new URLSearchParams({
        limit: String(params.limit ?? 20),
        offset: String(params.offset ?? 0),
    });
    if (params.eventType) {
        query.set('event_type', params.eventType);
    }
    const result = await apiGet(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/audit?${query.toString()}`, 'Failed to list RoleSession audit events');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to list RoleSession audit events');
    }
    if (result.data.ok === false || !Array.isArray(result.data.audit_events)) {
        return fail(responseError(result.data, 'RoleSession audit response missing events'));
    }
    const items = normalizeAuditEvents(result.data.audit_events);
    return {
        ok: true,
        data: {
            items,
            total: normalizeTotal(result.data.total, items.length),
        },
    };
}
export async function searchRoleSessionMemory(sessionId, queryText, params = {}) {
    const query = new URLSearchParams({
        q: queryText,
        limit: String(params.limit ?? 8),
    });
    if (params.kind) {
        query.set('kind', params.kind);
    }
    if (params.entity) {
        query.set('entity', params.entity);
    }
    const result = await apiGet(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/memory/search?${query.toString()}`, 'Failed to search RoleSession memory');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to search RoleSession memory');
    }
    if (result.data.ok === false || !Array.isArray(result.data.items)) {
        return fail(responseError(result.data, 'RoleSession memory search response missing items'));
    }
    return { ok: true, data: normalizeMemoryItems(result.data.items) };
}
export async function readRoleSessionMemoryArtifact(sessionId, artifactId) {
    const result = await apiGet(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/memory/artifacts/${encodeURIComponent(artifactId)}`, 'Failed to read RoleSession memory artifact');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to read RoleSession memory artifact');
    }
    if (result.data.ok === false) {
        return fail(responseError(result.data, 'Failed to read RoleSession memory artifact'));
    }
    return { ok: true, data: result.data.artifact ?? result.data };
}
export async function readRoleSessionMemoryEpisode(sessionId, episodeId) {
    const result = await apiGet(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/memory/episodes/${encodeURIComponent(episodeId)}`, 'Failed to read RoleSession memory episode');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to read RoleSession memory episode');
    }
    if (result.data.ok === false) {
        return fail(responseError(result.data, 'Failed to read RoleSession memory episode'));
    }
    return { ok: true, data: result.data.episode ?? result.data };
}
export async function readRoleSessionMemoryState(sessionId, statePath) {
    const result = await apiGet(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/memory/state?path=${encodeURIComponent(statePath)}`, 'Failed to read RoleSession memory state');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to read RoleSession memory state');
    }
    if (result.data.ok === false) {
        return fail(responseError(result.data, 'Failed to read RoleSession memory state'));
    }
    return { ok: true, data: result.data.value ?? result.data };
}
export async function exportRoleSessionSnapshot(sessionId, payload) {
    const result = await apiPost(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/actions/export`, payload, 'Failed to export RoleSession snapshot');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to export RoleSession snapshot');
    }
    if (result.data.ok === false) {
        return fail(responseError(result.data, 'Failed to export RoleSession snapshot'));
    }
    return { ok: true, data: result.data.export ?? null };
}
export async function exportRoleSessionToWorkflow(sessionId, payload) {
    const result = await apiPost(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/actions/export-to-workflow`, payload, 'Failed to export RoleSession to workflow');
    if (!result.ok || !result.data) {
        return fail(result.error || 'Failed to export RoleSession to workflow');
    }
    if (result.data.ok === false) {
        return fail(responseError(result.data, 'Failed to export RoleSession to workflow'));
    }
    return { ok: true, data: result.data };
}
