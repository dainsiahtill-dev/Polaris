import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import type {
  BackendSettings,
  BackendStatus,
  FilePayload,
  LanceDbStatus,
  ResidentDecisionPayload,
  ResidentExperimentPayload,
  ResidentGoalPayload,
  ResidentGoalRunPayload,
  ResidentGoalStagePayload,
  ResidentIdentityPayload,
  ResidentImprovementPayload,
  ResidentSkillPayload,
  ResidentStatusDetailsPayload,
  SnapshotPayload,
} from '@/app/types/appContracts';
import type { LLMStatus } from '@/app/components/llm/types';

export interface ApiResult<T> {
  ok: boolean;
  data?: T;
  error?: string;
}

function normalizeArtifactPath(path: string): string {
  const normalized = String(path || '').trim().replace(/\\/g, '/');
  if (!normalized) return normalized;
  if (normalized === '.polaris/runtime') return 'runtime';
  if (normalized.startsWith('.polaris/runtime/')) {
    return `runtime/${normalized.slice('.polaris/runtime/'.length)}`;
  }
  return normalized;
}

async function handleResponse<T>(res: Response, fallbackError: string): Promise<ApiResult<T>> {
  if (!res.ok) {
    let detail = fallbackError;
    try {
      const payload = await res.json() as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch (err) {
      devLogger.warn('[api] Error parsing error response:', err);
    }
    return { ok: false, error: detail };
  }
  try {
    const data = await res.json() as T;
    return { ok: true, data };
  } catch (err) {
    devLogger.warn('[api] Failed to parse response:', err);
    return { ok: false, error: 'Failed to parse response' };
  }
}

function normalizeDirectorStatusPayload(payload: unknown): BackendStatus {
  const raw = payload as Record<string, unknown> | null;
  if (!raw || typeof raw !== 'object') {
    return { running: false, pid: null, started_at: null, source: 'none', status: null };
  }

  if (typeof raw.running === 'boolean') {
    return {
      running: raw.running,
      pid: typeof raw.pid === 'number' ? raw.pid : null,
      started_at: typeof raw.started_at === 'number' ? raw.started_at : null,
      mode: typeof raw.mode === 'string' ? raw.mode : undefined,
      log_path: typeof raw.log_path === 'string' ? raw.log_path : undefined,
      source: typeof raw.source === 'string' ? raw.source : undefined,
      status: typeof raw.status === 'object' && raw.status !== null ? (raw.status as Record<string, unknown>) : null,
    };
  }

  const state = String(raw.state || '').trim().toUpperCase();
  return {
    running: state === 'RUNNING',
    pid: null,
    started_at: null,
    mode: 'v2_service',
    source: 'v2_service',
    status: raw,
  };
}

export const settingsService = {
  async get(): Promise<ApiResult<BackendSettings>> {
    const res = await apiFetch('/settings');
    return handleResponse(res, 'Failed to load settings');
  },

  async update(updates: Partial<BackendSettings>): Promise<ApiResult<BackendSettings>> {
    const res = await apiFetch('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    return handleResponse(res, 'Failed to update settings');
  },
};

export const statusService = {
  async getPm(): Promise<ApiResult<BackendStatus>> {
    const res = await apiFetch('/v2/pm/status');
    return handleResponse(res, 'Failed to load PM status');
  },

  async getDirector(): Promise<ApiResult<BackendStatus>> {
    const res = await apiFetch('/v2/director/status');
    if (!res.ok) {
      return handleResponse(res, 'Failed to load Director status');
    }
    try {
      const payload = await res.json();
      return { ok: true, data: normalizeDirectorStatusPayload(payload) };
    } catch {
      return { ok: false, error: 'Failed to parse Director status' };
    }
  },

  async getAll(): Promise<{ pm: ApiResult<BackendStatus>; director: ApiResult<BackendStatus> }> {
    const [pmRes, directorRes] = await Promise.all([
      apiFetch('/v2/pm/status'),
      apiFetch('/v2/director/status'),
    ]);
    let director: ApiResult<BackendStatus>;
    if (!directorRes.ok) {
      director = await handleResponse(directorRes, 'Failed to load Director status');
    } else {
      try {
        const payload = await directorRes.json();
        director = { ok: true, data: normalizeDirectorStatusPayload(payload) };
      } catch {
        director = { ok: false, error: 'Failed to parse Director status' };
      }
    }
    return {
      pm: await handleResponse(pmRes, 'Failed to load PM status'),
      director,
    };
  },
};

export const processService = {
  async startPm(resume = false): Promise<ApiResult<void>> {
    const url = resume ? '/v2/pm/start?resume=true' : '/v2/pm/start';
    const res = await apiFetch(url, { method: 'POST' });
    return handleResponse(res, 'Failed to start PM');
  },

  async stopPm(): Promise<ApiResult<void>> {
    const res = await apiFetch('/v2/pm/stop', { method: 'POST' });
    return handleResponse(res, 'Failed to stop PM');
  },

  async runPmOnce(): Promise<ApiResult<void>> {
    const res = await apiFetch('/v2/pm/run_once', { method: 'POST' });
    return handleResponse(res, 'PM run once failed');
  },

  async startDirector(): Promise<ApiResult<void>> {
    const res = await apiFetch('/v2/director/start', { method: 'POST' });
    return handleResponse(res, 'Failed to start Chief Engineer');
  },

  async stopDirector(): Promise<ApiResult<void>> {
    const res = await apiFetch('/v2/director/stop', { method: 'POST' });
    return handleResponse(res, 'Failed to stop Chief Engineer');
  },
};

export const snapshotService = {
  async get(): Promise<ApiResult<SnapshotPayload>> {
    const res = await apiFetch('/state/snapshot');
    return handleResponse(res, 'Failed to load snapshot');
  },
};

export const residentService = {
  async getStatus(workspace = '', details = false): Promise<ApiResult<ResidentStatusDetailsPayload>> {
    const query = new URLSearchParams();
    if (workspace) query.set('workspace', workspace);
    if (details) query.set('details', 'true');
    const suffix = query.toString();
    const res = await apiFetch(`/v2/resident/status${suffix ? `?${suffix}` : ''}`);
    return handleResponse(res, 'Failed to load Resident status');
  },

  async start(workspace: string, mode: string): Promise<ApiResult<ResidentStatusDetailsPayload>> {
    const res = await apiFetch('/v2/resident/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace, mode }),
    });
    return handleResponse(res, 'Failed to start Resident');
  },

  async stop(workspace: string): Promise<ApiResult<ResidentStatusDetailsPayload>> {
    const res = await apiFetch('/v2/resident/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace }),
    });
    return handleResponse(res, 'Failed to stop Resident');
  },

  async tick(workspace: string, force = true): Promise<ApiResult<ResidentStatusDetailsPayload>> {
    const query = force ? '?force=true' : '';
    const res = await apiFetch(`/v2/resident/tick${query}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace }),
    });
    return handleResponse(res, 'Failed to tick Resident');
  },

  async updateIdentity(
    workspace: string,
    payload: Partial<ResidentIdentityPayload>,
  ): Promise<ApiResult<ResidentIdentityPayload>> {
    const res = await apiFetch('/v2/resident/identity', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace, ...payload }),
    });
    return handleResponse(res, 'Failed to update Resident identity');
  },

  async listGoals(workspace: string, statusFilter = ''): Promise<ApiResult<ResidentGoalPayload[]>> {
    const query = new URLSearchParams();
    if (workspace) query.set('workspace', workspace);
    if (statusFilter) query.set('status_filter', statusFilter);
    const suffix = query.toString();
    const res = await apiFetch(`/v2/resident/goals${suffix ? `?${suffix}` : ''}`);
    const parsed = await handleResponse<{ items?: ResidentGoalPayload[] }>(res, 'Failed to list Resident goals');
    return parsed.ok
      ? { ok: true, data: Array.isArray(parsed.data?.items) ? parsed.data?.items : [] }
      : { ok: false, error: parsed.error };
  },

  async createGoal(workspace: string, payload: Partial<ResidentGoalPayload>): Promise<ApiResult<ResidentGoalPayload>> {
    const res = await apiFetch('/v2/resident/goals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace, ...payload }),
    });
    return handleResponse(res, 'Failed to create Resident goal');
  },

  async approveGoal(goalId: string, workspace: string, note = ''): Promise<ApiResult<ResidentGoalPayload>> {
    const res = await apiFetch(`/v2/resident/goals/${encodeURIComponent(goalId)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace, note }),
    });
    return handleResponse(res, 'Failed to approve Resident goal');
  },

  async rejectGoal(goalId: string, workspace: string, note = ''): Promise<ApiResult<ResidentGoalPayload>> {
    const res = await apiFetch(`/v2/resident/goals/${encodeURIComponent(goalId)}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace, note }),
    });
    return handleResponse(res, 'Failed to reject Resident goal');
  },

  async materializeGoal(goalId: string, workspace: string): Promise<ApiResult<Record<string, unknown>>> {
    const res = await apiFetch(`/v2/resident/goals/${encodeURIComponent(goalId)}/materialize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace }),
    });
    return handleResponse(res, 'Failed to materialize Resident goal');
  },

  async stageGoal(
    goalId: string,
    workspace: string,
    promoteToPmRuntime = false,
  ): Promise<ApiResult<ResidentGoalStagePayload>> {
    const res = await apiFetch(`/v2/resident/goals/${encodeURIComponent(goalId)}/stage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace, promote_to_pm_runtime: promoteToPmRuntime }),
    });
    return handleResponse(res, 'Failed to stage Resident goal');
  },

  async runGoal(
    goalId: string,
    workspace: string,
    options: { runType?: string; runDirector?: boolean; directorIterations?: number } = {},
  ): Promise<ApiResult<ResidentGoalRunPayload>> {
    const res = await apiFetch(`/v2/resident/goals/${encodeURIComponent(goalId)}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        workspace,
        run_type: options.runType || 'pm',
        run_director: options.runDirector ?? false,
        director_iterations: options.directorIterations ?? 1,
      }),
    });
    return handleResponse(res, 'Failed to run Resident goal through PM');
  },

  async listDecisions(
    workspace: string,
    options: { limit?: number; actor?: string; verdict?: string } = {},
  ): Promise<ApiResult<ResidentDecisionPayload[]>> {
    const query = new URLSearchParams();
    if (workspace) query.set('workspace', workspace);
    if (options.limit) query.set('limit', String(options.limit));
    if (options.actor) query.set('actor', options.actor);
    if (options.verdict) query.set('verdict', options.verdict);
    const suffix = query.toString();
    const res = await apiFetch(`/v2/resident/decisions${suffix ? `?${suffix}` : ''}`);
    const parsed = await handleResponse<{ items?: ResidentDecisionPayload[] }>(res, 'Failed to list Resident decisions');
    return parsed.ok
      ? { ok: true, data: Array.isArray(parsed.data?.items) ? parsed.data?.items : [] }
      : { ok: false, error: parsed.error };
  },

  async listSkills(workspace: string): Promise<ApiResult<ResidentSkillPayload[]>> {
    const suffix = workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
    const res = await apiFetch(`/v2/resident/skills${suffix}`);
    const parsed = await handleResponse<{ items?: ResidentSkillPayload[] }>(res, 'Failed to list Resident skills');
    return parsed.ok
      ? { ok: true, data: Array.isArray(parsed.data?.items) ? parsed.data?.items : [] }
      : { ok: false, error: parsed.error };
  },

  async extractSkills(workspace: string): Promise<ApiResult<ResidentSkillPayload[]>> {
    const res = await apiFetch('/v2/resident/skills/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace }),
    });
    const parsed = await handleResponse<{ items?: ResidentSkillPayload[] }>(res, 'Failed to extract Resident skills');
    return parsed.ok
      ? { ok: true, data: Array.isArray(parsed.data?.items) ? parsed.data?.items : [] }
      : { ok: false, error: parsed.error };
  },

  async listExperiments(workspace: string): Promise<ApiResult<ResidentExperimentPayload[]>> {
    const suffix = workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
    const res = await apiFetch(`/v2/resident/experiments${suffix}`);
    const parsed = await handleResponse<{ items?: ResidentExperimentPayload[] }>(res, 'Failed to list Resident experiments');
    return parsed.ok
      ? { ok: true, data: Array.isArray(parsed.data?.items) ? parsed.data?.items : [] }
      : { ok: false, error: parsed.error };
  },

  async runExperiments(workspace: string): Promise<ApiResult<ResidentExperimentPayload[]>> {
    const res = await apiFetch('/v2/resident/experiments/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace }),
    });
    const parsed = await handleResponse<{ items?: ResidentExperimentPayload[] }>(res, 'Failed to run Resident experiments');
    return parsed.ok
      ? { ok: true, data: Array.isArray(parsed.data?.items) ? parsed.data?.items : [] }
      : { ok: false, error: parsed.error };
  },

  async listImprovements(workspace: string): Promise<ApiResult<ResidentImprovementPayload[]>> {
    const suffix = workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
    const res = await apiFetch(`/v2/resident/improvements${suffix}`);
    const parsed = await handleResponse<{ items?: ResidentImprovementPayload[] }>(res, 'Failed to list Resident improvements');
    return parsed.ok
      ? { ok: true, data: Array.isArray(parsed.data?.items) ? parsed.data?.items : [] }
      : { ok: false, error: parsed.error };
  },

  async runImprovements(workspace: string): Promise<ApiResult<ResidentImprovementPayload[]>> {
    const res = await apiFetch('/v2/resident/improvements/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace }),
    });
    const parsed = await handleResponse<{ items?: ResidentImprovementPayload[] }>(res, 'Failed to run Resident improvements');
    return parsed.ok
      ? { ok: true, data: Array.isArray(parsed.data?.items) ? parsed.data?.items : [] }
      : { ok: false, error: parsed.error };
  },

  // Phase 1.2: Goal Execution Projection
  async getGoalExecution(goalId: string, workspace: string): Promise<ApiResult<import('@/app/types/appContracts').GoalExecutionView>> {
    const suffix = workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
    const res = await apiFetch(`/v2/resident/goals/${encodeURIComponent(goalId)}/execution${suffix}`);
    return handleResponse(res, 'Failed to load goal execution view');
  },

  async listGoalExecutions(workspace: string): Promise<ApiResult<import('@/app/types/appContracts').GoalExecutionView[]>> {
    const suffix = workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
    const res = await apiFetch(`/v2/resident/goals/execution/bulk${suffix}`);
    const parsed = await handleResponse<{ items?: import('@/app/types/appContracts').GoalExecutionView[] }>(res, 'Failed to list goal executions');
    return parsed.ok
      ? { ok: true, data: Array.isArray(parsed.data?.items) ? parsed.data?.items : [] }
      : { ok: false, error: parsed.error };
  },
};

