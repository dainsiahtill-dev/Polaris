/**
 * Runtime Parsing Utilities - 运行时数据解析工具
 * 
 * 纯函数，用于解析和转换运行时数据
 */

import type { LogEntry } from '@/types/log';
import type { PmTask } from '@/types/task';
import { TaskStatus } from '@/types/task';
import type { BackendStatus, EngineStatus } from '@/app/types/appContracts';
import type { FileEditEvent } from '@/app/hooks/useRuntime';
import type { TaskTraceEvent } from '../types/taskTrace';

// ============================================================
// 类型guards
// ============================================================

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function toStringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function toNumberValue(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = Number(value.trim());
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
}

// ============================================================
// 阶段标准化
// ============================================================

export function normalizePhaseToken(value: unknown): string {
  const token = String(value || '').trim().toLowerCase();
  if (!token) return '';
  
  if (['idle', 'planning', 'analyzing', 'executing', 'llm_calling', 'tool_running', 'verification', 'completed', 'error'].includes(token)) {
    return token;
  }
  if (token === 'failed' || token === 'blocked' || token === 'cancelled' || token === 'canceled') {
    return 'error';
  }
  if (token === 'implementation' || token.startsWith('director_')) {
    if (token === 'director_completed') return 'verification';
    if (token.includes('failed') || token.includes('deadlock')) return 'error';
    return 'executing';
  }
  if (token.startsWith('qa_')) {
    if (token === 'qa_completed') return 'completed';
    if (token === 'qa_skipped' || token.includes('failed')) return 'error';
    return 'verification';
  }
  if (token.startsWith('pm_') || token === 'intake' || token === 'docs_check' || token === 'architect') {
    if (token === 'pm_completed') return 'completed';
    if (token === 'pm_failed') return 'error';
    return 'planning';
  }
  if (token === 'handover') return 'completed';
  return '';
}

// ============================================================
// Director 状态解析
// ============================================================

export function getWorkflowStage(payload: {
  director_status?: BackendStatus | null;
  snapshot?: Record<string, unknown> | null;
}): string {
  const directorRoot = isRecord(payload.director_status)
    ? (payload.director_status as unknown as Record<string, unknown>)
    : null;
  const directorStatus = directorRoot && isRecord(directorRoot.status) ? directorRoot.status : null;
  const directorNested = directorStatus && isRecord(directorStatus.status) ? directorStatus.status : null;
  
  for (const candidate of [directorNested?.stage, directorStatus?.stage, directorRoot?.stage]) {
    if (typeof candidate === 'string' && candidate.trim()) {
      return candidate;
    }
  }

  const snapshotWorkflow = isRecord(payload.snapshot?.workflow) ? payload.snapshot.workflow : null;
  if (typeof snapshotWorkflow?.stage === 'string') {
    return snapshotWorkflow.stage;
  }
  return '';
}

export function parseDirectorStateToken(
  directorStatus: BackendStatus | null | undefined,
): { running: boolean; state: string } {
  const root = isRecord(directorStatus) ? (directorStatus as unknown as Record<string, unknown>) : null;
  const nested = root && isRecord(root.status) ? root.status : null;
  const deepNested = nested && isRecord(nested.status) ? nested.status : null;
  
  const token =
    toStringValue(root?.state) ||
    toStringValue(nested?.state) ||
    toStringValue(deepNested?.state);
    
  const running =
    Boolean((directorStatus as BackendStatus | null | undefined)?.running) ||
    token.toUpperCase() === 'RUNNING';
    
  return { running, state: token.toLowerCase() };
}

export function inferDirectorPhase(directorStatus: BackendStatus | null | undefined): string {
  const { running, state } = parseDirectorStateToken(directorStatus);
  const tasks = extractDirectorTasks(directorStatus);
  const taskStates = tasks.map((task) =>
    toStringValue((task as unknown as Record<string, unknown>).status || task.state).toLowerCase()
  );
  
  const hasFailed = taskStates.some((token) => token === 'failed' || token === 'blocked' || token === 'error');
  if (hasFailed || state === 'failed' || state === 'error') return 'error';
  
  const hasRunningTask = taskStates.some((token) =>
    token === 'running' || token === 'in_progress' || token === 'claimed' || token === 'executing'
  );
  if (hasRunningTask || running) return 'executing';
  
  if (taskStates.length > 0 && taskStates.every((token) => token === 'completed' || token === 'done' || token === 'success')) {
    return 'completed';
  }
  return '';
}

