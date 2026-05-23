/**
 * PM Service
 *
 * 封装所有PM相关的API调用，提供类型安全的接口
 */

import { apiDelete, apiGet, apiPost, apiPostEmpty } from './apiClient';
import type { ApiResult } from './api.types';
import type { ProcessStatus, DirectorStatusPayload } from './api.types';

// ============================================================================
// Status Types
// ============================================================================

export interface PmStatus extends ProcessStatus {}
export interface DirectorStatus extends ProcessStatus {}

export interface PmDiagnosticsLanceDBStatus {
  ok: boolean;
  state: string;
  error?: string | null;
  details?: Record<string, unknown>;
}

export interface PmDiagnosticsLLMStatus {
  ok: boolean;
  state: string;
  blocked_roles?: string[];
  unsupported_roles?: string[];
  required_ready_roles?: string[];
  error?: string | null;
  details?: Record<string, unknown>;
}

export interface PmDiagnosticsWorkspaceStatus {
  ok: boolean;
  status: string;
  workspace: string;
  docs_present: boolean;
  error?: string | null;
}

export interface PmStartupDiagnosticsResponse {
  ok: boolean;
  generated_at: string;
  lancedb: PmDiagnosticsLanceDBStatus;
  llm: PmDiagnosticsLLMStatus;
  workspace: PmDiagnosticsWorkspaceStatus;
  issues: string[];
}

export interface PmManagementStatusResponse extends Record<string, unknown> {
  initialized: boolean;
  workspace: string;
  project?: string | null;
  version?: string | null;
  stats?: Record<string, unknown> | null;
  storage?: Record<string, unknown> | null;
}

export interface PmManagementHealthResponse extends Record<string, unknown> {
  overall: string;
  components: Record<string, string>;
  metrics: Record<string, number>;
  recommendations: string[];
}

export interface PmManagementInitResponse extends Record<string, unknown> {
  initialized: boolean;
  workspace: string;
  project_name?: string | null;
  pm_version?: string | null;
  message?: string | null;
}

export interface PmManagementInitPayload {
  projectName?: string | null;
  description?: string | null;
}

export type RoleKernelDiagnosticsRole = 'pm' | 'director' | 'chief_engineer';

export interface RoleKernelCacheStats extends Record<string, unknown> {
  hits?: number;
  misses?: number;
  evictions?: number;
  size?: number;
  max_size?: number;
  hit_rate?: number;
  enabled?: boolean;
}

export interface RoleKernelCacheClearResponse {
  ok: boolean;
  message: string;
}

export interface RoleKernelTokenBudgetStats extends Record<string, unknown> {
  system_context?: number;
  task_context?: number;
  conversation?: number;
  override?: number;
  safety_margin?: number;
  total?: number;
  available_conversation?: number;
  section_breakdown?: Record<string, unknown>;
}

export interface RoleKernelLLMEvent extends Record<string, unknown> {
  event_type?: string;
  role?: string;
  run_id?: string | null;
  task_id?: string | null;
  timestamp?: string | number | null;
}

export interface RoleKernelLLMEventsResponse extends Record<string, unknown> {
  run_id?: string | null;
  task_id?: string | null;
  events: RoleKernelLLMEvent[];
  count?: number;
  stats?: Record<string, unknown>;
}

export interface RoleKernelLLMEventsQuery {
  runId?: string | null;
  taskId?: string | null;
  role?: string | null;
  limit?: number;
}

// ============================================================================
// Status Services
// ============================================================================

/**
 * 获取PM状态
 */
export async function getPmStatus(): Promise<ApiResult<PmStatus>> {
  return apiGet<PmStatus>('/v2/pm/status', 'Failed to load PM status');
}

/**
 * 获取 PM 启动诊断快照。
 */
export async function getPmStartupDiagnostics(): Promise<ApiResult<PmStartupDiagnosticsResponse>> {
  return apiGet<PmStartupDiagnosticsResponse>(
    '/v2/pm/diagnostics',
    'Failed to load PM diagnostics',
  );
}