export const lancedbService = {
  async getStatus(): Promise<ApiResult<LanceDbStatus>> {
    const res = await apiFetch('/lancedb/status');
    return handleResponse(res, 'Failed to load LanceDB status');
  },
};

export const llmService = {
  async getStatus(): Promise<ApiResult<LLMStatus>> {
    const res = await apiFetch('/v2/llm/status');
    return handleResponse(res, 'Failed to load LLM status');
  },
};

export const fileService = {
  async read(path: string, tailLines?: number): Promise<ApiResult<FilePayload>> {
    const normalizedPath = normalizeArtifactPath(path);
    let url = `/files/read?path=${encodeURIComponent(normalizedPath)}`;
    if (tailLines) {
      url += `&tail_lines=${tailLines}`;
    }
    const res = await apiFetch(url);
    return handleResponse(res, 'Failed to read file');
  },

  async readLogTail(path: string, lines = 20): Promise<string> {
    const result = await this.read(path, 200);
    if (!result.ok || !result.data?.content) return '';
    const allLines = result.data.content.split('\n');
    return allLines.slice(-lines).join('\n');
  },
};

export const memoService = {
  async list(limit = 200) {
    const res = await apiFetch(`/memos/list?limit=${limit}`);
    return handleResponse<{ items: Array<{ path: string; name: string; mtime?: string }>; count: number }>(
      res,
      'Failed to list memos'
    );
  },
};