// ============================================================
// Worker 解析
// ============================================================

export function normalizeWorkerStatus(value: unknown): 'idle' | 'busy' | 'stopping' | 'stopped' | 'failed' | string {
  const token = toStringValue(value).toLowerCase();
  if (!token) return 'idle';
  if (['busy', 'idle', 'stopping', 'stopped', 'failed'].includes(token)) return token;
  if (['running', 'claimed', 'in_progress'].includes(token)) return 'busy';
  if (['completed', 'success'].includes(token)) return 'idle';
  if (token === 'error') return 'failed';
  return token;
}

export function normalizeWorker(input: Record<string, unknown>): { id: string; name?: string; status: string; currentTaskId?: string; healthy?: boolean; tasksCompleted?: number; tasksFailed?: number } | null {
  const id = toStringValue(input.id) || toStringValue(input.worker_id) || toStringValue(input.name);
  if (!id) return null;
  
  const health = isRecord(input.health) ? input.health : null;
  const currentTaskId =
    toStringValue(input.currentTaskId) ||
    toStringValue(input.current_task_id) ||
    toStringValue(input.task_id);
  const tasksCompleted =
    toNumberValue(input.tasksCompleted) ??
    toNumberValue(input.tasks_completed) ??
    (health ? toNumberValue(health.tasks_completed) : undefined);
  const tasksFailed =
    toNumberValue(input.tasksFailed) ??
    toNumberValue(input.tasks_failed) ??
    (health ? toNumberValue(health.tasks_failed) : undefined);
  const healthy =
    typeof input.healthy === 'boolean'
      ? input.healthy
      : health && typeof health.is_healthy === 'boolean'
      ? health.is_healthy
      : undefined;
      
  return {
    id,
    name: toStringValue(input.name) || undefined,
    status: normalizeWorkerStatus(input.status ?? input.state),
    currentTaskId: currentTaskId || undefined,
    healthy,
    tasksCompleted,
    tasksFailed,
  };
}

// ============================================================
// 任务解析
// ============================================================

function normalizeTaskStatus(statusRaw: string): TaskStatus {
  const status = statusRaw.toLowerCase();
  if (status === 'in_progress') return TaskStatus.IN_PROGRESS;
  if (status === 'completed') return TaskStatus.COMPLETED;
  if (status === 'failed') return TaskStatus.FAILED;
  if (status === 'blocked') return TaskStatus.BLOCKED;
  if (status === 'success') return TaskStatus.SUCCESS;
  return TaskStatus.PENDING;
}

export function normalizeTask(task: Record<string, unknown>, index: number): PmTask {
  const statusRaw = toStringValue(task.status || task.state).toLowerCase() || 'pending';
  const status = normalizeTaskStatus(statusRaw);
  const done = task.done === true || task.completed === true || 
    statusRaw === 'completed' || statusRaw === 'done' || statusRaw === 'success';
  
  const id =
    toStringValue(task.id) ||
    toStringValue(task.task_id) ||
    toStringValue(task.subject) ||
    toStringValue(task.title) ||
    `task-${index + 1}`;
    
  const title =
    toStringValue(task.title) ||
    toStringValue(task.subject) ||
    toStringValue(task.goal) ||
    id;
    
  const priority: number =
    typeof task.priority === 'number'
      ? task.priority
      : typeof task.priority === 'string'
        ? parseInt(task.priority, 10) || 0
        : 0;
        
  const acceptanceRaw = Array.isArray(task.acceptance) ? task.acceptance : [];
  const acceptance = acceptanceRaw
    .filter((item): item is Record<string, unknown> => isRecord(item))
    .map((item) => ({
      id: toStringValue(item.id) || undefined,
      description: toStringValue(item.description) || '待补充验收标准',
      status:
        toStringValue(item.status) === 'met' ||
        toStringValue(item.status) === 'failed' ||
        toStringValue(item.status) === 'pending'
          ? (toStringValue(item.status) as 'pending' | 'met' | 'failed')
          : undefined,
    }));

  return {
    ...(task as unknown as PmTask),
    id,
    title,
    status,
    state: toStringValue(task.state) || status,
    done,
    completed: done,
    priority,
    acceptance,
  };
}

export function normalizeTasks(candidate: unknown): PmTask[] {
  if (!Array.isArray(candidate)) return [];
  return candidate
    .filter((item): item is Record<string, unknown> => isRecord(item))
    .map((task, index) => normalizeTask(task, index));
}