export async function getPmManagementStatus(): Promise<ApiResult<PmManagementStatusResponse>> {
  return apiGet<PmManagementStatusResponse>(
    '/pm/v2/pm/status',
    'Failed to load PM management status',
  );
}

export async function getPmManagementHealth(): Promise<ApiResult<PmManagementHealthResponse>> {
  return apiGet<PmManagementHealthResponse>(
    '/pm/v2/pm/health',
    'Failed to load PM management health',
  );
}

export async function initializePmManagement(
  payload: PmManagementInitPayload = {},
): Promise<ApiResult<PmManagementInitResponse>> {
  const query = new URLSearchParams();
  if (payload.projectName) {
    query.set('project_name', payload.projectName);
  }
  if (payload.description) {
    query.set('description', payload.description);
  }
  const suffix = query.toString();
  return apiPostEmpty<PmManagementInitResponse>(
    suffix ? `/pm/v2/pm/init?${suffix}` : '/pm/v2/pm/init',
    'Failed to initialize PM management',
  );
}

function roleKernelBasePath(role: RoleKernelDiagnosticsRole): string {
  if (role === 'pm') return '/v2/pm';
  if (role === 'chief_engineer') return '/v2/chief-engineer';
  return '/v2/director';
}

function appendKernelLLMEventQuery(path: string, query: RoleKernelLLMEventsQuery): string {
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

  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export async function getRoleKernelCacheStats(
  role: RoleKernelDiagnosticsRole,
): Promise<ApiResult<RoleKernelCacheStats>> {
  return apiGet<RoleKernelCacheStats>(
    `${roleKernelBasePath(role)}/cache-stats`,
    `Failed to load ${role} cache stats`,
  );
}

export async function clearRoleKernelCache(
  role: RoleKernelDiagnosticsRole,
): Promise<ApiResult<RoleKernelCacheClearResponse>> {
  return apiPostEmpty<RoleKernelCacheClearResponse>(
    `${roleKernelBasePath(role)}/cache-clear`,
    `Failed to clear ${role} cache`,
  );
}

export async function getRoleKernelTokenBudgetStats(
  role: RoleKernelDiagnosticsRole,
): Promise<ApiResult<RoleKernelTokenBudgetStats>> {
  return apiGet<RoleKernelTokenBudgetStats>(
    `${roleKernelBasePath(role)}/token-budget-stats`,
    `Failed to load ${role} token budget stats`,
  );
}

export async function getRoleKernelLLMEvents(
  role: RoleKernelDiagnosticsRole,
  query: RoleKernelLLMEventsQuery = {},
): Promise<ApiResult<RoleKernelLLMEventsResponse>> {
  return apiGet<RoleKernelLLMEventsResponse>(
    appendKernelLLMEventQuery(`${roleKernelBasePath(role)}/llm-events`, query),
    `Failed to load ${role} LLM events`,
  );
}

export async function getDirectorTaskKernelLLMEvents(
  taskId: string,
  query: Omit<RoleKernelLLMEventsQuery, 'taskId' | 'role'> = {},
): Promise<ApiResult<RoleKernelLLMEventsResponse>> {
  return apiGet<RoleKernelLLMEventsResponse>(
    appendKernelLLMEventQuery(
      `/v2/director/tasks/${encodeURIComponent(taskId)}/llm-events`,
      query,
    ),
    'Failed to load Director task LLM events',
  );
}

/**
 * 获取Director状态
 */
export async function getDirectorStatus(): Promise<ApiResult<DirectorStatus>> {
  const result = await apiGet<DirectorStatusPayload>('/v2/director/status?source=auto', 'Failed to load Director status');

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
      } as DirectorStatus,
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
      status: raw as Record<string, unknown>,
    } as DirectorStatus,
  };
}

/**
 * 获取所有进程状态
 */
