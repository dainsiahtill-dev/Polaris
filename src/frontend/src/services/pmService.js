/**
 * PM Service
 *
 * 封装所有PM相关的API调用，提供类型安全的接口
 */
import { apiDelete, apiGet, apiPost, apiPostEmpty } from './apiClient';
// ============================================================================
// Status Services
// ============================================================================
/**
 * 获取PM状态
 */
function workspaceQuerySuffix(workspace = '') {
    return workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
}
function setWorkspaceQuery(query, workspace) {
    if (workspace) {
        query.set('workspace', workspace);
    }
}
export async function getPmStatus(workspace = '') {
    return apiGet(`/v2/pm/status${workspaceQuerySuffix(workspace)}`, 'Failed to load PM status');
}
/**
 * 获取 PM 启动诊断快照。
 */
export async function getPmStartupDiagnostics(workspace = '') {
    return apiGet(`/v2/pm/diagnostics${workspaceQuerySuffix(workspace)}`, 'Failed to load PM diagnostics');
}
export async function getPmManagementStatus(workspace = '') {
    return apiGet(`/v2/pm/management/status${workspaceQuerySuffix(workspace)}`, 'Failed to load PM management status');
}
export async function getPmManagementHealth(workspace = '') {
    return apiGet(`/v2/pm/management/health${workspaceQuerySuffix(workspace)}`, 'Failed to load PM management health');
}
export async function initializePmManagement(payload = {}, workspace = '') {
    const query = new URLSearchParams();
    if (payload.projectName) {
        query.set('project_name', payload.projectName);
    }
    if (payload.description) {
        query.set('description', payload.description);
    }
    setWorkspaceQuery(query, workspace);
    const suffix = query.toString();
    return apiPostEmpty(suffix ? `/v2/pm/management/init?${suffix}` : '/v2/pm/management/init', 'Failed to initialize PM management');
}
function roleKernelBasePath(role) {
    if (role === 'pm')
        return '/v2/pm';
    if (role === 'chief_engineer')
        return '/v2/chief-engineer';
    return '/v2/director';
}
function appendKernelLLMEventQuery(path, query) {
    const params = new URLSearchParams();
    if (query.runId) {
        params.set('run_id', query.runId);
    }
    if (query.taskId) {
        params.set('task_id', query.taskId);
    }
    if (query.role) {
        params.set('role', query.role);
    }
    if (typeof query.limit === 'number') {
        params.set('limit', String(query.limit));
    }
    if (query.workspace) {
        params.set('workspace', query.workspace);
    }
    const suffix = params.toString();
    return suffix ? `${path}?${suffix}` : path;
}
export async function getRoleKernelCacheStats(role) {
    return apiGet(`${roleKernelBasePath(role)}/cache-stats`, `Failed to load ${role} cache stats`);
}
export async function clearRoleKernelCache(role) {
    return apiPostEmpty(`${roleKernelBasePath(role)}/cache-clear`, `Failed to clear ${role} cache`);
}
export async function getRoleKernelTokenBudgetStats(role) {
    return apiGet(`${roleKernelBasePath(role)}/token-budget-stats`, `Failed to load ${role} token budget stats`);
}
export async function getRoleKernelLLMEvents(role, query = {}) {
    return apiGet(appendKernelLLMEventQuery(`${roleKernelBasePath(role)}/llm-events`, query), `Failed to load ${role} LLM events`);
}
export async function getDirectorTaskKernelLLMEvents(taskId, query = {}) {
    return apiGet(appendKernelLLMEventQuery(`/v2/director/tasks/${encodeURIComponent(taskId)}/llm-events`, query), 'Failed to load Director task LLM events');
}
/**
 * 获取Director状态
 */
export async function getDirectorStatus(workspace = '') {
    const query = new URLSearchParams({ source: 'auto' });
    setWorkspaceQuery(query, workspace);
    const result = await apiGet(`/v2/director/status?${query.toString()}`, 'Failed to load Director status');
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
            },
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
            status: raw,
        },
    };
}
/**
 * 获取所有进程状态
 */