function collectTaskArrayCandidates(source: Record<string, unknown> | null): unknown[] {
  if (!source) return [];
  const tasksContainer = isRecord(source.tasks) ? source.tasks : null;
  return [
    source.tasks,
    source.task_rows,
    source.tasks_list,
    tasksContainer?.task_rows,
    tasksContainer?.tasks_list,
    tasksContainer?.rows,
    tasksContainer?.items,
  ];
}

export function extractDirectorTasks(directorStatus: BackendStatus | null | undefined): PmTask[] {
  const root = isRecord(directorStatus) ? (directorStatus as unknown as Record<string, unknown>) : null;
  const nested = root && isRecord(root.status) ? root.status : null;
  const deepNested = nested && isRecord(nested.status) ? nested.status : null;
  
  const candidates: unknown[] = [
    ...collectTaskArrayCandidates(root),
    ...collectTaskArrayCandidates(nested),
    ...collectTaskArrayCandidates(deepNested),
  ];
  
  for (const candidate of candidates) {
    const tasks = normalizeTasks(candidate);
    if (tasks.length > 0) return tasks;
  }
  return [];
}

function collectWorkerArrayCandidates(source: Record<string, unknown> | null): unknown[] {
  if (!source) return [];
  const workersContainer = isRecord(source.workers) ? source.workers : null;
  return [
    source.workers,
    source.worker_rows,
    source.worker_list,
    workersContainer?.worker_rows,
    workersContainer?.worker_list,
    workersContainer?.rows,
    workersContainer?.items,
  ];
}

export function extractDirectorWorkers(directorStatus: BackendStatus | null | undefined): Array<{
  id: string;
  name?: string;
  status: string;
  currentTaskId?: string;
  healthy?: boolean;
  tasksCompleted?: number;
  tasksFailed?: number;
}> {
  const root = isRecord(directorStatus) ? (directorStatus as unknown as Record<string, unknown>) : null;
  const nested = root && isRecord(root.status) ? root.status : null;
  const deepNested = nested && isRecord(nested.status) ? nested.status : null;
  
  const candidates: unknown[] = [
    ...collectWorkerArrayCandidates(root),
    ...collectWorkerArrayCandidates(nested),
    ...collectWorkerArrayCandidates(deepNested),
  ];
  
  for (const candidate of candidates) {
    if (!Array.isArray(candidate)) continue;
    
    const workers = candidate
      .filter((item): item is Record<string, unknown> => isRecord(item))
      .map((worker) => normalizeWorker(worker))
      .filter ((worker): worker is NonNullable<ReturnType<typeof normalizeWorker>> => worker !== null);
      
    if (workers.length > 0) return workers;
  }
  return [];
}

// ============================================================
// Run ID 解析
// ============================================================

export function extractRunId(payload: {
  snapshot?: { run_id?: string } | null;
  engine_status?: { run_id?: string } | null;
  director_status?: BackendStatus | null;
}): string | null {
  const fromSnapshot = toStringValue(payload.snapshot?.run_id);
  const fromEngine = toStringValue(payload.engine_status?.run_id);
  
  const directorRoot = isRecord(payload.director_status)
    ? (payload.director_status as unknown as Record<string, unknown>)
    : null;
  const directorStatus = directorRoot && isRecord(directorRoot.status) ? directorRoot.status : null;
  
  const fromDirector =
    toStringValue(directorStatus?.run_id) ||
    toStringValue(directorStatus?.workflow_id) ||
    toStringValue(directorRoot?.run_id);
    
  return fromSnapshot || fromEngine || fromDirector || null;
}

// ============================================================
// 文件编辑事件解析
// ============================================================

export function parseFileEditEvent(
  event: Record<string, unknown>,
  timestamp: string,
  taskId?: string,
): FileEditEvent | null {
  const filePath =
    toStringValue(event.filePath) ||
    toStringValue(event.file_path) ||
    toStringValue(event.filepath);
  if (!filePath) return null;
  
  const rawOperation = toStringValue(event.operation).toLowerCase();
  const operation: 'create' | 'delete' | 'modify' =
    rawOperation === 'create' || rawOperation === 'delete' || rawOperation === 'modify'
      ? rawOperation
      : 'modify';
      
  const contentSize =
    toNumberValue(event.contentSize) ??
    toNumberValue(event.content_size) ??
    toNumberValue(event.size_bytes) ??
    0;
  const addedLines = toNumberValue(event.addedLines) ?? toNumberValue(event.added_lines);
  const deletedLines = toNumberValue(event.deletedLines) ?? toNumberValue(event.deleted_lines);
  const modifiedLines = toNumberValue(event.modifiedLines) ?? toNumberValue(event.modified_lines);
  
  return {
    id: toStringValue(event.id) || `${filePath}-${timestamp}`,
    filePath,
    operation,
    contentSize,
    taskId,
    timestamp,
    patch: toStringValue(event.patch) || undefined,
    addedLines: typeof addedLines === 'number' ? Math.max(0, addedLines) : undefined,
    deletedLines: typeof deletedLines === 'number' ? Math.max(0, deletedLines) : undefined,
    modifiedLines: typeof modifiedLines === 'number' ? Math.max(0, modifiedLines) : undefined,
  };
}