export const runtimeService = {
  async clearDialogue(): Promise<ApiResult<void>> {
    const res = await apiFetch('/runtime/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'dialogue' }),
    });
    return handleResponse(res, '清空对话日志失败');
  },

  async resetTasks(): Promise<ApiResult<void>> {
    const res = await apiFetch('/runtime/reset-tasks', { method: 'POST' });
    return handleResponse(res, '重置任务失败');
  },
};

export const ollamaService = {
  async stopModels(): Promise<ApiResult<{ stopped?: string[]; failed?: Array<{ model: string }> }>> {
    const res = await apiFetch('/ollama/stop', { method: 'POST' });
    return handleResponse(res, 'Failed to stop Ollama models');
  },
};

export const healthService = {
  async check(): Promise<ApiResult<{ timestamp?: string }>> {
    const res = await apiFetch('/health');
    return handleResponse(res, 'Health check failed');
  },
};

export const agentsService = {
  async applyDraft(draftPath: string): Promise<ApiResult<void>> {
    const res = await apiFetch('/agents/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft_path: draftPath }),
    });
    return handleResponse(res, 'Failed to apply AGENTS draft');
  },

  async saveFeedback(text: string): Promise<ApiResult<{ mtime?: string; cleared?: boolean }>> {
    const res = await apiFetch('/agents/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    return handleResponse(res, 'Failed to save feedback');
  },
};

// V2 Services API (Strategy B - Director v2)
export interface TaskResponse {
  id: string;
  command: string;
  state: string;
  timeout: number;
  result?: {
    success: boolean;
    exit_code: number;
    stdout: string;
    stderr: string;
    duration_ms: number;
  };
}

export interface TodoItemResponse {
  id: string;
  content: string;
  status: string;
  priority: string;
  tags: string[];
}

export interface TokenStatusResponse {
  used_tokens: number;
  budget_limit?: number;
  remaining_tokens?: number;
  percent_used: number;
  is_exceeded: boolean;
}

// V2 P0 Missing Routes Types (re-export from api.types for local consistency)
export type {
  UnifiedRole,
  RoleChatRequest,
  RoleChatResponse,
  RoleChatStatusResponse,
  SessionMessageRequest,
  SessionMessageResponse,
  SessionMemoryResponse,
  StreamChatRequest,
  StreamChatResponse,
  ConversationV2,
  ConversationMessageV2,
  ConversationListResponseV2,
  CreateConversationRequestV2,
  AddConversationMessageRequestV2,
} from './api.types';