export async function getAllStatuses(): Promise<{
  pm: ApiResult<PmStatus>;
  director: ApiResult<DirectorStatus>;
}> {
  const [pm, director] = await Promise.all([
    getPmStatus(),
    getDirectorStatus(),
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
export async function startPm(resume = false): Promise<ApiResult<void>> {
  const path = resume ? '/v2/pm/start?resume=true' : '/v2/pm/start';
  return apiPostEmpty<void>(path, 'Failed to start PM');
}

/**
 * 停止PM
 */
export async function stopPm(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/pm/stop', 'Failed to stop PM');
}

/**
 * 单次运行PM
 */
export async function runPmOnce(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/pm/run_once', 'PM Run Once failed');
}

/**
 * 启动Director
 */
export async function startDirector(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/director/start', 'Failed to start Director');
}

/**
 * 停止Director
 */
export async function stopDirector(): Promise<ApiResult<void>> {
  return apiPostEmpty<void>('/v2/director/stop', 'Failed to stop Director');
}

// ============================================================================
// Director Task Queue Services
// ============================================================================

export interface DirectorTask {
  id: string;
  subject: string;
  description?: string;
  status?: string;
  priority?: string;
  claimed_by?: string | null;
  worker?: string | null;
  goal?: string;
  acceptance?: string[];
  target_files?: string[];
  dependencies?: string[];
  current_file?: string | null;
  error?: string | null;
  pm_task_id?: string | null;
  blueprint_id?: string | null;
  blueprint_path?: string | null;
  runtime_blueprint_path?: string | null;
  metadata?: {
    pm_task_id?: string;
    workflow_state?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export type DirectorTaskSource = 'auto' | 'workflow' | 'local';

export interface CreateDirectorTaskPayload {
  subject: string;
  description: string;
  command?: string | null;
  priority: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  timeout_seconds: number;
  metadata: {
    pm_task_id: string;
    pm_task_title: string;
    pm_task_status: string;
    acceptance: string[];
    blueprint_id?: string | null;
    blueprint_path?: string | null;
    runtime_blueprint_path?: string | null;
    guardrails?: unknown;
    no_touch_zones?: unknown;
    context_snapshot_ref?: string | null;
    [key: string]: unknown;
  };
}

export interface RunDirectorPayload {
  workspace: string;
  task_id?: string | null;
  task_filter?: string | null;
  max_workers?: number;
  execution_mode?: 'serial' | 'parallel';
}

export interface RunPmPayload {
  workspace: string;
  directive?: string;
  stage?: 'architect' | 'pm';
  run_director?: boolean;
  director_iterations?: number;
  metadata?: Record<string, unknown>;
}

export interface RunDirectorResponse {
  run_id: string;
  status: string;
  workspace: string;
  tasks_queued: number;
  message: string;
}

export interface PmOrchestrationRunResponse {
  run_id: string;
  status: string;
  workspace: string;
  stage: string;
  message: string;
  [key: string]: unknown;
}

export interface DirectorOrchestrationRunResponse {
  run_id: string;
  status: string;
  workspace: string;
  tasks_queued: number;
  message: string;
  [key: string]: unknown;
}

export interface DirectorCapabilitiesResponse {
  ok?: boolean;
  role?: string;
  capabilities?: Record<string, string[]> | string[];
  [key: string]: unknown;
}

export interface CancelDirectorTaskResponse {
  ok?: boolean;
  id?: string;
  task_id?: string;
  status?: string;
  cancelled?: boolean;
  [key: string]: unknown;
}

export interface DirectorFallbackTaskRow {
  id: string;
  metadata?: {
    director_task_source?: DirectorTaskSource;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface DirectorWorker {
  id: string;
  name?: string;
  status?: string;
  currentTaskId?: string | null;
  current_task_id?: string | null;
  task_id?: string | null;
  healthy?: boolean;
  tasksCompleted?: number;
  tasks_completed?: number;
  completed_tasks?: number;
  tasksFailed?: number;
  tasks_failed?: number;
  failed_tasks?: number;
  [key: string]: unknown;
}

export interface PmTaskHistoryEntry {
  id?: string;
  task_id?: string;
  title?: string;
  action?: string;
  status?: string;
  assignee?: string;
  timestamp?: string;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface PmTaskHistoryResponse {
  history?: PmTaskHistoryEntry[];
  pagination?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PmDirectorHistoryIteration {
  iteration?: number;
  tasks?: PmTaskHistoryEntry[];
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface PmDirectorTaskHistoryResponse {
  iterations?: PmDirectorHistoryIteration[];
  history?: PmTaskHistoryEntry[];
  pagination?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PmTaskSearchResult {
  id?: string;
  task_id?: string;
  title?: string;
  subject?: string;
  summary?: string;
  description?: string;
  status?: string;
  priority?: number | string;
  score?: number | null;
  [key: string]: unknown;
}

export interface PmTaskSearchResponse {
  query: string;
  results: PmTaskSearchResult[];
  count: number;
  [key: string]: unknown;
}

export interface PmTaskListParams {
  status?: string | null;
  assignee?: string | null;
  limit?: number;
  offset?: number;
}

export interface PmTaskListResponse {
  ok?: boolean;
  tasks?: PmTaskSearchResult[];
  items?: PmTaskSearchResult[];
  pagination?: Record<string, unknown>;
  total?: number;
  [key: string]: unknown;
}

export interface PmTaskAssignmentEntry {
  id?: string;
  task_id?: string;
  assignee?: string;
  assigned_to?: string;
  worker_id?: string;
  director_id?: string;
  status?: string;
  action?: string;
  created_at?: string;
  updated_at?: string;
  assigned_at?: string;
  [key: string]: unknown;
}

export interface PmTaskAssignmentsResponse {
  task_id: string;
  assignments: PmTaskAssignmentEntry[];
  count: number;
  [key: string]: unknown;
}

export interface PmRequirementEntry {
  id?: string;
  req_id?: string;
  title?: string;
  subject?: string;
  description?: string;
  status?: string;
  priority?: string | number;
  source?: string;
  source_doc?: string;
  acceptance_criteria?: string[];
  related_task_ids?: string[];
  tasks?: string[];
  created_at?: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PmRequirementListParams {
  status?: string | null;
  priority?: string | null;
  limit?: number;
  offset?: number;
}

export interface PmRequirementListResponse {
  ok?: boolean;
  requirements?: PmRequirementEntry[];
  items?: PmRequirementEntry[];
  pagination?: Record<string, unknown>;
  total?: number;
  [key: string]: unknown;
}

/**
 * 获取Director任务列表
 */
export async function listDirectorTasks(source?: string): Promise<ApiResult<DirectorTask[]>> {
  const query = source ? `?source=${encodeURIComponent(source)}` : '';
  return apiGet<DirectorTask[]>(`/v2/director/tasks${query}`, 'Failed to list Director tasks');
}

export async function getDirectorTask(taskId: string): Promise<ApiResult<DirectorTask>> {
  return apiGet<DirectorTask>(
    `/v2/director/tasks/${encodeURIComponent(taskId)}`,
    'Failed to load Director task',
  );
}

export async function listDirectorWorkers(): Promise<ApiResult<DirectorWorker[]>> {
  return apiGet<DirectorWorker[]>(
    '/v2/director/workers',
    'Failed to list Director workers',
  );
}

export async function getDirectorWorker(workerId: string): Promise<ApiResult<DirectorWorker>> {
  return apiGet<DirectorWorker>(
    `/v2/director/workers/${encodeURIComponent(workerId)}`,
    'Failed to load Director worker',
  );
}

function readDirectorTaskMetadata(task: DirectorTask): Record<string, unknown> {
  return task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
}

function isDirectorFallbackTaskRow(value: unknown): value is DirectorTask {
  return Boolean(value && typeof value === 'object' && String((value as { id?: unknown }).id || '').trim());
}

export function resolveDirectorTaskSources(directorRunning: boolean): DirectorTaskSource[] {
  return directorRunning ? ['workflow', 'local'] : ['auto', 'local'];
}

/**
 * Load Director fallback rows for desktop task boards.
 *
 * Runtime push rows own volatile execution state; these backend rows fill in
 * task-contract details such as PM linkage, blueprint refs, steps, and
 * acceptance criteria.
 */
export async function listDirectorTaskFallbackRows(
  directorRunning: boolean,
): Promise<ApiResult<DirectorFallbackTaskRow[]>> {
  const rows = new Map<string, DirectorFallbackTaskRow>();
  let sawSuccessfulSource = false;
  let lastError = '';

  for (const source of resolveDirectorTaskSources(directorRunning)) {
    const result = await listDirectorTasks(source);
    if (!result.ok || !Array.isArray(result.data)) {
      lastError = result.error || lastError;
      continue;
    }

    sawSuccessfulSource = true;
    for (const item of result.data) {
      if (!isDirectorFallbackTaskRow(item)) {
        continue;
      }
      rows.set(String(item.id), {
        ...(item as unknown as DirectorFallbackTaskRow),
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
export async function listPmTasks(params: PmTaskListParams = {}): Promise<ApiResult<PmTaskListResponse>> {
  const query = new URLSearchParams();
  if (params.status) query.set('status', params.status);
  if (params.assignee) query.set('assignee', params.assignee);
  query.set('limit', String(params.limit ?? 100));
  query.set('offset', String(params.offset ?? 0));
  return apiGet<PmTaskListResponse>(
    `/v2/pm/tasks?${query.toString()}`,
    'Failed to list PM tasks',
  );
}

/**
 * 获取 PM 任务合同详情。
 */
export async function getPmTask(taskId: string): Promise<ApiResult<PmTaskSearchResult>> {
  return apiGet<PmTaskSearchResult>(
    `/v2/pm/tasks/${encodeURIComponent(taskId)}`,
    'Failed to load PM task',
  );
}

/**
 * 获取 PM 任务分配历史。
 */
export async function listPmTaskAssignments(
  taskId: string,
  limit = 100,
): Promise<ApiResult<PmTaskAssignmentsResponse>> {
  const query = new URLSearchParams();
  query.set('limit', String(limit));
  return apiGet<PmTaskAssignmentsResponse>(
    `/v2/pm/tasks/${encodeURIComponent(taskId)}/assignments?${query.toString()}`,
    'Failed to list PM task assignments',
  );
}

/**
 * 获取 PM 需求列表。
 */
export async function listPmRequirements(
  params: PmRequirementListParams = {},
): Promise<ApiResult<PmRequirementListResponse>> {
  const query = new URLSearchParams();
  if (params.status) query.set('status', params.status);
  if (params.priority) query.set('priority', params.priority);
  query.set('limit', String(params.limit ?? 100));
  query.set('offset', String(params.offset ?? 0));
  return apiGet<PmRequirementListResponse>(
    `/v2/pm/requirements?${query.toString()}`,
    'Failed to list PM requirements',
  );
}

/**
 * 获取 PM 需求详情。
 */
export async function getPmRequirement(requirementId: string): Promise<ApiResult<PmRequirementEntry>> {
  return apiGet<PmRequirementEntry>(
    `/v2/pm/requirements/${encodeURIComponent(requirementId)}`,
    'Failed to load PM requirement',
  );
}

/**
 * 获取 PM 任务历史。
 */
export async function listPmTaskHistory(params: {
  taskId?: string | null;
  assignee?: string | null;
  status?: string | null;
  limit?: number;
  offset?: number;
} = {}): Promise<ApiResult<PmTaskHistoryResponse>> {
  const query = new URLSearchParams();
  if (params.taskId) query.set('task_id', params.taskId);
  if (params.assignee) query.set('assignee', params.assignee);
  if (params.status) query.set('status', params.status);
  query.set('limit', String(params.limit ?? 50));
  query.set('offset', String(params.offset ?? 0));
  return apiGet<PmTaskHistoryResponse>(
    `/v2/pm/tasks/history?${query.toString()}`,
    'Failed to list PM task history',
  );
}

/**
 * 获取 PM 分发给 Director 的任务历史。
 */
export async function listPmDirectorTaskHistory(params: {
  iteration?: number | null;
  limit?: number;
  offset?: number;
} = {}): Promise<ApiResult<PmDirectorTaskHistoryResponse>> {
  const query = new URLSearchParams();
  if (typeof params.iteration === 'number') query.set('iteration', String(params.iteration));
  query.set('limit', String(params.limit ?? 25));
  query.set('offset', String(params.offset ?? 0));
  return apiGet<PmDirectorTaskHistoryResponse>(
    `/v2/pm/tasks/director?${query.toString()}`,
    'Failed to list PM Director task history',
  );
}

/**
 * 搜索 PM 任务合同。
 */
export async function searchPmTasks(queryText: string, limit = 20): Promise<ApiResult<PmTaskSearchResponse>> {
  const query = new URLSearchParams();
  query.set('q', queryText);
  query.set('limit', String(limit));
  return apiGet<PmTaskSearchResponse>(
    `/v2/pm/search/tasks?${query.toString()}`,
    'Failed to search PM tasks',
  );
}

/**
 * 创建Director任务
 */
export async function createDirectorTask(payload: CreateDirectorTaskPayload): Promise<ApiResult<DirectorTask>> {
  return apiPost<DirectorTask>('/v2/director/tasks', payload, 'Failed to create Director task');
}

export async function cancelDirectorTask(taskId: string): Promise<ApiResult<CancelDirectorTaskResponse>> {
  return apiPostEmpty<CancelDirectorTaskResponse>(
    `/v2/director/tasks/${encodeURIComponent(taskId)}/cancel`,
    'Failed to cancel Director task',
  );
}

/**
 * 通过统一编排入口运行 Director，可选绑定单个任务。
 */
export async function runDirector(payload: RunDirectorPayload): Promise<ApiResult<RunDirectorResponse>> {
  return apiPost<RunDirectorResponse>('/v2/director/run', payload, 'Failed to run Director');
}

/**
 * 通过统一编排入口运行 PM。
 */
export async function runPm(payload: RunPmPayload): Promise<ApiResult<PmOrchestrationRunResponse>> {
  return apiPost<PmOrchestrationRunResponse>('/v2/pm/run', payload, 'Failed to run PM');
}

export async function getPmRun(runId: string): Promise<ApiResult<PmOrchestrationRunResponse>> {
  return apiGet<PmOrchestrationRunResponse>(
    `/v2/pm/runs/${encodeURIComponent(runId)}`,
    'Failed to load PM run',
  );
}

export async function cancelPmRun(runId: string): Promise<ApiResult<PmOrchestrationRunResponse>> {
  return apiPostEmpty<PmOrchestrationRunResponse>(
    `/v2/pm/runs/${encodeURIComponent(runId)}/cancel`,
    'Failed to cancel PM run',
  );
}

export async function getDirectorRun(runId: string): Promise<ApiResult<DirectorOrchestrationRunResponse>> {
  return apiGet<DirectorOrchestrationRunResponse>(
    `/v2/director/runs/${encodeURIComponent(runId)}`,
    'Failed to load Director run',
  );
}

export async function cancelDirectorRun(runId: string): Promise<ApiResult<DirectorOrchestrationRunResponse>> {
  return apiPostEmpty<DirectorOrchestrationRunResponse>(
    `/v2/director/runs/${encodeURIComponent(runId)}/cancel`,
    'Failed to cancel Director run',
  );
}

/**
 * 获取 Director 在各宿主下的能力矩阵。
 */
export async function getDirectorCapabilities(): Promise<ApiResult<DirectorCapabilitiesResponse>> {
  return apiGet<DirectorCapabilitiesResponse>(
    '/v2/director/capabilities',
    'Failed to load Director capabilities',
  );
}

// ============================================================================
// PM Document Services
// ============================================================================

export interface PmDocumentInfo {
  path: string;
  current_version: string | number;
  version_count: number;
  last_modified: string;
  created_at: string;
}

export interface PmDocumentListResponse {
  documents: PmDocumentInfo[];
  pagination: Record<string, unknown>;
}

export interface PmDocumentDetailResponse extends PmDocumentInfo {
  content?: string | null;
  versions?: Array<Record<string, unknown>> | null;
  analysis?: Record<string, unknown> | null;
}

export interface PmDocumentWriteResponse {
  success: boolean;
  path: string;
  version?: string | null;
  checksum?: string | null;
}

export interface PmDocumentDeleteResponse {
  success: boolean;
  path: string;
  deleted: boolean;
}

export interface PmDocumentVersionInfo {
  version: string;
  created_at: string;
  created_by: string;
  change_summary: string;
  checksum: string;
}

export interface PmDocumentVersionsResponse {
  path: string;
  versions: PmDocumentVersionInfo[];
}

export interface PmDocumentDiffResponse {
  path: string;
  old_version: string;
  new_version: string;
  diff_text: string;
  changed_sections: string[];
  added_requirements: string[];
  removed_requirements: string[];
  impact_score: number;
}

export interface PmDocumentSearchResult {
  path: string;
  snippet?: string | null;
  score?: number | null;
  line?: number | null;
  line_number?: number | null;
  current_version?: string | number | null;
  last_modified?: string | null;
  [key: string]: unknown;
}

export interface PmDocumentSearchResponse {
  query: string;
  results: PmDocumentSearchResult[];
  count: number;
  [key: string]: unknown;
}

function encodeDocumentPath(path: string): string {
  return path
    .replace(/\\/g, '/')
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/');
}

export const pmDocumentService = {
  list(): Promise<ApiResult<PmDocumentListResponse>> {
    return apiGet<PmDocumentListResponse>('/v2/pm/documents', 'Failed to list PM documents');
  },

  get(path: string, version?: string | null): Promise<ApiResult<PmDocumentDetailResponse>> {
    const query = new URLSearchParams();
    if (version) {
      query.set('version', version);
    }
    const suffix = query.toString();
    return apiGet<PmDocumentDetailResponse>(
      `/v2/pm/documents/${encodeDocumentPath(path)}${suffix ? `?${suffix}` : ''}`,
      'Failed to read PM document',
    );
  },

  save(path: string, content: string, changeSummary: string): Promise<ApiResult<PmDocumentWriteResponse>> {
    return apiPost<PmDocumentWriteResponse>(
      `/v2/pm/documents/${encodeDocumentPath(path)}`,
      { content, change_summary: changeSummary },
      'Failed to save PM document',
    );
  },

  delete(path: string, deleteFile = true): Promise<ApiResult<PmDocumentDeleteResponse>> {
    const query = new URLSearchParams();
    query.set('delete_file', String(deleteFile));
    return apiDelete<PmDocumentDeleteResponse>(
      `/v2/pm/documents/${encodeDocumentPath(path)}?${query.toString()}`,
      'Failed to delete PM document',
    );
  },

  versions(path: string): Promise<ApiResult<PmDocumentVersionsResponse>> {
    return apiGet<PmDocumentVersionsResponse>(
      `/v2/pm/documents/${encodeDocumentPath(path)}/versions`,
      'Failed to list PM document versions',
    );
  },

  compare(path: string, oldVersion: string, newVersion: string): Promise<ApiResult<PmDocumentDiffResponse>> {
    const query = new URLSearchParams();
    query.set('old_version', oldVersion);
    query.set('new_version', newVersion);
    return apiGet<PmDocumentDiffResponse>(
      `/v2/pm/documents/${encodeDocumentPath(path)}/compare?${query.toString()}`,
      'Failed to compare PM document versions',
    );
  },

  search(queryText: string, limit = 20): Promise<ApiResult<PmDocumentSearchResponse>> {
    const query = new URLSearchParams();
    query.set('q', queryText);
    query.set('limit', String(limit));
    return apiGet<PmDocumentSearchResponse>(
      `/v2/pm/search/documents?${query.toString()}`,
      'Failed to search PM documents',
    );
  },
};