function readFileEditSchemaMetadata(event: Record<string, unknown>): Pick<
  FileEditEvent,
  'schemaVersion' | 'eventSchema' | 'sourceChannel' | 'eventKind' | 'provenance'
> {
  const schemaVersion =
    toStringValue(event.schemaVersion) ||
    toStringValue(event.schema_version) ||
    toStringValue(event.protocol);
  const eventSchema = toStringValue(event.eventSchema) || toStringValue(event.event_schema);
  const sourceChannel =
    toStringValue(event.sourceChannel) ||
    toStringValue(event.channel) ||
    toStringValue(event.category);
  const eventKind =
    toStringValue(event.eventKind) ||
    toStringValue(event.kind) ||
    toStringValue(event.event) ||
    toStringValue(event.name) ||
    toStringValue(event.type);
  const provenance =
    toStringValue(event.provenance) ||
    toStringValue(event.source) ||
    (sourceChannel ? `ws:${sourceChannel}` : '');

  return {
    schemaVersion: schemaVersion || undefined,
    eventSchema: eventSchema || undefined,
    sourceChannel: sourceChannel || undefined,
    eventKind: eventKind || undefined,
    provenance: provenance || undefined,
  };
}

export function extractFileEditEvents(payload: {
  event?: Record<string, unknown> | null;
  timestamp?: string;
}): FileEditEvent | null {
  const event = isRecord(payload.event) ? payload.event : null;
  if (!event) return null;
  
  const filePath =
    toStringValue(event.filePath) ||
    toStringValue(event.file_path) ||
    toStringValue(event.filepath);
  if (!filePath) return null;
  
  const rawOperation = toStringValue(event.operation).toLowerCase();
  const operation: FileEditEvent['operation'] =
    rawOperation === 'create' || rawOperation === 'delete' || rawOperation === 'modify'
      ? rawOperation
      : 'modify';
      
  const contentSize =
    toNumberValue(event.contentSize) ??
    toNumberValue(event.content_size) ??
    toNumberValue(event.size_bytes) ??
    0;
  const addedLines = toNumberValue(event.addedLines) ?? toNumberValue(event.added_lines);
  const deletedLines = toNumberValue(event.deletedLines) ?? toNumberValue(event.deleted_lines);
  const modifiedLines = toNumberValue(event.modifiedLines) ?? toNumberValue(event.modified_lines);
  const timestamp = toStringValue(event.timestamp) || toStringValue(payload.timestamp) || new Date().toISOString();
  const schemaMetadata = readFileEditSchemaMetadata(event);
  
  return {
    id: toStringValue(event.id) || `${filePath}-${timestamp}`,
    filePath,
    operation,
    contentSize,
    taskId: toStringValue(event.taskId) || toStringValue(event.task_id) || undefined,
    timestamp,
    patch: toStringValue(event.patch) || undefined,
    addedLines: typeof addedLines === 'number' ? Math.max(0, addedLines) : undefined,
    deletedLines: typeof deletedLines === 'number' ? Math.max(0, deletedLines) : undefined,
    modifiedLines: typeof modifiedLines === 'number' ? Math.max(0, modifiedLines) : undefined,
    ...schemaMetadata,
  };
}