export const v2Services = {
  // Background Tasks
  async createTask(command: string, timeout = 300, tier = 'background'): Promise<ApiResult<TaskResponse>> {
    const res = await apiFetch('/v2/services/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command, timeout, tier }),
    });
    return handleResponse(res, 'Failed to create background task');
  },

  async getTask(taskId: string): Promise<ApiResult<TaskResponse>> {
    const res = await apiFetch(`/v2/services/tasks/${taskId}`);
    return handleResponse(res, 'Failed to get task');
  },

  async listTasks(state?: string): Promise<ApiResult<TaskResponse[]>> {
    const url = state ? `/v2/services/tasks?state=${state}` : '/v2/services/tasks';
    const res = await apiFetch(url);
    return handleResponse(res, 'Failed to list tasks');
  },

  // Todos
  async createTodo(content: string, priority = 'medium', tags: string[] = []): Promise<ApiResult<TodoItemResponse>> {
    const res = await apiFetch('/v2/services/todos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, priority, tags }),
    });
    return handleResponse(res, 'Failed to create todo');
  },

  async listTodos(status?: string): Promise<ApiResult<TodoItemResponse[]>> {
    const url = status ? `/v2/services/todos?status=${status}` : '/v2/services/todos';
    const res = await apiFetch(url);
    return handleResponse(res, 'Failed to list todos');
  },

  async getTodoSummary(): Promise<ApiResult<{ summary: Record<string, unknown>; next_action: TodoItemResponse | null }>> {
    const res = await apiFetch('/v2/services/todos/summary');
    return handleResponse(res, 'Failed to get todo summary');
  },

  async markTodoDone(itemId: string): Promise<ApiResult<{ ok: boolean }>> {
    const res = await apiFetch(`/v2/services/todos/${itemId}/done`, { method: 'POST' });
    return handleResponse(res, 'Failed to mark todo done');
  },

  // Token Budget
  async getTokenStatus(): Promise<ApiResult<TokenStatusResponse>> {
    const res = await apiFetch('/v2/services/tokens/status');
    return handleResponse(res, 'Failed to get token status');
  },

  async recordTokenUsage(tokens: number): Promise<ApiResult<{ ok: boolean; recorded: number; total_used: number; remaining?: number }>> {
    const res = await apiFetch('/v2/services/tokens/record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tokens }),
    });
    return handleResponse(res, 'Failed to record token usage');
  },

  // Security
  async checkSecurity(command: string): Promise<ApiResult<{ is_safe: boolean; reason?: string; suggested_alternative?: string }>> {
    const res = await apiFetch('/v2/services/security/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    });
    return handleResponse(res, 'Failed to check security');
  },

  // Transcript
  async getTranscript(limit = 100, messageType?: string): Promise<ApiResult<Array<Record<string, unknown>>>> {
    let url = `/v2/services/transcript?limit=${limit}`;
    if (messageType) url += `&message_type=${messageType}`;
    const res = await apiFetch(url);
    return handleResponse(res, 'Failed to get transcript');
  },

  async getTranscriptSession(): Promise<ApiResult<{ active: boolean; session_id?: string; message_count?: number }>> {
    const res = await apiFetch('/v2/services/transcript/session');
    return handleResponse(res, 'Failed to get transcript session');
  },
};

// ============================================================================
// V2 P0 Missing Routes — Unified Role Chat
// ============================================================================

export const roleChatService = {
  /** POST /v2/role/{role}/chat — Non-streaming unified role chat */
  async chat(role: string, request: import('./api.types').RoleChatRequest): Promise<ApiResult<import('./api.types').RoleChatResponse>> {
    const res = await apiFetch(`/v2/role/${encodeURIComponent(role)}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Role chat failed');
  },

  /** POST /v2/role/{role}/chat/stream — Streaming unified role chat (returns raw Response for SSE handling) */
  async chatStream(role: string, request: import('./api.types').RoleChatRequest, signal?: AbortSignal): Promise<Response> {
    return apiFetch(`/v2/role/${encodeURIComponent(role)}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    });
  },

  /** GET /v2/role/{role}/chat/status — Role chat readiness status */
  async getStatus(role: string): Promise<ApiResult<import('./api.types').RoleChatStatusResponse>> {
    const res = await apiFetch(`/v2/role/${encodeURIComponent(role)}/chat/status`);
    return handleResponse(res, 'Failed to load role chat status');
  },
};

// ============================================================================
// V2 P0 Missing Routes — Role Session Messages
// ============================================================================

export const roleSessionService = {
  /** POST /v2/roles/sessions/{id}/messages — Send a message to a role session */
  async sendMessage(sessionId: string, request: import('./api.types').SessionMessageRequest): Promise<ApiResult<import('./api.types').SessionMessageResponse>> {
    const res = await apiFetch(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Failed to send session message');
  },

  /** POST /v2/roles/sessions/{id}/messages/stream — Stream a message to a role session (returns raw Response for SSE) */
  async sendMessageStream(sessionId: string, request: import('./api.types').SessionMessageRequest, signal?: AbortSignal): Promise<Response> {
    return apiFetch(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/messages/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    });
  },

  /** GET /v2/roles/sessions/{id}/memory — Get session memory (maps to memory search endpoint) */
  async getMemory(sessionId: string, query?: string, kind?: string, entity?: string, limit = 6): Promise<ApiResult<import('./api.types').SessionMemoryResponse>> {
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    if (kind) params.set('kind', kind);
    if (entity) params.set('entity', entity);
    params.set('limit', String(limit));
    const res = await apiFetch(`/v2/roles/sessions/${encodeURIComponent(sessionId)}/memory/search?${params}`);
    return handleResponse(res, 'Failed to load session memory');
  },
};

// ============================================================================
// V2 P0 Missing Routes — Neural Weave Stream
// ============================================================================

