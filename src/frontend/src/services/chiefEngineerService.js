import { apiDelete, apiGet, apiPost } from './apiClient';
function workspaceQuerySuffix(workspace = '') {
    return workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
}
function appendWorkspaceQuery(path, workspace = '') {
    if (!workspace)
        return path;
    const separator = path.includes('?') ? '&' : '?';
    return `${path}${separator}workspace=${encodeURIComponent(workspace)}`;
}
export async function getChiefEngineerDiagnostics(workspace = '') {
    return apiGet(`/v2/chief-engineer/diagnostics${workspaceQuerySuffix(workspace)}`, 'Failed to load Chief Engineer diagnostics');
}
export async function generateChiefEngineerBlueprint(payload, workspace = '') {
    return apiPost(`/v2/chief-engineer/blueprints${workspaceQuerySuffix(workspace)}`, payload, 'Failed to generate Chief Engineer blueprint');
}
export async function bulkGenerateChiefEngineerBlueprints(payload, workspace = '') {
    return apiPost(`/v2/chief-engineer/blueprints/bulk${workspaceQuerySuffix(workspace)}`, payload, 'Failed to bulk generate Chief Engineer blueprints');
}
export async function getChiefEngineerBlueprintStatus(taskId, runId, workspace = '') {
    const query = new URLSearchParams({ task_id: taskId });
    if (runId) {
        query.set('run_id', runId);
    }
    if (workspace) {
        query.set('workspace', workspace);
    }
    return apiGet(`/v2/chief-engineer/blueprints/status?${query.toString()}`, 'Failed to load Chief Engineer blueprint status');
}
export async function listChiefEngineerBlueprints(workspace = '') {
    return apiGet(`/v2/chief-engineer/blueprints${workspaceQuerySuffix(workspace)}`, 'Failed to list Chief Engineer blueprints');
}
export async function getChiefEngineerBlueprint(blueprintId, workspace = '') {
    return apiGet(appendWorkspaceQuery(`/v2/chief-engineer/blueprints/${encodeURIComponent(blueprintId)}`, workspace), 'Failed to load Chief Engineer blueprint');
}
export async function deleteChiefEngineerBlueprint(blueprintId, workspace = '') {
    return apiDelete(appendWorkspaceQuery(`/v2/chief-engineer/blueprints/${encodeURIComponent(blueprintId)}`, workspace), 'Failed to delete Chief Engineer blueprint');
}
export async function registerChiefEngineerRisk(payload, workspace = '') {
    return apiPost(`/v2/chief-engineer/risks${workspaceQuerySuffix(workspace)}`, payload, 'Failed to register Chief Engineer risk');
}
export async function listChiefEngineerRisks(filters = {}, workspace = '') {
    const query = new URLSearchParams();
    if (workspace)
        query.set('workspace', workspace);
    if (filters.taskId)
        query.set('task_id', filters.taskId);
    if (filters.severity)
        query.set('severity', filters.severity);
    if (filters.status)
        query.set('status', filters.status);
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return apiGet(`/v2/chief-engineer/risks${suffix}`, 'Failed to list Chief Engineer risks');
}
export async function updateChiefEngineerRiskStatus(riskId, status, note = '', workspace = '') {
    return apiPost(appendWorkspaceQuery(`/v2/chief-engineer/risks/${encodeURIComponent(riskId)}/status`, workspace), { status, note }, 'Failed to update Chief Engineer risk status');
}
export async function registerChiefEngineerTechDebt(payload, workspace = '') {
    return apiPost(`/v2/chief-engineer/tech-debt${workspaceQuerySuffix(workspace)}`, payload, 'Failed to register Chief Engineer tech debt');
}
export async function listChiefEngineerTechDebt(filters = {}, workspace = '') {
    const query = new URLSearchParams();
    if (workspace)
        query.set('workspace', workspace);
    if (filters.severity)
        query.set('severity', filters.severity);
    if (filters.surface)
        query.set('surface', filters.surface);
    if (filters.status)
        query.set('status', filters.status);
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return apiGet(`/v2/chief-engineer/tech-debt${suffix}`, 'Failed to list Chief Engineer tech debt');
}
export async function updateChiefEngineerTechDebtStatus(debtId, status, note = '', workspace = '') {
    return apiPost(appendWorkspaceQuery(`/v2/chief-engineer/tech-debt/${encodeURIComponent(debtId)}/status`, workspace), { status, note }, 'Failed to update Chief Engineer tech debt status');
}
export async function registerChiefEngineerADR(payload, workspace = '') {
    return apiPost(`/v2/chief-engineer/adrs${workspaceQuerySuffix(workspace)}`, payload, 'Failed to record Chief Engineer ADR');
}
export async function listChiefEngineerADRs(filters = {}, workspace = '') {
    const query = new URLSearchParams();
    if (workspace)
        query.set('workspace', workspace);
    if (filters.status)
        query.set('status', filters.status);
    if (filters.taskId)
        query.set('task_id', filters.taskId);
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return apiGet(`/v2/chief-engineer/adrs${suffix}`, 'Failed to list Chief Engineer ADRs');
}
export async function updateChiefEngineerADRStatus(adrId, status, note = '', workspace = '') {
    return apiPost(appendWorkspaceQuery(`/v2/chief-engineer/adrs/${encodeURIComponent(adrId)}/status`, workspace), { status, note }, 'Failed to update Chief Engineer ADR status');
}
export async function getChiefEngineerHandoffDecision(blueprintId, workspace = '') {
    const query = new URLSearchParams({ blueprint_id: blueprintId });
    if (workspace)
        query.set('workspace', workspace);
    return apiGet(`/v2/chief-engineer/handoff-decision?${query.toString()}`, 'Failed to load Chief Engineer handoff decision');
}
export async function registerChiefEngineerTechRadar(payload, workspace = '') {
    return apiPost(`/v2/chief-engineer/tech-radar${workspaceQuerySuffix(workspace)}`, payload, 'Failed to register Chief Engineer tech radar entry');
}
export async function listChiefEngineerTechRadar(ring, workspace = '') {
    const query = new URLSearchParams();
    if (workspace)
        query.set('workspace', workspace);
    if (ring)
        query.set('ring', ring);
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return apiGet(`/v2/chief-engineer/tech-radar${suffix}`, 'Failed to list Chief Engineer tech radar');
}
export async function updateChiefEngineerTechRadarRing(entryId, ring, note = '', workspace = '') {
    return apiPost(appendWorkspaceQuery(`/v2/chief-engineer/tech-radar/${encodeURIComponent(entryId)}/ring`, workspace), { ring, note }, 'Failed to update Chief Engineer tech radar ring');
}
export async function checkChiefEngineerStackPolicy(libraries, workspace = '') {
    return apiPost(`/v2/chief-engineer/stack-policy/check${workspaceQuerySuffix(workspace)}`, { libraries }, 'Failed to check Chief Engineer stack policy');
}
export async function registerChiefEngineerPostMortem(payload, workspace = '') {
    return apiPost(`/v2/chief-engineer/post-mortems${workspaceQuerySuffix(workspace)}`, payload, 'Failed to record Chief Engineer post-mortem');
}
export async function listChiefEngineerPostMortems(filters = {}, workspace = '') {
    const query = new URLSearchParams();
    if (workspace)
        query.set('workspace', workspace);
    if (filters.severity)
        query.set('severity', filters.severity);
    if (filters.status)
        query.set('status', filters.status);
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return apiGet(`/v2/chief-engineer/post-mortems${suffix}`, 'Failed to list Chief Engineer post-mortems');
}
export async function updateChiefEngineerPostMortemStatus(incidentId, status, note = '', workspace = '') {
    return apiPost(appendWorkspaceQuery(`/v2/chief-engineer/post-mortems/${encodeURIComponent(incidentId)}/status`, workspace), { status, note }, 'Failed to update Chief Engineer post-mortem status');
}
export async function getChiefEngineerReleaseReadiness(options = {}, workspace = '') {
    const query = new URLSearchParams();
    if (workspace)
        query.set('workspace', workspace);
    if (options.blueprintIds?.length)
        query.set('blueprint_ids', options.blueprintIds.join(','));
    if (options.libraries?.length)
        query.set('libraries', options.libraries.join(','));
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return apiGet(`/v2/chief-engineer/release-readiness${suffix}`, 'Failed to load Chief Engineer release readiness');
}