function fileEditCandidateFromRuntimeEvent(event: Record<string, unknown>): Record<string, unknown> | null {
  const eventToken = toStringValue(event.event || event.name || event.kind || event.type || event.event_name)
    .toLowerCase();
  const channelToken = toStringValue(event.channel || event.category).toLowerCase();
  const domainToken = toStringValue(event.domain).toLowerCase();
  const payload = isRecord(event.payload) ? event.payload : null;
  const data = isRecord(event.data) ? event.data : null;
  const raw = isRecord(payload?.raw) ? payload.raw : null;
  const nestedPayload = isRecord(payload?.payload) ? payload.payload : null;
  const nestedEvent = isRecord(event.event) ? event.event : null;

  const candidates = [
    raw,
    data,
    nestedPayload,
    nestedEvent,
    payload,
    event,
  ].filter((item): item is Record<string, unknown> => Boolean(item));

  const hasFileEditShape = candidates.some((candidate) => {
    return Boolean(toStringValue(candidate.filePath) || toStringValue(candidate.file_path) || toStringValue(candidate.filepath));
  });
  if (!hasFileEditShape) return null;

  const isFileEditEvent =
    channelToken === 'event.file_edit' ||
    channelToken === 'file_edit' ||
    domainToken === 'file_edit' ||
    eventToken === 'file_edit' ||
    eventToken === 'file_written' ||
    eventToken === 'file.write' ||
    eventToken === 'file_written_event' ||
    eventToken.endsWith('.file_edit') ||
    eventToken.endsWith('.file_written');

  if (!isFileEditEvent) return null;
  return candidates.find((candidate) =>
    Boolean(toStringValue(candidate.filePath) || toStringValue(candidate.file_path) || toStringValue(candidate.filepath))
  ) || null;
}

export function extractRuntimeFileEditEvent(event: Record<string, unknown>): FileEditEvent | null {
  const candidate = fileEditCandidateFromRuntimeEvent(event);
  if (!candidate) return null;
  const timestamp =
    toStringValue(candidate.timestamp) ||
    toStringValue(event.timestamp) ||
    toStringValue(event.ts) ||
    new Date().toISOString();
  return extractFileEditEvents({
    event: {
      ...event,
      ...candidate,
      schema_version: candidate.schema_version || event.schema_version,
      event_schema: candidate.event_schema || event.event_schema,
      channel: candidate.channel || event.channel || event.category,
      kind: candidate.kind || event.kind || event.event || event.name,
      source: candidate.source || event.source,
    },
    timestamp,
  });
}

// ============================================================
// 日志解析
// ============================================================

export function hashText(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

export function buildStableLogId(
  channel: string,
  raw: string,
  parsed: Record<string, unknown> | null,
): string {
  if (parsed) {
    const eventId = String(parsed.event_id || parsed.id || '').trim();
    if (eventId) {
      return `${channel}:${eventId}`;
    }
    
    const eventData = parsed.data && typeof parsed.data === 'object'
      ? (parsed.data as Record<string, unknown>)
      : null;
    const fingerprint = [
      String(parsed.run_id || parsed.runId || '').trim(),
      String(parsed.seq || '').trim(),
      String(parsed.ts || parsed.timestamp || parsed.time || '').trim(),
      String(parsed.event || parsed.name || parsed.kind || parsed.type || '').trim(),
      String(parsed.summary || parsed.message || parsed.text || '').trim(),
      eventData ? String(eventData.stage || '').trim() : '',
      raw,
    ]
      .filter((item) => item.length > 0)
      .join('|');
    return `${channel}:${hashText(fingerprint || raw)}`;
  }
  return `${channel}:${hashText(raw)}`;
}

export function tryParseJsonObject(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*m/g, '');
}

export function mapSeverityToLevel(
  severityRaw: string,
  fallback: LogEntry['level'] = 'info',
): LogEntry['level'] {
  const severity = severityRaw.trim().toLowerCase();
  if (!severity) return fallback;
  if (severity === 'error' || severity === 'critical') return 'error';
  if (severity === 'warn' || severity === 'warning') return 'warning';
  if (severity === 'debug') return 'thinking';
  return severity === 'info' ? 'info' : fallback;
}

export function normalizeActorLabel(raw: string): string {
  const token = String(raw || '').trim();
  const lookup: Record<string, string> = {
    pm: 'PM',
    director: 'Director',
    qa: 'QA',
    system: 'System',
    planner: 'Planner',
  };
  const mapped = lookup[token.toLowerCase()];
  return mapped || token;
}

export function appendLogEntries(prev: LogEntry[], incoming: LogEntry[], limit: number): LogEntry[] {
  if (incoming.length <= 0) return prev;
  const merged = [...prev];
  const seen = new Set(prev.map((item) => item.id));
  for (const entry of incoming) {
    if (seen.has(entry.id)) continue;
    seen.add(entry.id);
    merged.push(entry);
  }
  return merged.slice(-limit);
}