export const streamService = {
  /** POST /v2/stream/chat — Neural Weave SSE chat (returns raw Response for SSE handling) */
  async chat(request: import('./api.types').StreamChatRequest, signal?: AbortSignal): Promise<Response> {
    return apiFetch('/v2/stream/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    });
  },

  /** POST /v2/stream/chat/backpressure — Explicit backpressure SSE chat (returns raw Response) */
  async chatWithBackpressure(request: import('./api.types').StreamChatRequest, signal?: AbortSignal): Promise<Response> {
    return apiFetch('/v2/stream/chat/backpressure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    });
  },
};

// ============================================================================
// V2 P0 Missing Routes — Factory Run Stream
// ============================================================================

export const factoryStreamService = {
  /** GET /v2/factory/runs/{id}/stream — Factory run SSE stream (returns raw Response for SSE handling) */
  async connectRunStream(runId: string, signal?: AbortSignal): Promise<Response> {
    return apiFetch(`/v2/factory/runs/${encodeURIComponent(runId)}/stream`, { signal });
  },
};

// ============================================================================
// V2 P0 Missing Routes — Conversations (unified stubs in api.ts)
// ============================================================================

// ============================================================================
// V2 P1 Management Routes — PM Tasks
// ============================================================================

export const pmTaskService = {
  /** GET /v2/pm/tasks — List PM tasks */
  async list(): Promise<ApiResult<import('./api.types').PmTaskListResponse>> {
    const res = await apiFetch('/v2/pm/tasks');
    return handleResponse(res, 'Failed to list PM tasks');
  },

  /** GET /v2/pm/tasks/{id} — Get PM task detail */
  async get(id: string): Promise<ApiResult<import('./api.types').PmTaskDetailResponse>> {
    const res = await apiFetch(`/v2/pm/tasks/${encodeURIComponent(id)}`);
    return handleResponse(res, 'Failed to get PM task detail');
  },

  /** POST /v2/pm/tasks — Create PM task */
  async create(request: import('./api.types').PmCreateTaskRequest): Promise<ApiResult<import('./api.types').PmTaskDetailResponse>> {
    const res = await apiFetch('/v2/pm/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Failed to create PM task');
  },
};

// ============================================================================
// V2 P1 Management Routes — PM Requirements
// ============================================================================

export const pmRequirementService = {
  /** GET /v2/pm/requirements — List PM requirements */
  async list(): Promise<ApiResult<import('./api.types').PmRequirementListResponse>> {
    const res = await apiFetch('/v2/pm/requirements');
    return handleResponse(res, 'Failed to list PM requirements');
  },

  /** GET /v2/pm/requirements/{id} — Get PM requirement detail */
  async get(id: string): Promise<ApiResult<import('./api.types').PmRequirementDetailResponse>> {
    const res = await apiFetch(`/v2/pm/requirements/${encodeURIComponent(id)}`);
    return handleResponse(res, 'Failed to get PM requirement detail');
  },
};

// ============================================================================
// V2 P1 Management Routes — Docs Init
// ============================================================================

export const docsInitService = {
  /** POST /v2/docs/init/dialogue — Docs init dialogue */
  async dialogue(request: import('./api.types').DocsInitDialogueRequest): Promise<ApiResult<import('./api.types').DocsInitDialogueResponse>> {
    const res = await apiFetch('/v2/docs/init/dialogue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Docs init dialogue failed');
  },

  /** POST /v2/docs/init/suggest — Docs init suggest */
  async suggest(request: import('./api.types').DocsInitSuggestRequest): Promise<ApiResult<import('./api.types').DocsInitSuggestResponse>> {
    const res = await apiFetch('/v2/docs/init/suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Docs init suggest failed');
  },

  /** POST /v2/docs/init/preview — Docs init preview */
  async preview(request: import('./api.types').DocsInitPreviewRequest): Promise<ApiResult<import('./api.types').DocsInitPreviewResponse>> {
    const res = await apiFetch('/v2/docs/init/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Docs init preview failed');
  },

  /** POST /v2/docs/init/apply — Docs init apply */
  async apply(request: import('./api.types').DocsInitApplyRequest): Promise<ApiResult<import('./api.types').DocsInitApplyResponse>> {
    const res = await apiFetch('/v2/docs/init/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Docs init apply failed');
  },
};

// ============================================================================
// V2 P1 Management Routes — LLM Config
// ============================================================================

export const llmConfigService = {
  /** GET /v2/llm/config — Get LLM config */
  async get(): Promise<ApiResult<import('./api.types').LLMConfigResponse>> {
    const res = await apiFetch('/v2/llm/config');
    return handleResponse(res, 'Failed to load LLM config');
  },

  /** POST /v2/llm/config/migrate — Migrate LLM config */
  async migrate(request: import('./api.types').LLMConfigMigrateRequest): Promise<ApiResult<import('./api.types').LLMConfigMigrateResponse>> {
    const res = await apiFetch('/v2/llm/config/migrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Failed to migrate LLM config');
  },

  /** GET /v2/llm/status — Get LLM status */
  async getStatus(): Promise<ApiResult<import('./api.types').LLMStatusResponse>> {
    const res = await apiFetch('/v2/llm/status');
    return handleResponse(res, 'Failed to load LLM status');
  },

  /** GET /v2/llm/providers — List LLM providers */
  async listProviders(): Promise<ApiResult<import('./api.types').LLMProviderListResponse>> {
    const res = await apiFetch('/v2/llm/providers');
    return handleResponse(res, 'Failed to list LLM providers');
  },
};

// ============================================================================
// V2 P1 Management Routes — Settings
// ============================================================================

export const settingsV2Service = {
  /** GET /v2/settings — Get settings */
  async get(): Promise<ApiResult<import('./api.types').SettingsV2Response>> {
    const res = await apiFetch('/v2/settings');
    return handleResponse(res, 'Failed to load settings');
  },

  /** POST /v2/settings — Update settings */
  async update(request: import('./api.types').SettingsV2UpdateRequest): Promise<ApiResult<import('./api.types').SettingsV2Response>> {
    const res = await apiFetch('/v2/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Failed to update settings');
  },
};

// ============================================================================
// V2 P1 Management Routes — Agents
// ============================================================================

export const agentsV2Service = {
  /** POST /v2/agents/apply — Apply agents */
  async apply(request: import('./api.types').AgentsApplyRequest): Promise<ApiResult<import('./api.types').AgentsApplyResponse>> {
    const res = await apiFetch('/v2/agents/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Failed to apply agents');
  },

  /** POST /v2/agents/feedback — Agents feedback */
  async feedback(request: import('./api.types').AgentsFeedbackRequest): Promise<ApiResult<import('./api.types').AgentsFeedbackResponse>> {
    const res = await apiFetch('/v2/agents/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Failed to submit agents feedback');
  },
};

// ============================================================================
// V2 P1 Management Routes — Role Cache
// ============================================================================

export const roleCacheService = {
  /** GET /v2/role/cache-stats — LLM cache stats */
  async getStats(): Promise<ApiResult<import('./api.types').RoleCacheStatsResponse>> {
    const res = await apiFetch('/v2/role/cache-stats');
    return handleResponse(res, 'Failed to load role cache stats');
  },

  /** POST /v2/role/cache-clear — Clear LLM cache */
  async clear(): Promise<ApiResult<import('./api.types').RoleCacheClearResponse>> {
    const res = await apiFetch('/v2/role/cache-clear', { method: 'POST' });
    return handleResponse(res, 'Failed to clear role cache');
  },
};

// ============================================================================
// V2 P1 Management Routes — Role Chat Roles
// ============================================================================

export const roleChatRolesService = {
  /** GET /v2/role/chat/roles — List supported roles */
  async list(): Promise<ApiResult<import('./api.types').RoleChatRolesResponse>> {
    const res = await apiFetch('/v2/role/chat/roles');
    return handleResponse(res, 'Failed to list supported roles');
  },
};

export const conversationV2Service = {
  /** GET /v2/conversations — List conversations */
  async list(params?: { role?: string; workspace?: string; limit?: number; offset?: number }): Promise<ApiResult<import('./api.types').ConversationListResponseV2>> {
    const searchParams = new URLSearchParams();
    if (params?.role) searchParams.set('role', params.role);
    if (params?.workspace) searchParams.set('workspace', params.workspace);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const query = searchParams.toString();
    const res = await apiFetch(`/v2/conversations${query ? `?${query}` : ''}`);
    return handleResponse(res, 'Failed to list conversations');
  },

  /** POST /v2/conversations — Create conversation */
  async create(request: import('./api.types').CreateConversationRequestV2): Promise<ApiResult<import('./api.types').ConversationV2>> {
    const res = await apiFetch('/v2/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Failed to create conversation');
  },

  /** GET /v2/conversations/{id}/messages — Get conversation messages */
  async getMessages(conversationId: string, params?: { limit?: number; offset?: number }): Promise<ApiResult<import('./api.types').ConversationMessageV2[]>> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const query = searchParams.toString();
    const res = await apiFetch(`/v2/conversations/${encodeURIComponent(conversationId)}/messages${query ? `?${query}` : ''}`);
    return handleResponse(res, 'Failed to get conversation messages');
  },

  /** POST /v2/conversations/{id}/messages — Add message to conversation */
  async addMessage(conversationId: string, request: import('./api.types').AddConversationMessageRequestV2): Promise<ApiResult<import('./api.types').ConversationMessageV2>> {
    const res = await apiFetch(`/v2/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Failed to add conversation message');
  },
};

// ============================================================================
// V2 P2 Diagnostic Routes
// ============================================================================

export const healthV2Service = {
  /** GET /v2/health — Health check */
  async check(): Promise<ApiResult<import('./api.types').HealthV2Response>> {
    const res = await apiFetch('/v2/health');
    return handleResponse(res, 'Health check failed');
  },
};

export const readyV2Service = {
  /** GET /v2/ready — Readiness probe */
  async check(): Promise<ApiResult<import('./api.types').ReadyV2Response>> {
    const res = await apiFetch('/v2/ready');
    return handleResponse(res, 'Readiness check failed');
  },
};

export const liveV2Service = {
  /** GET /v2/live — Liveness probe */
  async check(): Promise<ApiResult<import('./api.types').LiveV2Response>> {
    const res = await apiFetch('/v2/live');
    return handleResponse(res, 'Liveness check failed');
  },
};

export const stateSnapshotV2Service = {
  /** GET /v2/state/snapshot — State snapshot */
  async get(): Promise<ApiResult<import('./api.types').StateSnapshotV2Response>> {
    const res = await apiFetch('/v2/state/snapshot');
    return handleResponse(res, 'Failed to load state snapshot');
  },
};

export const shutdownV2Service = {
  /** POST /v2/app/shutdown — Shutdown */
  async shutdown(): Promise<ApiResult<import('./api.types').ShutdownV2Response>> {
    const res = await apiFetch('/v2/app/shutdown', { method: 'POST' });
    return handleResponse(res, 'Shutdown failed');
  },
};

export const logsV2Service = {
  /** GET /v2/logs/query — Query logs */
  async query(params?: { level?: string; channel?: string; limit?: number; offset?: number; start?: string; end?: string }): Promise<ApiResult<import('./api.types').LogsQueryV2Response>> {
    const searchParams = new URLSearchParams();
    if (params?.level) searchParams.set('level', params.level);
    if (params?.channel) searchParams.set('channel', params.channel);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    if (params?.start) searchParams.set('start', params.start);
    if (params?.end) searchParams.set('end', params.end);
    const query = searchParams.toString();
    const res = await apiFetch(`/v2/logs/query${query ? `?${query}` : ''}`);
    return handleResponse(res, 'Failed to query logs');
  },

  /** POST /v2/logs/user-action — Log user action */
  async logUserAction(request: import('./api.types').LogUserActionV2Request): Promise<ApiResult<import('./api.types').LogUserActionV2Response>> {
    const res = await apiFetch('/v2/logs/user-action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Failed to log user action');
  },

  /** GET /v2/logs/channels — Log channels */
  async channels(): Promise<ApiResult<import('./api.types').LogChannelsV2Response>> {
    const res = await apiFetch('/v2/logs/channels');
    return handleResponse(res, 'Failed to load log channels');
  },
};

export const lancedbV2Service = {
  /** GET /v2/lancedb/status — LanceDB status */
  async getStatus(): Promise<ApiResult<import('./api.types').LanceDbStatusV2Response>> {
    const res = await apiFetch('/v2/lancedb/status');
    return handleResponse(res, 'Failed to load LanceDB status');
  },
};

export const memosV2Service = {
  /** GET /v2/memos/list — List memos */
  async list(limit = 200): Promise<ApiResult<import('./api.types').MemoListV2Response>> {
    const res = await apiFetch(`/v2/memos/list?limit=${limit}`);
    return handleResponse(res, 'Failed to list memos');
  },
};

export const ollamaV2Service = {
  /** POST /v2/ollama/models — List Ollama models */
  async listModels(request?: import('./api.types').OllamaModelsV2Request): Promise<ApiResult<import('./api.types').OllamaModelsV2Response>> {
    const res = await apiFetch('/v2/ollama/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request ?? {}),
    });
    return handleResponse(res, 'Failed to list Ollama models');
  },

  /** POST /v2/ollama/stop — Stop Ollama */
  async stop(): Promise<ApiResult<import('./api.types').OllamaStopV2Response>> {
    const res = await apiFetch('/v2/ollama/stop', { method: 'POST' });
    return handleResponse(res, 'Failed to stop Ollama');
  },
};

export const memoryV2Service = {
  /** GET /v2/memory/state — Memory state */
  async getState(): Promise<ApiResult<import('./api.types').MemoryStateV2Response>> {
    const res = await apiFetch('/v2/memory/state');
    return handleResponse(res, 'Failed to load memory state');
  },

  /** DELETE /v2/memory/memories/{memory_id} — Delete memory */
  async deleteMemory(memoryId: string): Promise<ApiResult<import('./api.types').DeleteMemoryV2Response>> {
    const res = await apiFetch(`/v2/memory/memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' });
    return handleResponse(res, 'Failed to delete memory');
  },
};

export const roleLlmEventsV2Service = {
  /** GET /v2/role/{role}/llm-events — Role LLM events */
  async getByRole(role: string, params?: { limit?: number; offset?: number }): Promise<ApiResult<import('./api.types').RoleLlmEventsV2Response>> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const query = searchParams.toString();
    const res = await apiFetch(`/v2/role/${encodeURIComponent(role)}/llm-events${query ? `?${query}` : ''}`);
    return handleResponse(res, 'Failed to load role LLM events');
  },

  /** GET /v2/role/llm-events — All LLM events */
  async getAll(params?: { limit?: number; offset?: number }): Promise<ApiResult<import('./api.types').AllLlmEventsV2Response>> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const query = searchParams.toString();
    const res = await apiFetch(`/v2/role/llm-events${query ? `?${query}` : ''}`);
    return handleResponse(res, 'Failed to load LLM events');
  },
};