export async function getAllStatuses(workspace = '') {
    const [pm, director] = await Promise.all([
        getPmStatus(workspace),
        getDirectorStatus(workspace),
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
export async function startPm(resume = false, workspace = '') {
    const query = new URLSearchParams();
    if (resume) {
        query.set('resume', 'true');
    }
    setWorkspaceQuery(query, workspace);
    const suffix = query.toString();
    const path = suffix ? `/v2/pm/start?${suffix}` : '/v2/pm/start';
    return apiPostEmpty(path, 'Failed to start PM');
}
/**
 * 停止PM
 */
export async function stopPm(workspace = '') {
    return apiPostEmpty(`/v2/pm/stop${workspaceQuerySuffix(workspace)}`, 'Failed to stop PM');
}
/**
 * 单次运行PM
 */
export async function runPmOnce(workspace = '') {
    return apiPostEmpty(`/v2/pm/run_once${workspaceQuerySuffix(workspace)}`, 'PM Run Once failed');
}
/**
 * 启动Director
 */
export async function startDirector(workspace = '') {
    return apiPostEmpty(`/v2/director/start${workspaceQuerySuffix(workspace)}`, 'Failed to start Director');
}
/**
 * 停止Director
 */
export async function stopDirector(workspace = '') {
    return apiPostEmpty(`/v2/director/stop${workspaceQuerySuffix(workspace)}`, 'Failed to stop Director');
}
/**
 * 获取Director任务列表
 */
export async function listDirectorTasks(source, workspace = '') {
    const query = new URLSearchParams();
    if (source) {
        query.set('source', source);
    }
    setWorkspaceQuery(query, workspace);
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return apiGet(`/v2/director/tasks${suffix}`, 'Failed to list Director tasks');
}
export async function getDirectorTask(taskId, workspace = '') {
    return apiGet(`/v2/director/tasks/${encodeURIComponent(taskId)}${workspaceQuerySuffix(workspace)}`, 'Failed to load Director task');
}
export async function listDirectorWorkers(workspace = '') {
    return apiGet(`/v2/director/workers${workspaceQuerySuffix(workspace)}`, 'Failed to list Director workers');
}
export async function getDirectorWorker(workerId, workspace = '') {
    return apiGet(`/v2/director/workers/${encodeURIComponent(workerId)}${workspaceQuerySuffix(workspace)}`, 'Failed to load Director worker');
}
function readDirectorTaskMetadata(task) {
    return task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
}
function isDirectorTaskSnapshotRow(value) {
    return Boolean(value && typeof value === 'object' && String(value.id || '').trim());
}
export function resolveDirectorTaskSources(directorRunning) {
    return directorRunning ? ['workflow', 'local'] : ['auto', 'local'];
}
/**
 * Load Director task snapshot rows for explicit desktop task actions.
 *
 * Runtime push rows own volatile execution state; these backend rows fill in
 * task-contract details such as PM linkage, blueprint refs, steps, and
 * acceptance criteria after a user-issued create/cancel command.
 */
export async function listDirectorTaskSnapshotRows(directorRunning, workspace = '') {
    const rows = new Map();
    let sawSuccessfulSource = false;
    let lastError = '';
    for (const source of resolveDirectorTaskSources(directorRunning)) {
        const result = await listDirectorTasks(source, workspace);
        if (!result.ok || !Array.isArray(result.data)) {
            lastError = result.error || lastError;
            continue;
        }
        sawSuccessfulSource = true;
        for (const item of result.data) {
            if (!isDirectorTaskSnapshotRow(item)) {
                continue;
            }
            rows.set(String(item.id), {
                ...item,
                metadata: {
                    ...readDirectorTaskMetadata(item),
                    director_task_source: source,
                },
            });
        }
    }
    if (!sawSuccessfulSource && lastError) {
        return { ok: false, error: lastError };
    }
    return { ok: true, data: Array.from(rows.values()) };
}
/**
 * 获取 PM 任务合同列表。
 */
export async function listPmTasks(params = {}) {
    const query = new URLSearchParams();
    if (params.status)
        query.set('status', params.status);
    if (params.assignee)
        query.set('assignee', params.assignee);
    query.set('limit', String(params.limit ?? 100));
    query.set('offset', String(params.offset ?? 0));
    setWorkspaceQuery(query, params.workspace);
    return apiGet(`/v2/pm/tasks?${query.toString()}`, 'Failed to list PM tasks');
}
/**
 * 获取 PM 任务合同详情。
 */
export async function getPmTask(taskId, workspace = '') {
    return apiGet(`/v2/pm/tasks/${encodeURIComponent(taskId)}${workspaceQuerySuffix(workspace)}`, 'Failed to load PM task');
}
/**
 * 获取 PM 任务分配历史。
 */
export async function listPmTaskAssignments(taskId, limit = 100, workspace = '') {
    const query = new URLSearchParams();
    query.set('limit', String(limit));
    setWorkspaceQuery(query, workspace);
    return apiGet(`/v2/pm/tasks/${encodeURIComponent(taskId)}/assignments?${query.toString()}`, 'Failed to list PM task assignments');
}
/**
 * 获取 PM 需求列表。
 */
export async function listPmRequirements(params = {}) {
    const query = new URLSearchParams();
    if (params.status)
        query.set('status', params.status);
    if (params.priority)
        query.set('priority', params.priority);
    query.set('limit', String(params.limit ?? 100));
    query.set('offset', String(params.offset ?? 0));
    setWorkspaceQuery(query, params.workspace);
    return apiGet(`/v2/pm/requirements?${query.toString()}`, 'Failed to list PM requirements');
}
/**
 * 获取 PM 需求详情。
 */
export async function getPmRequirement(requirementId, workspace = '') {
    return apiGet(`/v2/pm/requirements/${encodeURIComponent(requirementId)}${workspaceQuerySuffix(workspace)}`, 'Failed to load PM requirement');
}
/**
 * 获取 PM 任务历史。
 */
export async function listPmTaskHistory(params = {}) {
    const query = new URLSearchParams();
    if (params.taskId)
        query.set('task_id', params.taskId);
    if (params.assignee)
        query.set('assignee', params.assignee);
    if (params.status)
        query.set('status', params.status);
    query.set('limit', String(params.limit ?? 50));
    query.set('offset', String(params.offset ?? 0));
    setWorkspaceQuery(query, params.workspace);
    return apiGet(`/v2/pm/tasks/history?${query.toString()}`, 'Failed to list PM task history');
}
/**
 * 获取 PM 分发给 Director 的任务历史。
 */
export async function listPmDirectorTaskHistory(params = {}) {
    const query = new URLSearchParams();
    if (typeof params.iteration === 'number')
        query.set('iteration', String(params.iteration));
    query.set('limit', String(params.limit ?? 25));
    query.set('offset', String(params.offset ?? 0));
    setWorkspaceQuery(query, params.workspace);
    return apiGet(`/v2/pm/tasks/director?${query.toString()}`, 'Failed to list PM Director task history');
}
/**
 * 搜索 PM 任务合同。
 */
export async function searchPmTasks(queryText, limit = 20, workspace = '') {
    const query = new URLSearchParams();
    query.set('q', queryText);
    query.set('limit', String(limit));
    setWorkspaceQuery(query, workspace);
    return apiGet(`/v2/pm/search/tasks?${query.toString()}`, 'Failed to search PM tasks');
}
/**
 * 创建Director任务
 */
export async function createDirectorTask(payload, workspace = '') {
    return apiPost(`/v2/director/tasks${workspaceQuerySuffix(workspace)}`, payload, 'Failed to create Director task');
}
export async function cancelDirectorTask(taskId, workspace = '') {
    return apiPostEmpty(`/v2/director/tasks/${encodeURIComponent(taskId)}/cancel${workspaceQuerySuffix(workspace)}`, 'Failed to cancel Director task');
}
/**
 * 通过统一编排入口运行 Director，可选绑定单个任务。
 */
export async function runDirector(payload) {
    return apiPost('/v2/director/run', payload, 'Failed to run Director');
}
/**
 * 通过统一编排入口运行 PM。
 */
export async function runPm(payload) {
    return apiPost('/v2/pm/run', payload, 'Failed to run PM');
}
export async function getPmRun(runId, workspace = '') {
    return apiGet(`/v2/pm/runs/${encodeURIComponent(runId)}${workspaceQuerySuffix(workspace)}`, 'Failed to load PM run');
}
export async function cancelPmRun(runId, workspace = '') {
    return apiPostEmpty(`/v2/pm/runs/${encodeURIComponent(runId)}/cancel${workspaceQuerySuffix(workspace)}`, 'Failed to cancel PM run');
}
export async function getDirectorRun(runId, workspace = '') {
    return apiGet(`/v2/director/runs/${encodeURIComponent(runId)}${workspaceQuerySuffix(workspace)}`, 'Failed to load Director run');
}
export async function cancelDirectorRun(runId, workspace = '') {
    return apiPostEmpty(`/v2/director/runs/${encodeURIComponent(runId)}/cancel${workspaceQuerySuffix(workspace)}`, 'Failed to cancel Director run');
}
/**
 * 获取 Director 在各宿主下的能力矩阵。
 */
export async function getDirectorCapabilities() {
    return apiGet('/v2/director/capabilities', 'Failed to load Director capabilities');
}
/**
 * 获取 Director 桌面运行前诊断。
 */
export async function getDirectorDiagnostics(workspace = '') {
    return apiGet(`/v2/director/diagnostics${workspaceQuerySuffix(workspace)}`, 'Failed to load Director diagnostics');
}
function encodeDocumentPath(path) {
    return path
        .replace(/\\/g, '/')
        .split('/')
        .map((segment) => encodeURIComponent(segment))
        .join('/');
}
export const pmDocumentService = {
    list(workspace = '') {
        return apiGet(`/v2/pm/documents${workspaceQuerySuffix(workspace)}`, 'Failed to list PM documents');
    },
    get(path, version, workspace = '') {
        const query = new URLSearchParams();
        if (version) {
            query.set('version', version);
        }
        setWorkspaceQuery(query, workspace);
        const suffix = query.toString();
        return apiGet(`/v2/pm/documents/${encodeDocumentPath(path)}${suffix ? `?${suffix}` : ''}`, 'Failed to read PM document');
    },
    save(path, content, changeSummary, workspace = '') {
        return apiPost(`/v2/pm/documents/${encodeDocumentPath(path)}${workspaceQuerySuffix(workspace)}`, { content, change_summary: changeSummary }, 'Failed to save PM document');
    },
    delete(path, deleteFile = true, workspace = '') {
        const query = new URLSearchParams();
        query.set('delete_file', String(deleteFile));
        setWorkspaceQuery(query, workspace);
        return apiDelete(`/v2/pm/documents/${encodeDocumentPath(path)}?${query.toString()}`, 'Failed to delete PM document');
    },
    versions(path, workspace = '') {
        return apiGet(`/v2/pm/documents/${encodeDocumentPath(path)}/versions${workspaceQuerySuffix(workspace)}`, 'Failed to list PM document versions');
    },
    compare(path, oldVersion, newVersion, workspace = '') {
        const query = new URLSearchParams();
        query.set('old_version', oldVersion);
        query.set('new_version', newVersion);
        setWorkspaceQuery(query, workspace);
        return apiGet(`/v2/pm/documents/${encodeDocumentPath(path)}/compare?${query.toString()}`, 'Failed to compare PM document versions');
    },
    search(queryText, limit = 20, workspace = '') {
        const query = new URLSearchParams();
        query.set('q', queryText);
        query.set('limit', String(limit));
        setWorkspaceQuery(query, workspace);
        return apiGet(`/v2/pm/search/documents?${query.toString()}`, 'Failed to search PM documents');
    },
};
