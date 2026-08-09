/**
 * Runtime Parsing Utilities - 运行时数据解析工具
 *
 * 纯函数，用于解析和转换运行时数据
 */
import { TaskStatus } from '@/types/task';
// ============================================================
// 类型guards
// ============================================================
export function isRecord(value) {
    return typeof value === 'object' && value !== null;
}
export function toStringValue(value) {
    return typeof value === 'string' ? value.trim() : '';
}
function safeJsonStringify(value) {
    const seen = new WeakSet();
    const encoded = JSON.stringify(value, (_key, next) => {
        if (typeof next === 'bigint') {
            return String(next);
        }
        if (typeof next === 'object' && next !== null) {
            if (seen.has(next)) {
                return '[Circular]';
            }
            seen.add(next);
        }
        return next;
    });
    return typeof encoded === 'string' ? encoded : '';
}
const DISPLAY_TEXT_KEYS = [
    'text',
    'message',
    'summary',
    'title',
    'detail',
    'description',
    'error_message',
    'error',
];
function displayScalarFromRecord(value, depth = 0) {
    for (const key of DISPLAY_TEXT_KEYS) {
        const candidate = value[key];
        if (typeof candidate === 'string') {
            const text = candidate.trim();
            if (text)
                return text;
        }
        if (typeof candidate === 'number' && Number.isFinite(candidate))
            return String(candidate);
        if (typeof candidate === 'boolean' || typeof candidate === 'bigint')
            return String(candidate);
        if (depth < 1 && isRecord(candidate)) {
            const nested = displayScalarFromRecord(candidate, depth + 1);
            if (nested)
                return nested;
        }
    }
    return '';
}
export function toDisplayString(value) {
    if (value === null || value === undefined)
        return '';
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number')
        return Number.isFinite(value) ? String(value) : '';
    if (typeof value === 'boolean' || typeof value === 'bigint')
        return String(value);
    if (value instanceof Error)
        return (value.message || value.name || '').trim();
    if (typeof value === 'object') {
        const scalar = displayScalarFromRecord(value);
        if (scalar)
            return scalar;
        try {
            return safeJsonStringify(value).trim();
        }
        catch {
            return '';
        }
    }
    return '';
}
export function firstDisplayString(...candidates) {
    for (const candidate of candidates) {
        const value = toDisplayString(candidate);
        if (value)
            return value;
    }
    return '';
}
export function toNumberValue(value) {
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
const FILE_PATH_KEYS = ['filePath', 'file_path', 'filepath', 'path', 'file', 'filename'];
const PATCH_KEYS = ['patch', 'diff', 'unified_diff', 'patch_text', 'diff_text'];
const TASK_ID_KEYS = ['taskId', 'task_id', 'pm_task_id', 'director_task_id'];
const EVENT_TOKEN_KEYS = ['event', 'name', 'kind', 'type', 'event_name'];
const LOG_REF_KEYS = ['contextSnapshotRef', 'context_snapshot_ref', 'promptHash', 'prompt_hash', 'turnId', 'turn_id', 'runId', 'run_id'];
const LOG_USAGE_KEYS = ['promptTokens', 'completionTokens', 'totalTokens', 'contextTokens', 'durationMs'];
function readFirstString(source, keys) {
    if (!source)
        return '';
    for (const key of keys) {
        const value = toStringValue(source[key]);
        if (value)
            return value;
    }
    return '';
}
function readNestedMetadata(source) {
    if (!source)
        return null;
    return isRecord(source.metadata) ? source.metadata : null;
}
function readFilePathValue(source) {
    return readFirstString(source, FILE_PATH_KEYS);
}
function readPatchValue(source) {
    return readFirstString(source, PATCH_KEYS);
}
function readTaskIdValue(source) {
    const direct = readFirstString(source, TASK_ID_KEYS);
    if (direct)
        return direct;
    return readFirstString(readNestedMetadata(source), TASK_ID_KEYS);
}
function readEventTokenValue(source) {
    return readFirstString(source, EVENT_TOKEN_KEYS);
}
function normalizeLogMetaValue(value) {
    if (typeof value === 'number' && Number.isFinite(value))
        return String(Math.round(value));
    return toDisplayString(value);
}
function readFirstLogMetaValue(source, keys) {
    if (!source)
        return '';
    for (const key of keys) {
        const value = normalizeLogMetaValue(source[key]);
        if (value)
            return value;
    }
    return '';
}
export function logEntryDedupeKey(log) {
    const meta = isRecord(log.meta) ? log.meta : {};
    const channel = readFirstLogMetaValue(meta, ['channel']);
    const streamEvent = readFirstLogMetaValue(meta, ['streamEvent', 'stream_event', 'event', 'event_type']);
    const role = readFirstLogMetaValue(meta, ['role', 'actor', 'source']) || String(log.source || '').trim();
    const stableRef = readFirstLogMetaValue(meta, LOG_REF_KEYS);
    const usage = LOG_USAGE_KEYS.map((key) => readFirstLogMetaValue(meta, [key])).join('/');
    return [
        channel,
        streamEvent,
        role,
        stableRef,
        String(log.timestamp || '').trim(),
        String(log.level || '').trim(),
        String(log.source || '').trim(),
        String(log.title || '').trim(),
        String(log.message || '').trim().replace(/\s+/g, ' '),
        String(log.details || '').trim().replace(/\s+/g, ' '),
        usage,
    ].join('\u001f');
}
// ============================================================
// 阶段标准化
// ============================================================
export function normalizePhaseToken(value) {
    const token = String(value || '').trim().toLowerCase();
    if (!token)
        return '';
    if (['idle', 'planning', 'analyzing', 'executing', 'llm_calling', 'tool_running', 'verification', 'completed', 'error'].includes(token)) {
        return token;
    }
    if (token === 'failed' || token === 'blocked' || token === 'cancelled' || token === 'canceled') {
        return 'error';
    }
    if (token === 'implementation' || token.startsWith('director_')) {
        if (token === 'director_completed')
            return 'verification';
        if (token.includes('failed') || token.includes('deadlock'))
            return 'error';
        return 'executing';
    }
    if (token.startsWith('qa_')) {
        if (token === 'qa_completed')
            return 'completed';
        if (token === 'qa_skipped' || token.includes('failed'))
            return 'error';
        return 'verification';
    }
    if (token.startsWith('pm_') || token === 'intake' || token === 'docs_check' || token === 'architect') {
        if (token === 'pm_completed')
            return 'completed';
        if (token === 'pm_failed')
            return 'error';
        return 'planning';
    }
    if (token === 'handover')
        return 'completed';
    return '';
}
// ============================================================
// Director 状态解析
// ============================================================
export function getWorkflowStage(payload) {
    const directorRoot = isRecord(payload.director_status)
        ? payload.director_status
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
export function parseDirectorStateToken(directorStatus) {
    const root = isRecord(directorStatus) ? directorStatus : null;
    const nested = root && isRecord(root.status) ? root.status : null;
    const deepNested = nested && isRecord(nested.status) ? nested.status : null;
    const rawToken = toStringValue(root?.execution_state) ||
        toStringValue(root?.state) ||
        toStringValue(nested?.execution_state) ||
        toStringValue(nested?.state) ||
        toStringValue(deepNested?.execution_state) ||
        toStringValue(deepNested?.state);
    const token = normalizeDirectorRuntimeStateToken(rawToken);
    const running = (Boolean(directorStatus?.running) &&
        !['error', 'failed', 'blocked'].includes(token)) ||
        token === 'running' ||
        token === 'recovering';
    return { running, state: token };
}
function normalizeDirectorRuntimeStateToken(value) {
    const token = toStringValue(value).toLowerCase();
    if (!token)
        return '';
    if (['running', 'working', 'active', 'executing', 'in_progress', 'in-progress'].includes(token))
        return 'running';
    if (['recovering', 'retrying'].includes(token))
        return 'recovering';
    if (['completed', 'complete', 'done', 'success', 'succeeded', 'stopped', 'completed_verified'].includes(token))
        return 'completed';
    if (['failed', 'failure', 'error', 'failed_platform', 'failed_artifact', 'blocked_with_reason'].includes(token) ||
        token.startsWith('failed_') ||
        token.includes('failure') ||
        token.includes('error')) {
        return 'error';
    }
    if (['blocked', 'waiting_human'].includes(token))
        return 'blocked';
    if (['pending', 'idle', 'ready', 'waiting'].includes(token))
        return 'idle';
    return token;
}
export function inferDirectorPhase(directorStatus) {
    const { running, state } = parseDirectorStateToken(directorStatus);
    const tasks = extractDirectorTasks(directorStatus);
    const taskStates = tasks.map((task) => toStringValue(task.status || task.state).toLowerCase());
    const hasFailed = taskStates.some((token) => token === 'failed' || token === 'blocked' || token === 'error');
    if (hasFailed || state === 'failed' || state === 'error' || state === 'blocked')
        return 'error';
    const hasRunningTask = taskStates.some((token) => token === 'running' || token === 'in_progress' || token === 'claimed' || token === 'executing');
    if (hasRunningTask || running)
        return 'executing';
    if (taskStates.length > 0 && taskStates.every((token) => token === 'completed' || token === 'done' || token === 'success')) {
        return 'completed';
    }
    return '';
}
// ============================================================
// Worker 解析
// ============================================================
export function normalizeWorkerStatus(value) {
    const token = toStringValue(value).toLowerCase();
    if (!token)
        return 'idle';
    if (['busy', 'idle', 'stopping', 'stopped', 'failed'].includes(token))
        return token;
    if (['running', 'claimed', 'in_progress'].includes(token))
        return 'busy';
    if (['completed', 'success'].includes(token))
        return 'idle';
    if (token === 'error')
        return 'failed';
    return token;
}
export function normalizeWorker(input) {
    const id = toStringValue(input.id) || toStringValue(input.worker_id) || toStringValue(input.name);
    if (!id)
        return null;
    const health = isRecord(input.health) ? input.health : null;
    const currentTaskId = toStringValue(input.currentTaskId) ||
        toStringValue(input.current_task_id) ||
        toStringValue(input.task_id);
    const tasksCompleted = toNumberValue(input.tasksCompleted) ??
        toNumberValue(input.tasks_completed) ??
        (health ? toNumberValue(health.tasks_completed) : undefined);
    const tasksFailed = toNumberValue(input.tasksFailed) ??
        toNumberValue(input.tasks_failed) ??
        (health ? toNumberValue(health.tasks_failed) : undefined);
    const healthy = typeof input.healthy === 'boolean'
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
function normalizeTaskStatus(statusRaw) {
    const status = statusRaw.toLowerCase();
    if (status === 'in_progress')
        return TaskStatus.IN_PROGRESS;
    if (status === 'completed')
        return TaskStatus.COMPLETED;
    if (status === 'failed')
        return TaskStatus.FAILED;
    if (status === 'blocked')
        return TaskStatus.BLOCKED;
    if (status === 'success')
        return TaskStatus.SUCCESS;
    return TaskStatus.PENDING;
}
export function normalizeTask(task, index) {
    const statusRaw = toStringValue(task.status || task.state).toLowerCase() || 'pending';
    const status = normalizeTaskStatus(statusRaw);
    const done = task.done === true || task.completed === true ||
        statusRaw === 'completed' || statusRaw === 'done' || statusRaw === 'success';
    const id = toStringValue(task.id) ||
        toStringValue(task.task_id) ||
        toStringValue(task.subject) ||
        toStringValue(task.title) ||
        `task-${index + 1}`;
    const title = toStringValue(task.title) ||
        toStringValue(task.subject) ||
        toStringValue(task.goal) ||
        id;
    const priority = typeof task.priority === 'number'
        ? task.priority
        : typeof task.priority === 'string'
            ? parseInt(task.priority, 10) || 0
            : 0;
    const acceptanceRaw = Array.isArray(task.acceptance) ? task.acceptance : [];
    const acceptance = acceptanceRaw
        .filter((item) => isRecord(item))
        .map((item) => ({
        id: toStringValue(item.id) || undefined,
        description: toStringValue(item.description) || '待补充验收标准',
        status: toStringValue(item.status) === 'met' ||
            toStringValue(item.status) === 'failed' ||
            toStringValue(item.status) === 'pending'
            ? toStringValue(item.status)
            : undefined,
    }));
    return {
        ...task,
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
export function normalizeTasks(candidate) {
    if (!Array.isArray(candidate))
        return [];
    return candidate
        .filter((item) => isRecord(item))
        .map((task, index) => normalizeTask(task, index));
}
function collectTaskArrayCandidates(source) {
    if (!source)
        return [];
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
export function extractDirectorTasks(directorStatus) {
    const root = isRecord(directorStatus) ? directorStatus : null;
    const nested = root && isRecord(root.status) ? root.status : null;
    const deepNested = nested && isRecord(nested.status) ? nested.status : null;
    const candidates = [
        ...collectTaskArrayCandidates(root),
        ...collectTaskArrayCandidates(nested),
        ...collectTaskArrayCandidates(deepNested),
    ];
    for (const candidate of candidates) {
        const tasks = normalizeTasks(candidate);
        if (tasks.length > 0)
            return tasks;
    }
    return [];
}
function collectWorkerArrayCandidates(source) {
    if (!source)
        return [];
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
export function extractDirectorWorkers(directorStatus) {
    const root = isRecord(directorStatus) ? directorStatus : null;
    const nested = root && isRecord(root.status) ? root.status : null;
    const deepNested = nested && isRecord(nested.status) ? nested.status : null;
    const candidates = [
        ...collectWorkerArrayCandidates(root),
        ...collectWorkerArrayCandidates(nested),
        ...collectWorkerArrayCandidates(deepNested),
    ];
    for (const candidate of candidates) {
        if (!Array.isArray(candidate))
            continue;
        const workers = candidate
            .filter((item) => isRecord(item))
            .map((worker) => normalizeWorker(worker))
            .filter((worker) => worker !== null);
        if (workers.length > 0)
            return workers;
    }
    return [];
}
// ============================================================
// Run ID 解析
// ============================================================
export function extractRunId(payload) {
    const fromSnapshot = toStringValue(payload.snapshot?.run_id);
    const fromEngine = toStringValue(payload.engine_status?.run_id);
    const directorRoot = isRecord(payload.director_status)
        ? payload.director_status
        : null;
    const directorStatus = directorRoot && isRecord(directorRoot.status) ? directorRoot.status : null;
    const fromDirector = toStringValue(directorStatus?.run_id) ||
        toStringValue(directorStatus?.workflow_id) ||
        toStringValue(directorRoot?.run_id);
    return fromSnapshot || fromEngine || fromDirector || null;
}
// ============================================================
// 文件编辑事件解析
// ============================================================
export function parseFileEditEvent(event, timestamp, taskId) {
    const filePath = readFilePathValue(event);
    if (!filePath)
        return null;
    const rawOperation = toStringValue(event.operation).toLowerCase();
    const operation = rawOperation === 'create' || rawOperation === 'delete' || rawOperation === 'modify'
        ? rawOperation
        : 'modify';
    const contentSize = toNumberValue(event.contentSize) ??
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
        taskId: taskId || readTaskIdValue(event) || undefined,
        timestamp,
        patch: readPatchValue(event) || undefined,
        diffStatus: toStringValue(event.diffStatus) || toStringValue(event.diff_status) || undefined,
        patchUnavailableReason: toStringValue(event.patchUnavailableReason) || toStringValue(event.patch_unavailable_reason) || undefined,
        hasPatch: typeof event.hasPatch === 'boolean'
            ? event.hasPatch
            : typeof event.has_patch === 'boolean'
                ? event.has_patch
                : undefined,
        addedLines: typeof addedLines === 'number' ? Math.max(0, addedLines) : undefined,
        deletedLines: typeof deletedLines === 'number' ? Math.max(0, deletedLines) : undefined,
        modifiedLines: typeof modifiedLines === 'number' ? Math.max(0, modifiedLines) : undefined,
    };
}
function readFileEditSchemaMetadata(event) {
    const schemaVersion = toStringValue(event.schemaVersion) ||
        toStringValue(event.schema_version) ||
        toStringValue(event.protocol);
    const eventSchema = toStringValue(event.eventSchema) || toStringValue(event.event_schema);
    const sourceChannel = toStringValue(event.sourceChannel) ||
        toStringValue(event.channel) ||
        toStringValue(event.category);
    const eventKind = toStringValue(event.eventKind) ||
        readEventTokenValue(event);
    const provenance = toStringValue(event.provenance) ||
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
export function extractFileEditEvents(payload) {
    const event = isRecord(payload.event) ? payload.event : null;
    if (!event)
        return null;
    const filePath = readFilePathValue(event);
    if (!filePath)
        return null;
    const rawOperation = toStringValue(event.operation).toLowerCase();
    const operation = rawOperation === 'create' || rawOperation === 'delete' || rawOperation === 'modify'
        ? rawOperation
        : 'modify';
    const contentSize = toNumberValue(event.contentSize) ??
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
        taskId: readTaskIdValue(event) || undefined,
        timestamp,
        patch: readPatchValue(event) || undefined,
        diffStatus: toStringValue(event.diffStatus) || toStringValue(event.diff_status) || undefined,
        patchUnavailableReason: toStringValue(event.patchUnavailableReason) || toStringValue(event.patch_unavailable_reason) || undefined,
        hasPatch: typeof event.hasPatch === 'boolean'
            ? event.hasPatch
            : typeof event.has_patch === 'boolean'
                ? event.has_patch
                : undefined,
        addedLines: typeof addedLines === 'number' ? Math.max(0, addedLines) : undefined,
        deletedLines: typeof deletedLines === 'number' ? Math.max(0, deletedLines) : undefined,
        modifiedLines: typeof modifiedLines === 'number' ? Math.max(0, modifiedLines) : undefined,
        ...schemaMetadata,
    };
}
function fileEditCandidateFromRuntimeEvent(event) {
    const eventToken = readEventTokenValue(event).toLowerCase();
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
    ].filter((item) => Boolean(item));
    const hasFileEditShape = candidates.some((candidate) => {
        return Boolean(readFilePathValue(candidate));
    });
    if (!hasFileEditShape)
        return null;
    const isFileEditEvent = channelToken === 'event.file_edit' ||
        channelToken === 'file_edit' ||
        domainToken === 'file_edit' ||
        eventToken === 'file_edit' ||
        eventToken === 'file_written' ||
        eventToken === 'file_changed' ||
        eventToken === 'file_change' ||
        eventToken === 'file.write' ||
        eventToken === 'file.change' ||
        eventToken === 'file.modified' ||
        eventToken === 'file.created' ||
        eventToken === 'file.deleted' ||
        eventToken === 'file_written_event' ||
        eventToken.endsWith('.file_edit') ||
        eventToken.endsWith('.file_change') ||
        eventToken.endsWith('.file_written');
    if (!isFileEditEvent)
        return null;
    return candidates.find((candidate) => Boolean(readFilePathValue(candidate))) || null;
}
export function extractRuntimeFileEditEvent(event) {
    const candidate = fileEditCandidateFromRuntimeEvent(event);
    if (!candidate)
        return null;
    const timestamp = toStringValue(candidate.timestamp) ||
        toStringValue(event.timestamp) ||
        toStringValue(event.ts) ||
        new Date().toISOString();
    const eventKind = readEventTokenValue(candidate) || readEventTokenValue(event);
    return extractFileEditEvents({
        event: {
            ...event,
            ...candidate,
            schema_version: candidate.schema_version || event.schema_version,
            event_schema: candidate.event_schema || event.event_schema,
            channel: candidate.channel || event.channel || event.category,
            kind: eventKind,
            source: candidate.source || event.source,
        },
        timestamp,
    });
}
// ============================================================
// 日志解析
// ============================================================
export function hashText(value) {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
        hash ^= value.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, '0');
}
export function buildStableLogId(channel, raw, parsed) {
    if (parsed) {
        const eventId = String(parsed.event_id || parsed.id || '').trim();
        if (eventId) {
            return `${channel}:${eventId}`;
        }
        const eventData = parsed.data && typeof parsed.data === 'object'
            ? parsed.data
            : null;
        const fingerprint = [
            firstDisplayString(parsed.run_id, parsed.runId),
            firstDisplayString(parsed.seq),
            firstDisplayString(parsed.ts, parsed.timestamp, parsed.time),
            toStringValue(parsed.event) || toStringValue(parsed.name) || toStringValue(parsed.kind) || toStringValue(parsed.type),
            firstDisplayString(parsed.summary, parsed.message, parsed.text),
            eventData ? firstDisplayString(eventData.stage) : '',
            raw,
        ]
            .filter((item) => item.length > 0)
            .join('|');
        return `${channel}:${hashText(fingerprint || raw)}`;
    }
    return `${channel}:${hashText(raw)}`;
}
export function tryParseJsonObject(raw) {
    try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : null;
    }
    catch {
        return null;
    }
}
export function stripAnsi(text) {
    return text.replace(/\x1b\[[0-9;]*m/g, '');
}
export function mapSeverityToLevel(severityRaw, fallback = 'info') {
    const severity = severityRaw.trim().toLowerCase();
    if (!severity)
        return fallback;
    if (severity === 'error' || severity === 'critical')
        return 'error';
    if (severity === 'warn' || severity === 'warning')
        return 'warning';
    if (severity === 'debug')
        return 'thinking';
    return severity === 'info' ? 'info' : fallback;
}
export function normalizeActorLabel(raw) {
    const token = String(raw || '').trim();
    const lookup = {
        pm: 'PM',
        director: 'Director',
        qa: 'QA',
        system: 'System',
        planner: 'Planner',
    };
    const mapped = lookup[token.toLowerCase()];
    return mapped || token;
}
export function appendLogEntries(prev, incoming, limit) {
    if (incoming.length <= 0)
        return prev;
    const merged = [...prev];
    const seen = new Set(prev.map((item) => logEntryDedupeKey(item)));
    for (const entry of incoming) {
        const key = logEntryDedupeKey(entry);
        if (seen.has(key))
            continue;
        seen.add(key);
        merged.push(entry);
    }
    return merged.slice(-limit);
}