export const factoryRunV2Service = {
  /** GET /v2/factory/runs/{id}/events — Factory run events */
  async getEvents(runId: string, params?: { limit?: number; offset?: number }): Promise<ApiResult<import('./api.types').FactoryRunEventsV2Response>> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const query = searchParams.toString();
    const res = await apiFetch(`/v2/factory/runs/${encodeURIComponent(runId)}/events${query ? `?${query}` : ''}`);
    return handleResponse(res, 'Failed to load factory run events');
  },

  /** GET /v2/factory/runs/{id}/audit-bundle — Audit bundle */
  async getAuditBundle(runId: string): Promise<ApiResult<import('./api.types').FactoryRunAuditBundleV2Response>> {
    const res = await apiFetch(`/v2/factory/runs/${encodeURIComponent(runId)}/audit-bundle`);
    return handleResponse(res, 'Failed to load factory run audit bundle');
  },
};

export const runtimeMigrationV2Service = {
  /** GET /v2/runtime/migration/status — Migration status */
  async getStatus(): Promise<ApiResult<import('./api.types').RuntimeMigrationStatusV2Response>> {
    const res = await apiFetch('/v2/runtime/migration/status');
    return handleResponse(res, 'Failed to load runtime migration status');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — Court
// ============================================================================

export const courtService = {
  /** GET /v2/court/topology — Court topology */
  async getTopology(): Promise<ApiResult<import('./api.types').CourtTopologyResponse>> {
    const res = await apiFetch('/v2/court/topology');
    return handleResponse(res, 'Failed to load court topology');
  },

  /** GET /v2/court/state — Court state */
  async getState(): Promise<ApiResult<import('./api.types').CourtStateResponse>> {
    const res = await apiFetch('/v2/court/state');
    return handleResponse(res, 'Failed to load court state');
  },

  /** GET /v2/court/actors/{role_id} — Court actor */
  async getActor(roleId: string): Promise<ApiResult<import('./api.types').CourtActorResponse>> {
    const res = await apiFetch(`/v2/court/actors/${encodeURIComponent(roleId)}`);
    return handleResponse(res, 'Failed to load court actor');
  },

  /** GET /v2/court/scenes/{scene_id} — Court scene */
  async getScene(sceneId: string): Promise<ApiResult<import('./api.types').CourtSceneResponse>> {
    const res = await apiFetch(`/v2/court/scenes/${encodeURIComponent(sceneId)}`);
    return handleResponse(res, 'Failed to load court scene');
  },

  /** GET /v2/court/mapping — Court mapping */
  async getMapping(): Promise<ApiResult<import('./api.types').CourtMappingResponse>> {
    const res = await apiFetch('/v2/court/mapping');
    return handleResponse(res, 'Failed to load court mapping');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — Vision
// ============================================================================

export const visionService = {
  /** GET /v2/vision/status — Vision status */
  async getStatus(): Promise<ApiResult<import('./api.types').VisionStatusResponse>> {
    const res = await apiFetch('/v2/vision/status');
    return handleResponse(res, 'Failed to load vision status');
  },

  /** POST /v2/vision/analyze — Vision analyze */
  async analyze(request: import('./api.types').VisionAnalyzeRequest): Promise<ApiResult<import('./api.types').VisionAnalyzeResponse>> {
    const res = await apiFetch('/v2/vision/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Vision analyze failed');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — Scheduler
// ============================================================================

export const schedulerService = {
  /** GET /v2/scheduler/status — Scheduler status */
  async getStatus(): Promise<ApiResult<import('./api.types').SchedulerStatusResponse>> {
    const res = await apiFetch('/v2/scheduler/status');
    return handleResponse(res, 'Failed to load scheduler status');
  },

  /** POST /v2/scheduler/start — Scheduler start */
  async start(request?: import('./api.types').SchedulerStartRequest): Promise<ApiResult<import('./api.types').SchedulerStartResponse>> {
    const res = await apiFetch('/v2/scheduler/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request ?? {}),
    });
    return handleResponse(res, 'Failed to start scheduler');
  },

  /** POST /v2/scheduler/stop — Scheduler stop */
  async stop(): Promise<ApiResult<import('./api.types').SchedulerStopResponse>> {
    const res = await apiFetch('/v2/scheduler/stop', { method: 'POST' });
    return handleResponse(res, 'Failed to stop scheduler');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — Code Map
// ============================================================================

export const codeMapService = {
  /** GET /v2/code_map — Code map */
  async getMap(): Promise<ApiResult<import('./api.types').CodeMapResponse>> {
    const res = await apiFetch('/v2/code_map');
    return handleResponse(res, 'Failed to load code map');
  },

  /** POST /v2/code/index — Code index */
  async index(request?: import('./api.types').CodeIndexRequest): Promise<ApiResult<import('./api.types').CodeIndexResponse>> {
    const res = await apiFetch('/v2/code/index', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request ?? {}),
    });
    return handleResponse(res, 'Failed to index code');
  },

  /** POST /v2/code/search — Code search */
  async search(request: import('./api.types').CodeSearchRequest): Promise<ApiResult<import('./api.types').CodeSearchResponse>> {
    const res = await apiFetch('/v2/code/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Code search failed');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — MCP
// ============================================================================

export const mcpService = {
  /** GET /v2/mcp/status — MCP status */
  async getStatus(): Promise<ApiResult<import('./api.types').McpStatusResponse>> {
    const res = await apiFetch('/v2/mcp/status');
    return handleResponse(res, 'Failed to load MCP status');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — Director Capabilities
// ============================================================================

export const directorCapabilitiesService = {
  /** GET /v2/director/capabilities — Director capabilities */
  async getCapabilities(): Promise<ApiResult<import('./api.types').DirectorCapabilitiesResponse>> {
    const res = await apiFetch('/v2/director/capabilities');
    return handleResponse(res, 'Failed to load director capabilities');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — Interview
// ============================================================================

export const interviewService = {
  /** POST /v2/llm/interview/ask — Interview ask */
  async ask(request: import('./api.types').InterviewAskRequest): Promise<ApiResult<import('./api.types').InterviewAskResponse>> {
    const res = await apiFetch('/v2/llm/interview/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Interview ask failed');
  },

  /** POST /v2/llm/interview/save — Interview save */
  async save(request: import('./api.types').InterviewSaveRequest): Promise<ApiResult<import('./api.types').InterviewSaveResponse>> {
    const res = await apiFetch('/v2/llm/interview/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Interview save failed');
  },

  /** POST /v2/llm/interview/cancel — Interview cancel */
  async cancel(request?: import('./api.types').InterviewCancelRequest): Promise<ApiResult<import('./api.types').InterviewCancelResponse>> {
    const res = await apiFetch('/v2/llm/interview/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request ?? {}),
    });
    return handleResponse(res, 'Interview cancel failed');
  },

  /** POST /v2/llm/interview/stream — Interview stream (returns raw Response for SSE handling) */
  async stream(request: import('./api.types').InterviewStreamRequest, signal?: AbortSignal): Promise<Response> {
    return apiFetch('/v2/llm/interview/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    });
  },
};

// ============================================================================
// V2 P3 Advanced Routes — LLM Test
// ============================================================================

export const llmTestService = {
  /** GET /v2/llm/test — LLM test report */
  async getReport(): Promise<ApiResult<import('./api.types').LLMTestReportResponse>> {
    const res = await apiFetch('/v2/llm/test');
    return handleResponse(res, 'Failed to load LLM test report');
  },

  /** POST /v2/llm/test — Start LLM test */
  async start(request?: import('./api.types').LLMTestStartRequest): Promise<ApiResult<import('./api.types').LLMTestStartResponse>> {
    const res = await apiFetch('/v2/llm/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request ?? {}),
    });
    return handleResponse(res, 'Failed to start LLM test');
  },

  /** GET /v2/llm/test/{test_run_id} — Test run status */
  async getRunStatus(testRunId: string): Promise<ApiResult<import('./api.types').LLMTestRunStatusResponse>> {
    const res = await apiFetch(`/v2/llm/test/${encodeURIComponent(testRunId)}`);
    return handleResponse(res, 'Failed to load test run status');
  },

  /** GET /v2/llm/test/{test_run_id}/transcript — Test transcript */
  async getTranscript(testRunId: string): Promise<ApiResult<import('./api.types').LLMTestTranscriptResponse>> {
    const res = await apiFetch(`/v2/llm/test/${encodeURIComponent(testRunId)}/transcript`);
    return handleResponse(res, 'Failed to load test transcript');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — Permissions
// ============================================================================

export const permissionsV2Service = {
  /** POST /v2/permissions/v2/check — Check permission */
  async check(request: import('./api.types').PermissionCheckRequest): Promise<ApiResult<import('./api.types').PermissionCheckResponse>> {
    const res = await apiFetch('/v2/permissions/v2/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Permission check failed');
  },

  /** GET /v2/permissions/v2/effective — Effective permissions */
  async getEffective(): Promise<ApiResult<import('./api.types').EffectivePermissionsResponse>> {
    const res = await apiFetch('/v2/permissions/v2/effective');
    return handleResponse(res, 'Failed to load effective permissions');
  },

  /** GET /v2/permissions/v2/roles — Permission roles */
  async listRoles(): Promise<ApiResult<import('./api.types').PermissionRolesResponse>> {
    const res = await apiFetch('/v2/permissions/v2/roles');
    return handleResponse(res, 'Failed to list permission roles');
  },

  /** POST /v2/permissions/v2/assign — Assign permission */
  async assign(request: import('./api.types').PermissionAssignRequest): Promise<ApiResult<import('./api.types').PermissionAssignResponse>> {
    const res = await apiFetch('/v2/permissions/v2/assign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return handleResponse(res, 'Permission assign failed');
  },

  /** GET /v2/permissions/v2/policies — Permission policies */
  async listPolicies(): Promise<ApiResult<import('./api.types').PermissionPoliciesResponse>> {
    const res = await apiFetch('/v2/permissions/v2/policies');
    return handleResponse(res, 'Failed to list permission policies');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — Files V2
// ============================================================================

export const fileV2Service = {
  /** GET /v2/files/read — Read file */
  async read(path: string): Promise<ApiResult<import('./api.types').FileReadV2Response>> {
    const res = await apiFetch(`/v2/files/read?path=${encodeURIComponent(path)}`);
    return handleResponse(res, 'Failed to read file');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — Runtime V2
// ============================================================================

export const runtimeV2Service = {
  /** POST /v2/runtime/clear — Clear runtime */
  async clear(): Promise<ApiResult<import('./api.types').RuntimeClearResponse>> {
    const res = await apiFetch('/v2/runtime/clear', { method: 'POST' });
    return handleResponse(res, 'Failed to clear runtime');
  },

  /** POST /v2/runtime/reset/tasks — Reset tasks */
  async resetTasks(): Promise<ApiResult<import('./api.types').RuntimeResetTasksResponse>> {
    const res = await apiFetch('/v2/runtime/reset/tasks', { method: 'POST' });
    return handleResponse(res, 'Failed to reset tasks');
  },
};

// ============================================================================
// V2 P3 Advanced Routes — LLM Runtime Status
// ============================================================================

export const llmRuntimeStatusService = {
  /** GET /v2/llm/runtime-status — LLM runtime status */
  async getStatus(): Promise<ApiResult<import('./api.types').LLMRuntimeStatusResponse>> {
    const res = await apiFetch('/v2/llm/runtime-status');
    return handleResponse(res, 'Failed to load LLM runtime status');
  },

  /** GET /v2/llm/runtime-status/{role_id} — Role runtime status */
  async getRoleStatus(roleId: string): Promise<ApiResult<import('./api.types').RoleRuntimeStatusResponse>> {
    const res = await apiFetch(`/v2/llm/runtime-status/${encodeURIComponent(roleId)}`);
    return handleResponse(res, 'Failed to load role runtime status');
  },
};
