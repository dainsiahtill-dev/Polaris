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
    return handleResponse(res, '启动尚书令失败');
  },

  async stopPm(): Promise<ApiResult<void>> {
    const res = await apiFetch('/v2/pm/stop', { method: 'POST' });
    return handleResponse(res, '停止尚书令失败');
  },

  async runPmOnce(): Promise<ApiResult<void>> {
    const res = await apiFetch('/v2/pm/run_once', { method: 'POST' });
    return handleResponse(res, '尚书令单次督办失败');
  },

  async startDirector(): Promise<ApiResult<void>> {
    const res = await apiFetch('/v2/director/start', { method: 'POST' });
    return handleResponse(res, '启动工部尚书失败');
  },

  async stopDirector(): Promise<ApiResult<void>> {
    const res = await apiFetch('/v2/director/stop', { method: 'POST' });
    return handleResponse(res, '停止工部尚书失败');
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
    const res = await apiFetch('/llm/status');
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
