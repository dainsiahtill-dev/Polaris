import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, ArrowRight, Brain, CheckCircle2, ChevronLeft, FileCode, FilePlus, FileText, GitBranch, Hammer, Loader2, MessageSquare, Play, Settings, ShieldCheck, Trash2, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { AIDialoguePanel } from '@/app/components/ai-dialogue';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import { bulkGenerateChiefEngineerBlueprints, generateChiefEngineerBlueprint, deleteChiefEngineerBlueprint, getChiefEngineerDiagnostics, getChiefEngineerBlueprint, getChiefEngineerBlueprintStatus, listChiefEngineerBlueprints, } from '@/services/chiefEngineerService';
import { getRoleCapabilities, resolveRoleCapabilities, } from '@/services/roleSessionService';
import { clearRoleKernelCache, getRoleKernelCacheStats, getRoleKernelLLMEvents, getRoleKernelTokenBudgetStats, listDirectorWorkers, } from '@/services';
import { ChiefEngineerWorkbenchPanel } from './ChiefEngineerWorkbenchPanel';
const RUNTIME_PUSH_ENDPOINT = '/v2/ws/runtime';
const DIRECTOR_COMMAND_ACCEPTED_MESSAGE = '命令已提交，等待 runtime.v2 推送确认。';
function normalizeToken(value) {
    return String(value || '').trim().toLowerCase();
}
function evidenceEndpoint(endpoint, workspace = '') {
    const value = String(workspace || '').trim();
    if (!value)
        return endpoint;
    const separator = endpoint.includes('?') ? '&' : '?';
    return `${endpoint}${separator}workspace=${encodeURIComponent(value)}`;
}
function EvidenceEndpointBadge({ endpoint, testId, }) {
    return (_jsx("span", { className: "shrink-0 rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] font-medium text-slate-500", title: endpoint, "data-endpoint": endpoint, "data-testid": testId, children: "API" }));
}
function blueprintStatusEvidenceEndpoint(taskId, workspace = '') {
    const query = new URLSearchParams({ task_id: taskId });
    const value = String(workspace || '').trim();
    if (value)
        query.set('workspace', value);
    return `/v2/chief-engineer/blueprints/status?${query.toString()}`;
}
function metadataOf(task) {
    return task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
}
function readString(task, keys) {
    const metadata = metadataOf(task);
    const direct = task;
    for (const key of keys) {
        const value = direct[key];
        if (typeof value === 'string' && value.trim())
            return value.trim();
        const metaValue = metadata[key];
        if (typeof metaValue === 'string' && metaValue.trim())
            return metaValue.trim();
    }
    return '';
}
function readStringList(value) {
    if (!Array.isArray(value))
        return [];
    return value
        .map((item) => {
        if (typeof item === 'string')
            return item.trim();
        if (item && typeof item === 'object') {
            const record = item;
            return String(record.path
                || record.file
                || record.description
                || record.text
                || record.title
                || record.name
                || record.id
                || record.value
                || '').trim();
        }
        return String(item || '').trim();
    })
        .filter(Boolean);
}
function readTaskStringList(task, keys) {
    const metadata = metadataOf(task);
    const direct = task;
    for (const key of keys) {
        const directList = readStringList(direct[key]);
        if (directList.length > 0)
            return directList;
        const metadataList = readStringList(metadata[key]);
        if (metadataList.length > 0)
            return metadataList;
    }
    return [];
}
function readTaskRecord(task, keys) {
    const metadata = metadataOf(task);
    const direct = task;
    for (const key of keys) {
        const directValue = direct[key];
        if (directValue && typeof directValue === 'object' && !Array.isArray(directValue)) {
            return directValue;
        }
        const metadataValue = metadata[key];
        if (metadataValue && typeof metadataValue === 'object' && !Array.isArray(metadataValue)) {
            return metadataValue;
        }
    }
    return {};
}
function readWorkerText(record, keys) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'string' && value.trim())
            return value.trim();
        if (typeof value === 'number' && Number.isFinite(value))
            return String(value);
    }
    return '';
}
function readWorkerNumber(record, keys) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'number' && Number.isFinite(value))
            return value;
        if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value)))
            return Number(value);
    }
    return undefined;
}
function readWorkerBoolean(record, keys) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'boolean')
            return value;
        if (typeof value === 'string' && value.trim()) {
            const normalized = value.trim().toLowerCase();
            if (['true', 'healthy', 'ok', 'ready'].includes(normalized))
                return true;
            if (['false', 'unhealthy', 'failed', 'error'].includes(normalized))
                return false;
        }
    }
    return undefined;
}
function normalizeDirectorWorkerRows(rows) {
    if (!Array.isArray(rows)) {
        return [];
    }
    return rows
        .map((row) => {
        if (!row || typeof row !== 'object')
            return null;
        const record = row;
        const id = readWorkerText(record, ['id', 'worker_id', 'name']);
        if (!id)
            return null;
        return {
            id,
            name: readWorkerText(record, ['name', 'display_name', 'worker_name']) || id,
            status: readWorkerText(record, ['status', 'state']) || 'idle',
            currentTaskId: readWorkerText(record, ['currentTaskId', 'current_task_id', 'task_id', 'current_task']) || undefined,
            healthy: readWorkerBoolean(record, ['healthy', 'is_healthy']),
            tasksCompleted: readWorkerNumber(record, ['tasksCompleted', 'tasks_completed', 'completed_tasks']),
            tasksFailed: readWorkerNumber(record, ['tasksFailed', 'tasks_failed', 'failed_tasks']),
        };
    })
        .filter((row) => Boolean(row));
}
function mergeDirectorWorkers(realtimeWorkers, backendWorkers) {
    const merged = new Map();
    for (const worker of backendWorkers) {
        if (worker?.id) {
            merged.set(worker.id, worker);
        }
    }
    for (const worker of realtimeWorkers) {
        if (worker?.id) {
            merged.set(worker.id, {
                ...merged.get(worker.id),
                ...worker,
            });
        }
    }
    return Array.from(merged.values()).sort((left, right) => left.id.localeCompare(right.id));
}
function taskTitle(task) {
    return readString(task, ['title', 'subject', 'goal', 'summary']) || String(task.id || '未命名任务');
}
function taskObjective(task) {
    return readString(task, ['goal', 'description', 'summary', 'subject', 'title']) || taskTitle(task);
}
function taskHandoffId(task) {
    return readString(task, ['pm_task_id', 'pmTaskId', 'task_id', 'taskId', 'id']) || String(task.id || '').trim();
}
function canonicalTaskMatchId(value) {
    const text = String(value || '').trim();
    if (!text)
        return '';
    const normalized = text.toLowerCase();
    const numericAlias = normalized.match(/^(?:task|pm-task|pm)[-_]?(\d+)$/);
    return numericAlias ? numericAlias[1] : normalized;
}
function taskHasBlueprintEvidence(task) {
    return Boolean(readString(task, ['blueprint_id', 'blueprintId', 'blueprint_path', 'runtime_blueprint_path']));
}
function taskStatus(task) {
    const status = normalizeToken(task.status || task.state);
    const direct = task;
    if (direct.done || direct.completed || ['completed', 'done', 'success', 'passed'].includes(status))
        return 'completed';
    if (['failed', 'error'].includes(status))
        return 'failed';
    if (['blocked', 'cancelled', 'canceled'].includes(status))
        return 'blocked';
    if (['running', 'in_progress', 'claimed', 'pending_exec'].includes(status))
        return 'running';
    return 'unclaimed';
}
function buildBlueprintEvidence(tasks) {
    return tasks
        .map((task) => {
        const blueprintId = readString(task, ['blueprint_id', 'blueprintId']);
        const blueprintPath = readString(task, ['blueprint_path', 'runtime_blueprint_path']);
        const summary = readString(task, ['blueprint_summary', 'summary', 'goal']);
        if (!blueprintId && !blueprintPath)
            return null;
        return {
            taskId: taskHandoffId(task),
            taskTitle: taskTitle(task),
            blueprintId,
            blueprintPath,
            source: blueprintPath
                ? 'runtime_blueprint_path'
                : blueprintId
                    ? 'blueprint_id'
                    : 'task_contract',
            summary,
            targetFiles: readTaskStringList(task, ['target_files', 'scope_paths', 'files', 'blueprint_files']),
        };
    })
        .filter((item) => Boolean(item));
}
function readRecordString(record, keys) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'string' && value.trim())
            return value.trim();
        if (typeof value === 'number' && Number.isFinite(value))
            return String(value);
    }
    return '';
}
function blueprintTaskIdFromGeneratedId(value) {
    const text = String(value || '').trim();
    if (!text)
        return '';
    const match = text.match(/^ce_((?:task|pm)[-_]?\d+)(?:_\d{8,}.*)?$/i);
    return match?.[1] || '';
}
function runtimeBlueprintTaskId(row) {
    const record = row;
    const raw = row.raw && typeof row.raw === 'object' ? row.raw : {};
    const nestedBlueprint = raw.blueprint && typeof raw.blueprint === 'object'
        ? raw.blueprint
        : {};
    const keys = ['task_id', 'pm_task_id', 'taskId', 'pmTaskId'];
    return (readRecordString(record, keys)
        || readRecordString(raw, keys)
        || readRecordString(nestedBlueprint, keys)
        || blueprintTaskIdFromGeneratedId(record.blueprint_id));
}
function buildRuntimeBlueprintEvidence(rows) {
    return rows
        .filter((row) => row && typeof row === 'object' && String(row.blueprint_id || '').trim())
        .map((row) => {
        const blueprintId = String(row.blueprint_id).trim();
        return {
            taskId: runtimeBlueprintTaskId(row) || blueprintId,
            taskTitle: String(row.title || blueprintId).trim(),
            blueprintId,
            blueprintPath: '',
            source: String(row.source || 'runtime/blueprints').trim(),
            summary: String(row.summary || '').trim(),
            targetFiles: Array.isArray(row.target_files) ? row.target_files.map((item) => String(item).trim()).filter(Boolean) : [],
        };
    });
}
function roleStatus(engineStatus, role) {
    const roles = engineStatus?.roles;
    const rolePayload = roles?.[role] || roles?.[role.toLowerCase()];
    return String(rolePayload?.status || '').trim();
}
function readRecordNumber(record, keys) {
    if (!record)
        return undefined;
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'number' && Number.isFinite(value))
            return value;
        if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value)))
            return Number(value);
    }
    return undefined;
}
function readKernelEventString(event, keys) {
    if (!event)
        return '';
    for (const key of keys) {
        const value = event[key];
        if (typeof value === 'string' && value.trim())
            return value.trim();
        if (typeof value === 'number' && Number.isFinite(value))
            return String(value);
    }
    return '';
}
function formatChiefKernelEvent(event) {
    if (!event)
        return 'events=0';
    const eventType = readKernelEventString(event, ['event_type', 'type']) || 'event';
    const model = readKernelEventString(event, ['model', 'model_name']);
    const tokens = readRecordNumber(event, ['tokens', 'token_count', 'total_tokens']);
    return [
        eventType,
        model,
        typeof tokens === 'number' ? `${tokens} tokens` : '',
    ].filter(Boolean).join(' · ');
}
function formatChiefCacheStats(stats) {
    if (!stats)
        return 'unavailable';
    const hits = readRecordNumber(stats, ['hits']) ?? 0;
    const misses = readRecordNumber(stats, ['misses']) ?? 0;
    const size = readRecordNumber(stats, ['size']) ?? 0;
    const hitRate = readRecordNumber(stats, ['hit_rate']);
    return `hits=${hits} · misses=${misses} · size=${size}${typeof hitRate === 'number' ? ` · hit=${hitRate}%` : ''}`;
}
function formatChiefTokenBudget(stats) {
    if (!stats)
        return 'unavailable';
    const total = readRecordNumber(stats, ['total', 'total_budget']);
    const available = readRecordNumber(stats, ['available_conversation', 'remaining']);
    const used = readRecordNumber(stats, ['used_tokens']);
    return [
        typeof total === 'number' ? `total=${total}` : '',
        typeof available === 'number' ? `available=${available}` : '',
        typeof used === 'number' ? `used=${used}` : '',
    ].filter(Boolean).join(' · ') || 'stats ready';
}
function diagnosticsTone(diagnostics, error) {
    if (error)
        return 'error';
    if (!diagnostics)
        return 'checking';
    if (diagnostics.can_handoff === false || (diagnostics.handoff_blockers || []).length > 0)
        return 'degraded';
    return diagnostics.ok ? 'ready' : 'degraded';
}
const CHIEF_HANDOFF_BLOCKER_LABELS = {
    workspace_unavailable: '工作区不可用',
    blueprint_store_unreadable: '蓝图存储不可读',
    blueprint_task_plan_unavailable: 'PM 任务计划不可读',
    blueprint_task_plan_empty: 'PM 任务计划为空',
    blueprint_payload_invalid: '存在无效蓝图 payload',
    blueprint_coverage_incomplete: 'PM 任务蓝图覆盖不完整',
    blueprint_handoff_not_ready: '没有可交接的 Chief Engineer 蓝图',
};
const CHIEF_HANDOFF_HARD_ISSUES = new Set(Object.keys(CHIEF_HANDOFF_BLOCKER_LABELS));
const STALE_BLUEPRINT_COVERAGE_ISSUES = new Set([
    'blueprint_coverage_incomplete',
    'blueprint_handoff_not_ready',
    'blueprint_task_plan_unavailable',
    'blueprint_task_plan_empty',
]);
const CHIEF_GENERATE_BLOCKER_LABELS = {
    workspace_unavailable: '工作区不可用',
    llm_not_ready: 'Chief Engineer LLM 未就绪',
};
const CHIEF_GENERATE_HARD_ISSUES = new Set(Object.keys(CHIEF_GENERATE_BLOCKER_LABELS));
function chiefHandoffBlockers(diagnostics) {
    if (!diagnostics) {
        return [];
    }
    if (Array.isArray(diagnostics.handoff_blockers) && diagnostics.handoff_blockers.length > 0) {
        return diagnostics.handoff_blockers
            .map((issue) => String(issue || '').trim())
            .filter((issue) => issue.length > 0);
    }
    const hasExplicitHandoffSignal = typeof diagnostics.can_handoff === 'boolean' || Array.isArray(diagnostics.handoff_blockers);
    if (hasExplicitHandoffSignal && diagnostics.can_handoff !== false) {
        return [];
    }
    const issueBlockers = (diagnostics.issues || []).filter((issue) => CHIEF_HANDOFF_HARD_ISSUES.has(issue));
    if (issueBlockers.length > 0) {
        return issueBlockers;
    }
    if (!hasExplicitHandoffSignal && !diagnostics.blueprints.director_handoff_ready && diagnostics.blueprints.planned_tasks > 0) {
        return ['blueprint_coverage_incomplete'];
    }
    return [];
}
function chiefGenerateBlockers(diagnostics) {
    if (!diagnostics) {
        return [];
    }
    if (Array.isArray(diagnostics.generate_blockers) && diagnostics.generate_blockers.length > 0) {
        return diagnostics.generate_blockers
            .map((issue) => String(issue || '').trim())
            .filter((issue) => issue.length > 0);
    }
    const hasExplicitGenerateSignal = typeof diagnostics.can_generate === 'boolean' || Array.isArray(diagnostics.generate_blockers);
    if (hasExplicitGenerateSignal && diagnostics.can_generate !== false) {
        return [];
    }
    const issueBlockers = (diagnostics.issues || []).filter((issue) => CHIEF_GENERATE_HARD_ISSUES.has(issue));
    if (issueBlockers.length > 0) {
        return issueBlockers;
    }
    if (!diagnostics.workspace.ok) {
        return ['workspace_unavailable'];
    }
    if (diagnostics.llm && diagnostics.llm.ok === false) {
        return ['llm_not_ready'];
    }
    return [];
}
function formatChiefGenerateBlockReason(diagnostics) {
    const blockers = chiefGenerateBlockers(diagnostics);
    if (blockers.length === 0) {
        return '';
    }
    const primary = CHIEF_GENERATE_BLOCKER_LABELS[blockers[0]] || blockers[0].replace(/_/g, ' ');
    const extraCount = blockers.length - 1;
    return `Chief Engineer 蓝图生成前置检查未通过：${primary}${extraCount > 0 ? `，另有 ${extraCount} 项阻断` : ''}`;
}
function formatChiefHandoffBlockReason(diagnostics) {
    const blockers = chiefHandoffBlockers(diagnostics);
    if (blockers.length === 0) {
        return '';
    }
    const missingTaskIds = diagnostics?.blueprints.missing_task_ids || [];
    const planned = diagnostics?.blueprints.planned_tasks ?? 0;
    const covered = diagnostics?.blueprints.covered_tasks ?? 0;
    if (blockers.includes('blueprint_coverage_incomplete')) {
        const missingCount = missingTaskIds.length || Math.max(0, planned - covered);
        return `诊断显示 ${missingCount || 1} 个 PM 任务缺少蓝图证据，不能启动 Director`;
    }
    if (blockers.includes('blueprint_task_plan_unavailable')) {
        return 'Chief Engineer 缺少可审计的 PM 任务计划，不能启动 Director';
    }
    if (blockers.includes('blueprint_task_plan_empty')) {
        return 'PM 任务计划为空，Chief Engineer 没有可交接的任务';
    }
    const primary = CHIEF_HANDOFF_BLOCKER_LABELS[blockers[0]] || blockers[0].replace(/_/g, ' ');
    const extraCount = blockers.length - 1;
    return `Chief Engineer 交接诊断未通过：${primary}${extraCount > 0 ? `，另有 ${extraCount} 项阻断` : ''}`;
}
function blueprintSummaryFromResult(result) {
    const blueprintId = String(result.blueprint_id || '').trim();
    if (!blueprintId)
        return null;
    const raw = result.blueprint && typeof result.blueprint === 'object' ? result.blueprint : {};
    const rawRecord = raw;
    return {
        blueprint_id: blueprintId,
        title: String(rawRecord.title || result.task_id || blueprintId).trim(),
        summary: String(result.summary || rawRecord.summary || '').trim(),
        status: String(result.status || rawRecord.status || '').trim() || null,
        source: String(result.source || 'runtime/blueprints').trim(),
        target_files: readStringList(rawRecord.target_files),
        updated_at: typeof rawRecord.updated_at === 'string' && rawRecord.updated_at.trim()
            ? rawRecord.updated_at.trim()
            : null,
        raw: rawRecord,
    };
}
function blueprintPayloadFromTask(task) {
    const taskId = taskHandoffId(task);
    const acceptanceCriteria = readTaskStringList(task, ['acceptance_criteria', 'acceptance']);
    const executionChecklist = readTaskStringList(task, ['execution_checklist', 'steps']);
    const targetFiles = readTaskStringList(task, ['target_files', 'scope_paths', 'files']);
    const scopePaths = readTaskStringList(task, ['scope_paths', 'scope']);
    const dependencies = readTaskStringList(task, ['dependencies', 'depends_on', 'blocked_by']);
    const constraints = readTaskRecord(task, ['constraints']);
    const qaContract = readTaskRecord(task, ['qa_contract']);
    const taskRecord = task;
    return {
        task_id: taskId,
        objective: taskObjective(task),
        constraints,
        context: {
            source: 'chief_engineer_desktop',
            task_id: taskId,
            source_pm_task_id: taskId,
            task_title: taskTitle(task),
            goal: readString(task, ['goal']),
            summary: readString(task, ['summary']),
            acceptance_criteria: acceptanceCriteria,
            acceptance: acceptanceCriteria,
            execution_checklist: executionChecklist,
            steps: executionChecklist,
            target_files: targetFiles,
            scope_paths: scopePaths,
            dependencies,
            qa_contract: qaContract,
            task: {
                ...taskRecord,
                metadata: metadataOf(task),
            },
        },
    };
}
export function ChiefEngineerWorkspace({ workspace, tasks, workers, pmState, engineStatus, directorRunning, isStartingDirector, isStoppingDirector = false, directorStartBlockedReason = '', onBackToMain, onEnterDirectorWorkspace, onToggleDirector, onOpenSettings, executionLogs = [], llmStreamEvents = [], processStreamEvents = [], currentPhase = 'idle', }) {
    const [runtimeBlueprints, setRuntimeBlueprints] = useState([]);
    const [blueprintApiError, setBlueprintApiError] = useState('');
    const [diagnostics, setDiagnostics] = useState(null);
    const [diagnosticsError, setDiagnosticsError] = useState('');
    const [chiefCapabilities, setChiefCapabilities] = useState([]);
    const [chiefCapabilitiesError, setChiefCapabilitiesError] = useState('');
    const [chiefLLMEvents, setChiefLLMEvents] = useState(null);
    const [chiefLLMEventsError, setChiefLLMEventsError] = useState('');
    const [chiefKernelCacheStats, setChiefKernelCacheStats] = useState(null);
    const [chiefKernelCacheError, setChiefKernelCacheError] = useState('');
    const [chiefKernelCacheClearStatus, setChiefKernelCacheClearStatus] = useState('');
    const [chiefKernelCacheClearing, setChiefKernelCacheClearing] = useState(false);
    const [chiefKernelTokenBudgetStats, setChiefKernelTokenBudgetStats] = useState(null);
    const [chiefKernelTokenBudgetError, setChiefKernelTokenBudgetError] = useState('');
    const [chiefBackendEvidenceLoading, setChiefBackendEvidenceLoading] = useState(false);
    const [selectedBlueprintId, setSelectedBlueprintId] = useState('');
    const [blueprintDetail, setBlueprintDetail] = useState(null);
    const [blueprintDetailError, setBlueprintDetailError] = useState('');
    const [blueprintDetailLoading, setBlueprintDetailLoading] = useState(false);
    const [deletingBlueprintId, setDeletingBlueprintId] = useState('');
    const [blueprintDeleteError, setBlueprintDeleteError] = useState('');
    const [blueprintDeleteEvidence, setBlueprintDeleteEvidence] = useState('');
    const [generatingTaskId, setGeneratingTaskId] = useState('');
    const [generateError, setGenerateError] = useState('');
    const [bulkGeneratingBlueprints, setBulkGeneratingBlueprints] = useState(false);
    const [bulkGenerateError, setBulkGenerateError] = useState('');
    const [bulkGenerateEvidence, setBulkGenerateEvidence] = useState('');
    const [blueprintStatusChecks, setBlueprintStatusChecks] = useState({});
    const [backendDirectorWorkers, setBackendDirectorWorkers] = useState([]);
    const [directorWorkerApiError, setDirectorWorkerApiError] = useState('');
    const [directorWorkerLoading, setDirectorWorkerLoading] = useState(false);
    const [directorToggleStatusEvidence, setDirectorToggleStatusEvidence] = useState({
        triggered: false,
        loading: false,
        message: null,
        error: null,
    });
    const [activeView, setActiveView] = useState('control');
    const [showAIDialogue, setShowAIDialogue] = useState(true);
    useEffect(() => {
        if (!workspace) {
            setRuntimeBlueprints([]);
            setBlueprintApiError('');
            setDiagnostics(null);
            setDiagnosticsError('');
            return;
        }
        let cancelled = false;
        const loadChiefEngineerState = async () => {
            const [blueprintResult, diagnosticsResult] = await Promise.all([
                listChiefEngineerBlueprints(workspace),
                getChiefEngineerDiagnostics(workspace),
            ]);
            if (cancelled) {
                return;
            }
            if (blueprintResult.ok && blueprintResult.data) {
                setRuntimeBlueprints(Array.isArray(blueprintResult.data.blueprints) ? blueprintResult.data.blueprints : []);
                setBlueprintApiError('');
            }
            else {
                setRuntimeBlueprints([]);
                setBlueprintApiError(blueprintResult.error || '蓝图 API 暂不可用');
            }
            if (diagnosticsResult.ok && diagnosticsResult.data) {
                setDiagnostics(diagnosticsResult.data);
                setDiagnosticsError('');
                return;
            }
            setDiagnostics(null);
            setDiagnosticsError(diagnosticsResult.error || '诊断 API 暂不可用');
        };
        void loadChiefEngineerState();
        return () => {
            cancelled = true;
        };
    }, [workspace]);
    useEffect(() => {
        if (!workspace) {
            setChiefCapabilities([]);
            setChiefCapabilitiesError('');
            setChiefLLMEvents(null);
            setChiefLLMEventsError('');
            setChiefKernelCacheStats(null);
            setChiefKernelCacheError('');
            setChiefKernelCacheClearStatus('');
            setChiefKernelTokenBudgetStats(null);
            setChiefKernelTokenBudgetError('');
            setChiefBackendEvidenceLoading(false);
            return;
        }
        let cancelled = false;
        const loadChiefBackendEvidence = async () => {
            setChiefBackendEvidenceLoading(true);
            try {
                const [capabilityResult, llmResult, cacheResult, tokenBudgetResult] = await Promise.all([
                    getRoleCapabilities('chief_engineer', 'electron_workbench'),
                    getRoleKernelLLMEvents('chief_engineer', { limit: 5, workspace }),
                    getRoleKernelCacheStats('chief_engineer'),
                    getRoleKernelTokenBudgetStats('chief_engineer'),
                ]);
                if (cancelled) {
                    return;
                }
                if (capabilityResult.ok && capabilityResult.data) {
                    setChiefCapabilities(resolveRoleCapabilities(capabilityResult.data, 'electron_workbench').sort());
                    setChiefCapabilitiesError('');
                }
                else {
                    setChiefCapabilities([]);
                    setChiefCapabilitiesError(capabilityResult.error || 'Chief Engineer capabilities unavailable');
                }
                if (llmResult.ok && llmResult.data) {
                    setChiefLLMEvents({
                        ...llmResult.data,
                        events: Array.isArray(llmResult.data.events) ? llmResult.data.events : [],
                    });
                    setChiefLLMEventsError('');
                }
                else {
                    setChiefLLMEvents(null);
                    setChiefLLMEventsError(llmResult.error || 'Chief Engineer LLM events unavailable');
                }
                if (cacheResult.ok && cacheResult.data) {
                    setChiefKernelCacheStats(cacheResult.data);
                    setChiefKernelCacheError('');
                }
                else {
                    setChiefKernelCacheStats(null);
                    setChiefKernelCacheError(cacheResult.error || 'Chief Engineer cache stats unavailable');
                }
                if (tokenBudgetResult.ok && tokenBudgetResult.data) {
                    setChiefKernelTokenBudgetStats(tokenBudgetResult.data);
                    setChiefKernelTokenBudgetError('');
                }
                else {
                    setChiefKernelTokenBudgetStats(null);
                    setChiefKernelTokenBudgetError(tokenBudgetResult.error || 'Chief Engineer token budget unavailable');
                }
            }
            catch (err) {
                if (!cancelled) {
                    const message = err instanceof Error ? err.message : 'Chief Engineer backend evidence unavailable';
                    setChiefCapabilities([]);
                    setChiefCapabilitiesError(message);
                    setChiefLLMEvents(null);
                    setChiefLLMEventsError(message);
                    setChiefKernelCacheStats(null);
                    setChiefKernelCacheError(message);
                    setChiefKernelTokenBudgetStats(null);
                    setChiefKernelTokenBudgetError(message);
                }
            }
            finally {
                if (!cancelled) {
                    setChiefBackendEvidenceLoading(false);
                }
            }
        };
        void loadChiefBackendEvidence();
        return () => {
            cancelled = true;
        };
    }, [workspace]);
    useEffect(() => {
        if (!workspace) {
            setBackendDirectorWorkers([]);
            setDirectorWorkerApiError('');
            setDirectorWorkerLoading(false);
            return;
        }
        let cancelled = false;
        const syncDirectorWorkers = async () => {
            setDirectorWorkerLoading(true);
            try {
                const result = await listDirectorWorkers(workspace);
                if (cancelled) {
                    return;
                }
                if (result.ok && Array.isArray(result.data)) {
                    setBackendDirectorWorkers(normalizeDirectorWorkerRows(result.data));
                    setDirectorWorkerApiError('');
                }
                else {
                    setDirectorWorkerApiError(result.error || 'Director worker backend unavailable');
                }
            }
            catch (err) {
                if (!cancelled) {
                    setDirectorWorkerApiError(err instanceof Error ? err.message : 'Director worker backend unavailable');
                }
            }
            finally {
                if (!cancelled) {
                    setDirectorWorkerLoading(false);
                }
            }
        };
        void syncDirectorWorkers();
        return () => {
            cancelled = true;
        };
    }, [workspace, directorRunning]);
    const directorTaskEvidenceRows = useMemo(() => tasks, [tasks]);
    const taskBlueprintEvidence = useMemo(() => buildBlueprintEvidence(directorTaskEvidenceRows), [directorTaskEvidenceRows]);
    const runtimeBlueprintEvidence = useMemo(() => buildRuntimeBlueprintEvidence(runtimeBlueprints), [runtimeBlueprints]);
    const blueprintEvidence = useMemo(() => {
        const byKey = new Map();
        for (const item of runtimeBlueprintEvidence) {
            byKey.set(item.blueprintId || item.blueprintPath || item.taskId, item);
        }
        for (const item of taskBlueprintEvidence) {
            const key = item.blueprintId || item.blueprintPath || item.taskId;
            if (!byKey.has(key)) {
                byKey.set(key, item);
            }
        }
        return Array.from(byKey.values());
    }, [runtimeBlueprintEvidence, taskBlueprintEvidence]);
    const stats = useMemo(() => {
        const rows = directorTaskEvidenceRows.map(taskStatus);
        return {
            total: rows.length,
            unclaimed: rows.filter((item) => item === 'unclaimed').length,
            running: rows.filter((item) => item === 'running').length,
            blocked: rows.filter((item) => item === 'blocked').length,
            failed: rows.filter((item) => item === 'failed').length,
            completed: rows.filter((item) => item === 'completed').length,
        };
    }, [directorTaskEvidenceRows]);
    const chiefStatus = roleStatus(engineStatus, 'ChiefEngineer') || roleStatus(engineStatus, 'chief_engineer') || 'idle';
    const directorRows = useMemo(() => mergeDirectorWorkers(workers.filter((worker) => worker && typeof worker === 'object'), backendDirectorWorkers), [workers, backendDirectorWorkers]);
    const lastDirectorStatus = String(pmState?.last_director_status || '').trim();
    const diagnosticsHasInvalidBlueprintPayloads = Boolean((diagnostics?.blueprints.invalid_payloads ?? 0) > 0
        || (diagnostics?.issues ?? []).includes('blueprint_payload_invalid'));
    const diagnosticMissingBlueprintTaskIds = useMemo(() => new Set((diagnostics?.blueprints.missing_task_ids ?? []).map(canonicalTaskMatchId).filter(Boolean)), [diagnostics]);
    const blueprintEvidenceTaskIds = useMemo(() => new Set(blueprintEvidence
        .map((item) => canonicalTaskMatchId(item.taskId))
        .filter(Boolean)), [blueprintEvidence]);
    const taskEvidenceTaskIds = useMemo(() => new Set(directorTaskEvidenceRows
        .map((task) => canonicalTaskMatchId(taskHandoffId(task)))
        .filter(Boolean)), [directorTaskEvidenceRows]);
    const blueprintCoveredTaskEvidenceCount = useMemo(() => Array.from(taskEvidenceTaskIds)
        .filter((taskId) => blueprintEvidenceTaskIds.has(taskId) && !diagnosticMissingBlueprintTaskIds.has(taskId))
        .length, [blueprintEvidenceTaskIds, diagnosticMissingBlueprintTaskIds, taskEvidenceTaskIds]);
    const missingBlueprintHandoffTasks = useMemo(() => {
        const seen = new Set();
        return directorTaskEvidenceRows
            .filter((task) => {
            const taskId = taskHandoffId(task);
            const canonicalTaskId = canonicalTaskMatchId(taskId);
            if (!taskId || !canonicalTaskId || seen.has(canonicalTaskId))
                return false;
            if (taskStatus(task) === 'completed') {
                return false;
            }
            const diagnosticsRequiresRegeneration = diagnosticMissingBlueprintTaskIds.has(canonicalTaskId);
            if ((!diagnosticsRequiresRegeneration || !diagnosticsHasInvalidBlueprintPayloads)
                && blueprintEvidenceTaskIds.has(canonicalTaskId)) {
                return false;
            }
            if (!diagnosticsRequiresRegeneration
                && taskHasBlueprintEvidence(task)) {
                return false;
            }
            seen.add(canonicalTaskId);
            return true;
        });
    }, [
        blueprintEvidenceTaskIds,
        diagnosticMissingBlueprintTaskIds,
        diagnosticsHasInvalidBlueprintPayloads,
        directorTaskEvidenceRows,
    ]);
    const blueprintCandidateTasks = useMemo(() => missingBlueprintHandoffTasks.slice(0, 4), [missingBlueprintHandoffTasks]);
    const workspaceDiagnosticTone = !diagnostics ? 'checking' : diagnostics.workspace.ok ? 'ready' : 'error';
    const blueprintDiagnosticTone = !diagnostics
        ? 'checking'
        : diagnostics.blueprints.ok
            ? diagnostics.blueprints.status === 'empty'
                ? 'degraded'
                : 'ready'
            : 'error';
    const blueprintCoveragePlanned = diagnostics?.blueprints.planned_tasks ?? 0;
    const blueprintCoverageCovered = diagnostics?.blueprints.covered_tasks ?? 0;
    const missingBlueprintTaskIds = diagnostics?.blueprints.missing_task_ids ?? [];
    const effectiveMissingBlueprintTaskIds = missingBlueprintTaskIds.filter((taskId) => {
        const canonicalTaskId = canonicalTaskMatchId(taskId);
        if (!canonicalTaskId) {
            return Boolean(String(taskId || '').trim());
        }
        return diagnosticsHasInvalidBlueprintPayloads || !blueprintEvidenceTaskIds.has(canonicalTaskId);
    });
    const effectiveBlueprintCoveragePlanned = Math.max(blueprintCoveragePlanned, taskEvidenceTaskIds.size);
    const diagnosticsCoveredByMissingList = blueprintCoveragePlanned > 0
        ? blueprintCoveragePlanned - effectiveMissingBlueprintTaskIds.length
        : 0;
    const effectiveBlueprintCoverageCovered = effectiveBlueprintCoveragePlanned > 0
        ? Math.min(effectiveBlueprintCoveragePlanned, Math.max(blueprintCoverageCovered, diagnosticsCoveredByMissingList, blueprintCoveredTaskEvidenceCount))
        : blueprintCoverageCovered;
    const effectiveBlueprintCoverageComplete = Boolean(effectiveBlueprintCoveragePlanned > 0
        && effectiveBlueprintCoverageCovered >= effectiveBlueprintCoveragePlanned);
    const effectiveDirectorHandoffReady = Boolean(diagnostics?.blueprints.director_handoff_ready || effectiveBlueprintCoverageComplete);
    const shouldSuppressStaleBlueprintIssue = (issue) => {
        if (!effectiveBlueprintCoverageComplete || !STALE_BLUEPRINT_COVERAGE_ISSUES.has(issue)) {
            return false;
        }
        if (diagnosticsHasInvalidBlueprintPayloads
            && (issue === 'blueprint_coverage_incomplete' || issue === 'blueprint_handoff_not_ready')) {
            return false;
        }
        return true;
    };
    const diagnosticsHandoffBlockers = chiefHandoffBlockers(diagnostics).filter((issue) => (!shouldSuppressStaleBlueprintIssue(issue)));
    const diagnosticsHandoffBlocked = !directorRunning && diagnosticsHandoffBlockers.length > 0;
    const diagnosticsHandoffBlockReason = diagnosticsHandoffBlockers.length > 0
        ? formatChiefHandoffBlockReason(diagnostics)
        : '';
    const effectiveDiagnosticIssues = (diagnostics?.issues ?? []).filter((issue) => (!shouldSuppressStaleBlueprintIssue(issue)));
    const diagnosticsState = diagnosticsTone(diagnostics
        ? {
            ...diagnostics,
            can_handoff: effectiveDirectorHandoffReady ? true : diagnostics.can_handoff,
            handoff_blockers: diagnosticsHandoffBlockers,
            issues: effectiveDiagnosticIssues,
        }
        : null, diagnosticsError);
    const handoffDiagnosticTone = !diagnostics
        ? 'checking'
        : effectiveDirectorHandoffReady
            ? 'ready'
            : 'degraded';
    const diagnosticsGenerateBlockers = chiefGenerateBlockers(diagnostics);
    const diagnosticsGenerateBlocked = diagnosticsGenerateBlockers.length > 0;
    const diagnosticsGenerateBlockReason = formatChiefGenerateBlockReason(diagnostics);
    const llmDiagnosticTone = !diagnostics
        ? 'checking'
        : diagnostics.llm?.ok
            ? 'ready'
            : 'error';
    const llmDiagnosticValue = !diagnostics
        ? 'checking'
        : diagnostics.llm
            ? [
                diagnostics.llm.state || (diagnostics.llm.ok ? 'ready' : 'blocked'),
                diagnostics.llm.model || diagnostics.llm.provider_id || '',
            ].filter(Boolean).join(' · ')
            : 'missing';
    const externalDirectorStartBlocked = Boolean(!directorRunning && directorStartBlockedReason.trim());
    const startDirectorBlocked = !directorRunning
        && (externalDirectorStartBlocked || missingBlueprintHandoffTasks.length > 0 || diagnosticsHandoffBlocked);
    const startDirectorBlockedTitle = externalDirectorStartBlocked
        ? directorStartBlockedReason
        : missingBlueprintHandoffTasks.length > 0
            ? '缺少 Chief Engineer 蓝图证据，不能从 CE 页直接启动 Director'
            : diagnosticsHandoffBlocked
                ? diagnosticsHandoffBlockReason
                : undefined;
    const directorStarting = Boolean(isStartingDirector);
    const directorStopping = Boolean(isStoppingDirector);
    const directorControlBusyReason = directorStarting
        ? 'Director 正在启动，请等待状态回传。'
        : directorStopping
            ? 'Director 正在停止，请等待状态回传。'
            : directorToggleStatusEvidence.loading
                ? 'Director 命令提交中，请等待 runtime.v2 回传。'
                : '';
    const directorPrimaryActionLabel = directorStarting
        ? '启动中'
        : directorStopping
            ? '停止中'
            : directorToggleStatusEvidence.loading
                ? '确认中'
                : directorRunning
                    ? '停止 Director'
                    : '启动 Director';
    const blueprintCoverageValue = !diagnostics
        ? 'checking'
        : effectiveBlueprintCoveragePlanned > 0
            ? `${effectiveBlueprintCoverageCovered}/${effectiveBlueprintCoveragePlanned}`
            : diagnostics.blueprints.plan_status && diagnostics.blueprints.plan_status !== 'ready'
                ? diagnostics.blueprints.plan_status
                : 'no PM plan';
    const blueprintCoverageTone = !diagnostics
        ? 'checking'
        : effectiveBlueprintCoveragePlanned > 0
            && effectiveBlueprintCoverageCovered === effectiveBlueprintCoveragePlanned
            ? 'ready'
            : 'degraded';
    const handoffDiagnosticValue = !diagnostics
        ? 'checking'
        : effectiveDirectorHandoffReady
            ? 'ready'
            : effectiveMissingBlueprintTaskIds.length > 0
                ? `missing ${effectiveMissingBlueprintTaskIds.length}`
                : diagnostics.blueprints.loadable > 0
                    ? 'partial'
                    : 'no blueprint';
    const missingBlueprintValue = !diagnostics
        ? 'checking'
        : effectiveMissingBlueprintTaskIds.length > 0
            ? `${effectiveMissingBlueprintTaskIds.slice(0, 3).join(', ')}${effectiveMissingBlueprintTaskIds.length > 3 ? '...' : ''}`
            : 'none';
    const refreshChiefEngineerDiagnostics = async () => {
        if (!workspace) {
            setDiagnostics(null);
            setDiagnosticsError('');
            return;
        }
        const diagnosticsResult = await getChiefEngineerDiagnostics(workspace);
        if (diagnosticsResult.ok && diagnosticsResult.data) {
            setDiagnostics(diagnosticsResult.data);
            setDiagnosticsError('');
            return;
        }
        setDiagnostics(null);
        setDiagnosticsError(diagnosticsResult.error || '诊断 API 暂不可用');
    };
    const loadBlueprintDetail = async (blueprintId) => {
        const token = String(blueprintId || '').trim();
        if (!token)
            return;
        setSelectedBlueprintId(token);
        setBlueprintDetail(null);
        setBlueprintDetailError('');
        setBlueprintDetailLoading(true);
        const result = await getChiefEngineerBlueprint(token, workspace);
        if (result.ok && result.data) {
            setBlueprintDetail(result.data);
        }
        else {
            setBlueprintDetailError(result.error || '蓝图详情 API 暂不可用');
        }
        setBlueprintDetailLoading(false);
    };
    const handleGenerateBlueprint = async (task) => {
        const taskId = taskHandoffId(task);
        if (!taskId)
            return;
        if (diagnosticsGenerateBlocked) {
            setGenerateError(diagnosticsGenerateBlockReason);
            return;
        }
        setGeneratingTaskId(taskId);
        setGenerateError('');
        setBulkGenerateError('');
        setBulkGenerateEvidence('');
        const result = await generateChiefEngineerBlueprint(blueprintPayloadFromTask(task), workspace);
        if (!result.ok || !result.data) {
            setGenerateError(result.error || '蓝图生成 API 暂不可用');
            setGeneratingTaskId('');
            return;
        }
        const summary = blueprintSummaryFromResult(result.data);
        if (summary) {
            setRuntimeBlueprints((current) => [
                summary,
                ...current.filter((item) => item.blueprint_id !== summary.blueprint_id),
            ]);
            setSelectedBlueprintId(summary.blueprint_id);
            setBlueprintDetail({
                blueprint_id: summary.blueprint_id,
                source: summary.source,
                blueprint: result.data.blueprint,
            });
            setBlueprintDetailError('');
        }
        await refreshChiefEngineerDiagnostics();
        setGeneratingTaskId('');
    };
    const handleGenerateAllBlueprints = async () => {
        if (missingBlueprintHandoffTasks.length === 0) {
            return;
        }
        if (diagnosticsGenerateBlocked) {
            setBulkGenerateError(diagnosticsGenerateBlockReason);
            return;
        }
        setBulkGeneratingBlueprints(true);
        setBulkGenerateError('');
        setBulkGenerateEvidence('');
        setGenerateError('');
        const result = await bulkGenerateChiefEngineerBlueprints({
            tasks: missingBlueprintHandoffTasks.map(blueprintPayloadFromTask),
            stop_on_error: false,
        }, workspace);
        if (!result.ok || !result.data) {
            setBulkGenerateError(result.error || '批量蓝图生成 API 暂不可用');
            setBulkGeneratingBlueprints(false);
            return;
        }
        const summaries = result.data.results
            .map(blueprintSummaryFromResult)
            .filter((item) => Boolean(item));
        if (summaries.length > 0) {
            setRuntimeBlueprints((current) => {
                const next = new Map();
                for (const item of summaries) {
                    next.set(item.blueprint_id, item);
                }
                for (const item of current) {
                    if (!next.has(item.blueprint_id)) {
                        next.set(item.blueprint_id, item);
                    }
                }
                return Array.from(next.values());
            });
            const first = summaries[0];
            setSelectedBlueprintId(first.blueprint_id);
            setBlueprintDetail({
                blueprint_id: first.blueprint_id,
                source: first.source,
                blueprint: first.raw,
            });
            setBlueprintDetailError('');
        }
        if (result.data.failed > 0) {
            const firstError = result.data.errors[0];
            setBulkGenerateError(firstError
                ? `${firstError.task_id}: ${firstError.message}`
                : `${result.data.failed} 个蓝图生成失败`);
        }
        setBulkGenerateEvidence(`${evidenceEndpoint('/v2/chief-engineer/blueprints/bulk', workspace)} · generated ${result.data.generated}/${result.data.total}`);
        await refreshChiefEngineerDiagnostics();
        setBulkGeneratingBlueprints(false);
    };
    const handleCheckBlueprintStatus = async (task) => {
        const taskId = taskHandoffId(task);
        if (!taskId)
            return;
        setBlueprintStatusChecks((current) => ({
            ...current,
            [taskId]: {
                loading: true,
                error: '',
                result: current[taskId]?.result ?? null,
            },
        }));
        const result = await getChiefEngineerBlueprintStatus(taskId, null, workspace);
        if (!result.ok || !result.data) {
            setBlueprintStatusChecks((current) => ({
                ...current,
                [taskId]: {
                    loading: false,
                    error: result.error || '蓝图状态 API 暂不可用',
                    result: null,
                },
            }));
            return;
        }
        const statusResult = result.data;
        setBlueprintStatusChecks((current) => ({
            ...current,
            [taskId]: {
                loading: false,
                error: '',
                result: statusResult,
            },
        }));
        const summary = blueprintSummaryFromResult(statusResult);
        if (summary) {
            setRuntimeBlueprints((current) => [
                summary,
                ...current.filter((item) => item.blueprint_id !== summary.blueprint_id),
            ]);
            setSelectedBlueprintId(summary.blueprint_id);
            setBlueprintDetail({
                blueprint_id: summary.blueprint_id,
                source: summary.source,
                blueprint: statusResult.blueprint,
            });
            setBlueprintDetailError('');
        }
        await refreshChiefEngineerDiagnostics();
    };
    const handleClearChiefKernelCache = async () => {
        setChiefKernelCacheClearing(true);
        setChiefKernelCacheClearStatus('');
        const result = await clearRoleKernelCache('chief_engineer');
        if (!result.ok) {
            setChiefKernelCacheClearStatus(result.error || 'Chief Engineer cache clear unavailable');
            setChiefKernelCacheClearing(false);
            return;
        }
        setChiefKernelCacheClearStatus(result.data?.message || 'Cache cleared');
        const statsResult = await getRoleKernelCacheStats('chief_engineer');
        if (statsResult.ok && statsResult.data) {
            setChiefKernelCacheStats(statsResult.data);
            setChiefKernelCacheError('');
        }
        else {
            setChiefKernelCacheStats(null);
            setChiefKernelCacheError(statsResult.error || 'Chief Engineer cache stats unavailable');
        }
        setChiefKernelCacheClearing(false);
    };
    const handleToggleDirector = async () => {
        if (directorControlBusyReason) {
            return;
        }
        if (!directorRunning && startDirectorBlockedTitle) {
            setDirectorToggleStatusEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: startDirectorBlockedTitle,
            });
            return;
        }
        setDirectorToggleStatusEvidence({
            triggered: true,
            loading: true,
            message: null,
            error: null,
        });
        try {
            const accepted = await Promise.resolve(onToggleDirector());
            setDirectorToggleStatusEvidence({
                triggered: true,
                loading: false,
                message: accepted === false ? '命令未被接受。' : DIRECTOR_COMMAND_ACCEPTED_MESSAGE,
                error: accepted === false ? 'Director command was not accepted' : null,
            });
        }
        catch (error) {
            setDirectorToggleStatusEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: error instanceof Error ? error.message : 'Director command unavailable',
            });
        }
    };
    const handleDeleteBlueprint = async (blueprintId) => {
        const token = String(blueprintId || '').trim();
        if (!token)
            return;
        setDeletingBlueprintId(token);
        setBlueprintDeleteError('');
        setBlueprintDeleteEvidence('');
        const result = await deleteChiefEngineerBlueprint(token, workspace);
        if (!result.ok || !result.data?.deleted) {
            setBlueprintDeleteError(result.error || '蓝图删除 API 暂不可用');
            setDeletingBlueprintId('');
            return;
        }
        setRuntimeBlueprints((current) => current.filter((item) => item.blueprint_id !== token));
        if (selectedBlueprintId === token) {
            setSelectedBlueprintId('');
            setBlueprintDetail(null);
            setBlueprintDetailError('');
        }
        setBlueprintDeleteEvidence(`${evidenceEndpoint(`/v2/chief-engineer/blueprints/${encodeURIComponent(token)}`, workspace)} · deleted`);
        await refreshChiefEngineerDiagnostics();
        setDeletingBlueprintId('');
    };
    const latestChiefLLMEvent = chiefLLMEvents?.events[0] ?? null;
    const chiefLLMEventCount = chiefLLMEvents?.count
        ?? readRecordNumber(chiefLLMEvents?.stats, ['total'])
        ?? chiefLLMEvents?.events.length
        ?? 0;
    const directorToggleBusy = Boolean(directorControlBusyReason);
    const chiefRuntimeActive = directorRunning || !['', 'idle', 'unknown', 'none'].includes(normalizeToken(currentPhase));
    return (_jsxs("div", { "data-testid": "chief-engineer-workspace", className: "soft-app-bg flex h-full flex-col overflow-hidden text-slate-100", children: [_jsxs("header", { className: "flex h-14 items-center justify-between border-b border-white/[0.06] bg-slate-950/80 px-4", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsxs(Button, { variant: "ghost", size: "sm", onClick: onBackToMain, "data-testid": "chief-engineer-workspace-back", className: "text-slate-400 hover:bg-white/5 hover:text-slate-100", children: [_jsx(ChevronLeft, { className: "mr-1 h-4 w-4" }), "\u8FD4\u56DE"] }), _jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: "soft-raised flex h-8 w-8 items-center justify-center rounded-lg text-slate-300", children: _jsx(Brain, { className: "h-4 w-4" }) }), _jsxs("div", { children: [_jsx("h1", { className: "text-sm font-semibold text-slate-100", children: "Chief Engineer" }), _jsx("p", { className: "text-[10px] uppercase tracking-wider text-slate-400/70", children: "Blueprint Control Room" })] })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs(Button, { variant: "ghost", size: "sm", onClick: () => setActiveView((current) => current === 'workbench' ? 'control' : 'workbench'), "data-testid": "chief-engineer-toggle-workbench", className: cn('text-slate-300 hover:bg-white/5 hover:text-white', activeView === 'workbench' && 'soft-raised text-slate-100'), children: [_jsx(GitBranch, { className: "mr-1.5 h-3.5 w-3.5" }), activeView === 'workbench' ? '控制室' : '工作台'] }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => setShowAIDialogue((current) => !current), "data-testid": "chief-engineer-toggle-dialogue", disabled: activeView === 'workbench', title: activeView === 'workbench' ? '工作台内置对话面板' : '切换对话面板', className: cn('text-slate-300 hover:bg-white/5 hover:text-white', showAIDialogue && 'soft-raised text-slate-100'), children: [_jsx(MessageSquare, { className: "mr-1.5 h-3.5 w-3.5" }), "\u5BF9\u8BDD"] }), _jsx(Button, { variant: "ghost", size: "icon", onClick: onOpenSettings, disabled: !onOpenSettings, "data-testid": "chief-engineer-open-settings", title: onOpenSettings ? '系统配置' : '系统配置需由主界面打开', className: "text-slate-300 hover:bg-white/5 hover:text-white", children: _jsx(Settings, { className: "h-4 w-4" }) }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => { void handleToggleDirector(); }, disabled: directorToggleBusy || startDirectorBlocked, title: directorControlBusyReason || startDirectorBlockedTitle, "data-testid": "chief-engineer-start-director", className: "soft-chip text-slate-200 hover:bg-white/[0.06]", children: [directorToggleBusy ? _jsx(Loader2, { className: "mr-1.5 h-3.5 w-3.5 animate-spin" }) : _jsx(Play, { className: "mr-1.5 h-3.5 w-3.5" }), directorPrimaryActionLabel] }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: onEnterDirectorWorkspace, "data-testid": "chief-engineer-enter-director", className: "text-slate-300 hover:bg-white/5 hover:text-white", children: ["Director \u770B\u677F", _jsx(ArrowRight, { className: "ml-1.5 h-3.5 w-3.5" })] })] })] }), _jsx("section", { className: "border-b border-white/[0.06] bg-slate-950/75 px-4 py-2 text-xs text-slate-300", "data-testid": "chief-engineer-backend-strip", children: _jsxs("details", { className: "soft-panel-subtle group rounded-lg px-3 py-2", children: [_jsxs("summary", { className: "flex min-w-0 cursor-pointer list-none items-center gap-3 [&::-webkit-details-marker]:hidden", children: [_jsxs("div", { className: "flex shrink-0 items-center gap-2 font-medium text-slate-200", children: [_jsx(ShieldCheck, { className: "h-3.5 w-3.5 text-slate-400" }), "Chief Engineer \u540E\u7AEF\u72B6\u6001"] }), _jsxs("div", { className: "flex min-w-0 flex-1 flex-wrap items-center gap-2", children: [_jsx("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: chiefBackendEvidenceLoading
                                                ? '能力读取中'
                                                : chiefCapabilitiesError
                                                    ? '能力异常'
                                                    : `能力 ${chiefCapabilities.length}` }), _jsxs("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: ["LLM events=", chiefBackendEvidenceLoading ? '...' : chiefLLMEventCount] }), _jsx("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: chiefBackendEvidenceLoading
                                                ? '缓存读取中'
                                                : chiefKernelCacheError
                                                    ? '缓存异常'
                                                    : formatChiefCacheStats(chiefKernelCacheStats) }), _jsx("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300", children: chiefBackendEvidenceLoading
                                                ? '预算读取中'
                                                : chiefKernelTokenBudgetError
                                                    ? '预算异常'
                                                    : formatChiefTokenBudget(chiefKernelTokenBudgetStats) })] }), _jsx("span", { className: "shrink-0 text-[10px] text-slate-500 group-open:hidden", children: "\u5C55\u5F00\u8BC1\u636E" }), _jsx("span", { className: "hidden shrink-0 text-[10px] text-slate-300 group-open:inline", children: "\u6536\u8D77\u8BC1\u636E" })] }), _jsxs("div", { className: "mt-2 grid gap-2 border-t border-white/10 pt-2 lg:grid-cols-2", children: [_jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2", children: [_jsx("span", { className: "shrink-0 font-medium text-slate-200", children: "Capabilities" }), _jsx(EvidenceEndpointBadge, { endpoint: "/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench", testId: "chief-engineer-capabilities-endpoint" }), chiefBackendEvidenceLoading ? (_jsx("span", { className: "text-slate-400", children: "\u8BFB\u53D6\u4E2D..." })) : chiefCapabilitiesError ? (_jsx("span", { className: "text-rose-300", children: chiefCapabilitiesError })) : (_jsx("span", { className: "truncate text-emerald-300", children: chiefCapabilities.length > 0 ? chiefCapabilities.slice(0, 5).join(', ') : 'none' }))] }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2", children: [_jsx("span", { className: "shrink-0 font-medium text-slate-200", children: "LLM events" }), _jsx(EvidenceEndpointBadge, { endpoint: evidenceEndpoint('/v2/chief-engineer/llm-events?limit=5', workspace), testId: "chief-engineer-llm-events-endpoint" }), chiefBackendEvidenceLoading ? (_jsx("span", { className: "text-slate-400", children: "\u8BFB\u53D6\u4E2D..." })) : chiefLLMEventsError ? (_jsx("span", { className: "text-rose-300", children: chiefLLMEventsError })) : (_jsxs("span", { className: "truncate text-emerald-300", children: ["events=", chiefLLMEventCount, latestChiefLLMEvent ? ` · ${formatChiefKernelEvent(latestChiefLLMEvent)}` : ''] }))] }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2", children: [_jsx("span", { className: "shrink-0 font-medium text-slate-200", children: "Kernel cache" }), _jsx(EvidenceEndpointBadge, { endpoint: "/v2/chief-engineer/cache-stats", testId: "chief-engineer-cache-endpoint" }), chiefBackendEvidenceLoading ? (_jsx("span", { className: "text-slate-400", children: "\u8BFB\u53D6\u4E2D..." })) : chiefKernelCacheError ? (_jsx("span", { className: "text-rose-300", children: chiefKernelCacheError })) : (_jsx("span", { className: "truncate text-emerald-300", children: formatChiefCacheStats(chiefKernelCacheStats) })), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void handleClearChiefKernelCache(); }, disabled: chiefKernelCacheClearing, "data-testid": "chief-engineer-kernel-cache-clear", className: "h-6 px-1.5 text-[10px] text-slate-300 hover:bg-white/[0.06] hover:text-slate-100", children: [chiefKernelCacheClearing ? _jsx(Loader2, { className: "mr-1 h-3 w-3 animate-spin" }) : null, "clear"] }), chiefKernelCacheClearStatus ? (_jsx("span", { "data-testid": "chief-engineer-kernel-cache-clear-result", "data-endpoint": "/v2/chief-engineer/cache-clear", title: "/v2/chief-engineer/cache-clear", className: "truncate text-slate-300", children: chiefKernelCacheClearStatus })) : null] }), _jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2", children: [_jsx("span", { className: "shrink-0 font-medium text-slate-200", children: "Token budget" }), _jsx(EvidenceEndpointBadge, { endpoint: "/v2/chief-engineer/token-budget-stats", testId: "chief-engineer-token-budget-endpoint" }), chiefBackendEvidenceLoading ? (_jsx("span", { className: "text-slate-400", children: "\u8BFB\u53D6\u4E2D..." })) : chiefKernelTokenBudgetError ? (_jsx("span", { className: "text-rose-300", children: chiefKernelTokenBudgetError })) : (_jsx("span", { className: "truncate text-emerald-300", children: formatChiefTokenBudget(chiefKernelTokenBudgetStats) }))] })] })] }) }), startDirectorBlockedTitle ? (_jsx("section", { className: "border-b border-amber-500/20 bg-amber-500/10 px-4 py-2 text-xs text-amber-100", "data-testid": "chief-engineer-director-start-gate", children: _jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx(AlertTriangle, { className: "h-3.5 w-3.5 shrink-0" }), _jsx("span", { className: "min-w-0 break-words", children: startDirectorBlockedTitle })] }) })) : null, directorToggleStatusEvidence.triggered ? (_jsx("section", { className: "border-b border-white/[0.06] bg-slate-950/70 px-4 py-2 text-xs text-slate-300", "data-testid": "chief-engineer-director-status-evidence", children: _jsx("div", { className: "flex flex-wrap items-center gap-x-5 gap-y-1", children: _jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("span", { className: "shrink-0 font-medium text-slate-200", children: "Director command" }), _jsx(EvidenceEndpointBadge, { endpoint: RUNTIME_PUSH_ENDPOINT, testId: "chief-engineer-director-status-endpoint" }), directorToggleStatusEvidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u6B63\u5728\u63D0\u4EA4\u547D\u4EE4..." })) : directorToggleStatusEvidence.error ? (_jsx("span", { className: "text-rose-300", children: directorToggleStatusEvidence.error })) : (_jsx("span", { className: "truncate text-emerald-300", children: directorToggleStatusEvidence.message || DIRECTOR_COMMAND_ACCEPTED_MESSAGE }))] }) }) })) : null, activeView === 'workbench' ? (_jsx("main", { className: "min-h-0 flex-1 overflow-hidden p-4", children: _jsx("div", { className: "h-full overflow-hidden rounded-lg border border-white/[0.06] bg-slate-950/45", children: _jsx(ChiefEngineerWorkbenchPanel, { workspace: workspace, taskCount: tasks.length, blueprintCount: blueprintEvidence.length, missingBlueprintCount: missingBlueprintHandoffTasks.length || effectiveMissingBlueprintTaskIds.length, directorRunning: directorRunning, hostKind: "electron_workbench", attachmentMode: "isolated" }) }) })) : (_jsxs("main", { className: cn('grid min-h-0 flex-1 gap-4 overflow-hidden p-4', showAIDialogue
                    ? 'grid-cols-[minmax(0,1fr)_340px_380px]'
                    : 'grid-cols-[minmax(0,1fr)_340px]'), children: [_jsxs("section", { className: "min-h-0 overflow-auto rounded-lg border border-white/10 bg-white/[0.035]", children: [_jsx("div", { className: "border-b border-white/10 px-4 py-3", children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-sm font-semibold text-slate-100", children: "\u65BD\u5DE5\u84DD\u56FE\u8BC1\u636E" }), _jsx("p", { className: "mt-1 text-xs text-slate-400", children: "\u4EC5\u5C55\u793A\u4ECE PM/CE/Director \u4EFB\u52A1\u5408\u540C\u4E2D\u8BFB\u53D6\u5230\u7684\u771F\u5B9E\u5B57\u6BB5\u3002" }), blueprintApiError ? (_jsxs("p", { className: "mt-1 text-[11px] text-amber-300", children: ["\u84DD\u56FE API \u6682\u4E0D\u53EF\u7528: ", blueprintApiError] })) : null] }), _jsx("span", { "data-testid": "chief-engineer-status", className: "soft-chip rounded-md px-2 py-1 text-[10px] uppercase tracking-wider text-slate-300", children: chiefStatus })] }) }), _jsxs("div", { className: "space-y-3 p-4", children: [blueprintEvidence.length === 0 ? (_jsxs("div", { "data-testid": "chief-engineer-blueprint-empty", className: "rounded-lg border border-amber-500/25 bg-amber-500/10 p-4 text-sm text-amber-100", children: [_jsxs("div", { className: "flex items-center gap-2 font-medium", children: [_jsx(AlertTriangle, { className: "h-4 w-4" }), "\u672A\u53D1\u73B0\u5DF2\u843D\u76D8\u7684 Chief Engineer \u84DD\u56FE\u8BC1\u636E"] }), _jsx("p", { className: "mt-2 text-xs leading-5 text-amber-100/75", children: "\u5F53\u524D\u4E0D\u4F1A\u4F2A\u9020\u84DD\u56FE\u5185\u5BB9\u3002\u9700\u8981 PM/CE \u94FE\u8DEF\u5199\u5165 `blueprint_id`\u3001`blueprint_path` \u6216 `runtime_blueprint_path` \u540E\uFF0C\u8FD9\u91CC\u624D\u5C55\u793A\u84DD\u56FE\u8BB0\u5F55\u3002" })] })) : (blueprintEvidence.map((item) => (_jsxs("article", { className: "soft-panel-subtle rounded-lg p-3", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-medium text-slate-100", children: [_jsx(FileText, { className: "h-4 w-4 shrink-0" }), _jsx("span", { className: "truncate", children: item.taskTitle })] }), item.summary ? _jsx("p", { className: "mt-2 text-xs leading-5 text-slate-300", children: item.summary }) : null] }), item.blueprintId ? (_jsxs("div", { className: "flex shrink-0 items-center gap-1", children: [_jsx("span", { className: "rounded-md bg-white/10 px-2 py-1 text-[10px] text-slate-300", children: item.blueprintId }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void loadBlueprintDetail(item.blueprintId); }, "data-testid": `chief-engineer-blueprint-open-${item.blueprintId}`, className: "h-6 px-2 text-[10px] text-slate-300 hover:bg-white/[0.06] hover:text-slate-100", title: "\u8BFB\u53D6 Chief Engineer \u84DD\u56FE\u8BE6\u60C5", children: [_jsx(FileCode, { className: "mr-1 h-3 w-3" }), "\u8BE6\u60C5"] }), item.source === 'runtime/blueprints' ? (_jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void handleDeleteBlueprint(item.blueprintId); }, disabled: deletingBlueprintId === item.blueprintId, "data-testid": `chief-engineer-blueprint-delete-${item.blueprintId}`, className: "h-6 px-2 text-[10px] text-rose-200 hover:bg-rose-500/10 hover:text-rose-100 disabled:opacity-50", title: "\u5220\u9664\u5DF2\u843D\u76D8\u7684 Chief Engineer \u84DD\u56FE", children: [deletingBlueprintId === item.blueprintId ? (_jsx(Loader2, { className: "mr-1 h-3 w-3 animate-spin" })) : (_jsx(Trash2, { className: "mr-1 h-3 w-3" })), "\u6E05\u7406"] })) : null] })) : null] }), item.blueprintPath ? (_jsx("div", { className: "mt-2 truncate rounded-md border border-white/10 bg-slate-950/50 px-2 py-1 text-[11px] text-slate-400", title: item.blueprintPath, children: item.blueprintPath })) : null, _jsxs("div", { className: "mt-2 inline-flex rounded-md border border-white/10 bg-slate-950/55 px-2 py-1 text-[10px] text-slate-300", "data-testid": "chief-engineer-blueprint-provenance", title: `source: ${item.source}`, children: ["source \u00B7 ", item.source] }), item.targetFiles.length > 0 ? (_jsx("div", { className: "mt-2 flex flex-wrap gap-1", children: item.targetFiles.slice(0, 8).map((file) => (_jsx("span", { className: "rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-300", children: file }, file))) })) : null] }, `${item.taskId}-${item.blueprintId || item.blueprintPath}`)))), blueprintDeleteError ? (_jsx("div", { "data-testid": "chief-engineer-blueprint-delete-error", className: "rounded-md border border-red-500/25 bg-red-500/10 px-2 py-1.5 text-xs text-red-100", children: blueprintDeleteError })) : null, blueprintDeleteEvidence ? (_jsx("div", { "data-testid": "chief-engineer-blueprint-delete-evidence", className: "rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-1.5 font-mono text-[11px] text-emerald-100", children: blueprintDeleteEvidence })) : null, blueprintCandidateTasks.length > 0 || bulkGenerateError || bulkGenerateEvidence || bulkGeneratingBlueprints ? (_jsxs("div", { "data-testid": "chief-engineer-blueprint-candidates", className: "soft-panel-subtle rounded-lg p-3", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(FilePlus, { className: "h-3.5 w-3.5" }), "\u5F85\u751F\u6210\u84DD\u56FE", _jsx("span", { className: "soft-chip rounded px-1.5 py-0.5 font-mono text-[10px] text-slate-200", children: missingBlueprintHandoffTasks.length })] }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void handleGenerateAllBlueprints(); }, disabled: bulkGeneratingBlueprints || diagnosticsGenerateBlocked || missingBlueprintHandoffTasks.length === 0, title: diagnosticsGenerateBlocked ? diagnosticsGenerateBlockReason : '为全部缺失任务批量生成 Chief Engineer 蓝图', "data-testid": "chief-engineer-blueprint-generate-all", className: "h-7 shrink-0 px-2 text-[10px] text-slate-300 hover:bg-white/[0.06] hover:text-slate-100 disabled:opacity-50", children: [bulkGeneratingBlueprints ? _jsx(Loader2, { className: "mr-1 h-3 w-3 animate-spin" }) : _jsx(FilePlus, { className: "mr-1 h-3 w-3" }), "\u8865\u9F50\u5168\u90E8"] })] }), _jsx("div", { className: "space-y-2", children: blueprintCandidateTasks.map((task) => {
                                                    const taskId = taskHandoffId(task);
                                                    const isGenerating = generatingTaskId === taskId;
                                                    const generationDisabled = isGenerating || bulkGeneratingBlueprints || diagnosticsGenerateBlocked;
                                                    const statusCheck = blueprintStatusChecks[taskId];
                                                    return (_jsxs("div", { className: "rounded-md border border-white/10 bg-white/[0.03] px-2 py-2", children: [_jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-xs font-medium text-slate-200", children: taskTitle(task) }), _jsx("div", { className: "mt-0.5 truncate text-[10px] text-slate-500", children: taskObjective(task) })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-1.5", children: [_jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void handleCheckBlueprintStatus(task); }, disabled: Boolean(statusCheck?.loading), "data-testid": `chief-engineer-blueprint-status-${taskId}`, className: "h-7 px-2 text-[10px] text-slate-300 hover:bg-white/5 hover:text-slate-100", children: [statusCheck?.loading ? _jsx(Loader2, { className: "mr-1 h-3 w-3 animate-spin" }) : _jsx(CheckCircle2, { className: "mr-1 h-3 w-3" }), "\u72B6\u6001"] }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void handleGenerateBlueprint(task); }, disabled: generationDisabled, title: diagnosticsGenerateBlocked ? diagnosticsGenerateBlockReason : undefined, "data-testid": `chief-engineer-blueprint-generate-${taskId}`, className: "h-7 px-2 text-[10px] text-slate-300 hover:bg-white/[0.06] hover:text-slate-100", children: [isGenerating ? _jsx(Loader2, { className: "mr-1 h-3 w-3 animate-spin" }) : _jsx(FilePlus, { className: "mr-1 h-3 w-3" }), "\u751F\u6210"] })] })] }), statusCheck ? (_jsxs("div", { "data-testid": `chief-engineer-blueprint-status-result-${taskId}`, className: cn('mt-2 rounded-md border px-2 py-1.5 text-[11px]', statusCheck.error
                                                                    ? 'border-red-500/25 bg-red-500/10 text-red-100'
                                                                    : 'border-white/[0.06] bg-white/[0.03] text-slate-200'), children: [_jsx("div", { className: "mb-1 flex items-center", children: _jsx(EvidenceEndpointBadge, { endpoint: blueprintStatusEvidenceEndpoint(taskId, workspace), testId: `chief-engineer-blueprint-status-endpoint-${taskId}` }) }), statusCheck.error ? (_jsx("div", { children: statusCheck.error })) : statusCheck.loading ? (_jsx("div", { children: "\u6B63\u5728\u8BFB\u53D6\u84DD\u56FE\u72B6\u6001..." })) : statusCheck.result ? (_jsxs("div", { className: "space-y-1", children: [_jsxs("div", { children: ["status \u00B7 ", statusCheck.result.status || 'unknown', statusCheck.result.blueprint_id ? ` / ${statusCheck.result.blueprint_id}` : ''] }), statusCheck.result.summary ? (_jsx("div", { className: "line-clamp-2 text-slate-300", children: statusCheck.result.summary })) : null] })) : null] })) : null] }, taskId));
                                                }) }), generateError ? (_jsx("div", { "data-testid": "chief-engineer-blueprint-generate-error", className: "mt-2 rounded-md border border-red-500/25 bg-red-500/10 px-2 py-1.5 text-xs text-red-100", children: generateError })) : null, bulkGenerateError ? (_jsx("div", { "data-testid": "chief-engineer-blueprint-bulk-error", className: "mt-2 rounded-md border border-red-500/25 bg-red-500/10 px-2 py-1.5 text-xs text-red-100", children: bulkGenerateError })) : null, bulkGenerateEvidence ? (_jsx("div", { "data-testid": "chief-engineer-blueprint-bulk-evidence", className: "mt-2 rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-1.5 font-mono text-[11px] text-emerald-100", children: bulkGenerateEvidence })) : null] })) : null] })] }), _jsxs("aside", { className: "flex min-h-0 flex-col gap-3 overflow-auto", children: [_jsxs("section", { "data-testid": "chief-engineer-diagnostics", className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center justify-between gap-2", children: [_jsxs("h3", { className: "flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(CheckCircle2, { className: "h-3.5 w-3.5 text-emerald-300" }), "CE \u8BCA\u65AD"] }), _jsx("span", { "data-testid": "chief-engineer-diagnostics-status", className: cn('rounded-md border px-2 py-1 text-[10px] uppercase tracking-wider', diagnosticsState === 'ready' && 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200', diagnosticsState === 'degraded' && 'border-amber-500/30 bg-amber-500/10 text-amber-200', diagnosticsState === 'error' && 'border-red-500/30 bg-red-500/10 text-red-200', diagnosticsState === 'checking' && 'border-slate-500/30 bg-slate-500/10 text-slate-300'), children: diagnosticsState })] }), _jsxs("div", { className: "space-y-2", children: [_jsx(DiagnosticRow, { label: "Workspace", value: diagnostics?.workspace.status || 'checking', tone: workspaceDiagnosticTone }), _jsx(DiagnosticRow, { label: "LLM", value: llmDiagnosticValue, tone: llmDiagnosticTone }), _jsx(DiagnosticRow, { label: "Blueprints", value: diagnostics ? `${diagnostics.blueprints.loadable}/${diagnostics.blueprints.total}` : 'checking', tone: blueprintDiagnosticTone }), _jsx(DiagnosticRow, { label: "Task coverage", value: blueprintCoverageValue, tone: blueprintCoverageTone }), _jsx(DiagnosticRow, { label: "Director handoff", value: handoffDiagnosticValue, tone: handoffDiagnosticTone }), _jsx(DiagnosticRow, { label: "Missing PM tasks", value: missingBlueprintValue, tone: effectiveMissingBlueprintTaskIds.length > 0 ? 'degraded' : handoffDiagnosticTone })] }), diagnosticsError ? (_jsx("div", { "data-testid": "chief-engineer-diagnostics-error", className: "mt-3 rounded-md border border-red-500/25 bg-red-500/10 px-2 py-2 text-xs text-red-100", children: diagnosticsError })) : null, effectiveDiagnosticIssues.length ? (_jsx("div", { "data-testid": "chief-engineer-diagnostics-issues", className: "mt-3 rounded-md border border-amber-500/20 bg-amber-500/10 px-2 py-2 text-[11px] text-amber-100", children: effectiveDiagnosticIssues.join(', ') })) : null] }), _jsx("section", { "data-testid": "chief-engineer-runtime-activity", className: "h-[340px] min-h-[280px] overflow-hidden rounded-lg border border-white/[0.06] bg-slate-950/60", children: _jsx(RealtimeActivityPanel, { executionLogs: executionLogs, llmStreamEvents: llmStreamEvents, processStreamEvents: processStreamEvents, currentPhase: currentPhase, isRunning: chiefRuntimeActive, role: "chief_engineer" }) }), _jsxs("section", { "data-testid": "chief-engineer-director-task-pool", className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("h3", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(ShieldCheck, { className: "h-3.5 w-3.5 text-slate-400" }), "Director \u4EFB\u52A1\u6C60"] }), _jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsx(EvidenceEndpointBadge, { endpoint: "/v2/director/tasks", testId: "chief-engineer-director-task-pool-endpoint" }), _jsx("span", { "data-testid": "chief-engineer-director-task-source", className: "rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] text-slate-500", children: "runtime push" })] }), _jsxs("div", { className: "grid grid-cols-2 gap-2 text-center", children: [_jsx(Metric, { label: "\u672A\u9886\u53D6", value: stats.unclaimed, tone: "slate" }), _jsx(Metric, { label: "\u6267\u884C\u4E2D", value: stats.running, tone: "blue" }), _jsx(Metric, { label: "\u963B\u585E", value: stats.blocked, tone: "amber" }), _jsx(Metric, { label: "\u62A5\u9519", value: stats.failed, tone: "red" }), _jsx(Metric, { label: "\u5B8C\u6210", value: stats.completed, tone: "emerald" }), _jsx(Metric, { label: "\u603B\u8BA1", value: stats.total, tone: "slate" })] }), lastDirectorStatus ? (_jsxs("div", { className: "mt-3 rounded-md border border-white/10 bg-slate-950/50 px-2 py-2 text-xs text-slate-300", children: ["\u6700\u8FD1 Director \u72B6\u6001: ", lastDirectorStatus] })) : null] }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("h3", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(FileCode, { className: "h-3.5 w-3.5 text-slate-400" }), "\u84DD\u56FE\u8BE6\u60C5"] }), !selectedBlueprintId ? (_jsx("div", { "data-testid": "chief-engineer-blueprint-detail-empty", className: "rounded-md border border-white/10 bg-slate-950/50 p-3 text-xs text-slate-400", children: "\u9009\u62E9\u5DE6\u4FA7\u84DD\u56FE\u540E\uFF0C\u8FD9\u91CC\u5C55\u793A\u540E\u7AEF\u6301\u4E45\u5316\u7684\u539F\u59CB blueprint payload\u3002" })) : blueprintDetailLoading ? (_jsxs("div", { "data-testid": "chief-engineer-blueprint-detail-loading", className: "flex items-center gap-2 rounded-md border border-white/[0.06] bg-white/[0.03] p-3 text-xs text-slate-200", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }), "\u6B63\u5728\u8BFB\u53D6 ", selectedBlueprintId] })) : blueprintDetailError ? (_jsxs("div", { "data-testid": "chief-engineer-blueprint-detail-error", className: "rounded-md border border-red-500/25 bg-red-500/10 p-3 text-xs text-red-100", children: [selectedBlueprintId, ": ", blueprintDetailError] })) : blueprintDetail ? (_jsxs("div", { "data-testid": "chief-engineer-blueprint-detail", className: "min-w-0 rounded-md border border-white/[0.06] bg-slate-950/60", children: [_jsxs("div", { className: "flex items-center justify-between gap-2 border-b border-white/10 px-3 py-2 text-[11px]", children: [_jsx("span", { className: "truncate font-mono text-slate-200", children: blueprintDetail.blueprint_id }), _jsx("span", { className: "shrink-0 rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-400", children: blueprintDetail.source || 'runtime/blueprints' })] }), _jsx("pre", { className: "max-h-72 overflow-auto p-3 text-[10px] leading-4 text-slate-300", children: JSON.stringify(blueprintDetail.blueprint, null, 2) })] })) : null] }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-3 flex items-center justify-between gap-2", children: [_jsxs("h3", { className: "flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(Hammer, { className: "h-3.5 w-3.5 text-indigo-300" }), "\u5F53\u524D Director \u5217\u8868"] }), _jsx("span", { className: "rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] text-slate-500", children: "/v2/director/workers" })] }), directorWorkerApiError ? (_jsx("div", { "data-testid": "chief-engineer-director-worker-error", className: "mb-2 rounded-md border border-amber-500/25 bg-amber-500/10 p-2 text-xs text-amber-100", children: directorWorkerApiError })) : null, directorRows.length === 0 ? (_jsx("div", { "data-testid": "chief-engineer-director-empty", className: "rounded-md border border-white/10 bg-slate-950/50 p-3 text-xs text-slate-400", children: directorWorkerLoading
                                            ? '正在读取 Director worker 心跳...'
                                            : '暂无 Director worker 心跳。启动 Director 后这里显示每个 worker 的状态和当前任务。' })) : (_jsx("div", { "data-testid": "chief-engineer-director-list", className: "space-y-2", children: directorRows.map((worker) => (_jsxs("div", { className: "rounded-md border border-white/10 bg-slate-950/50 p-2 text-xs", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate font-medium text-slate-200", children: worker.name || worker.id }), _jsx("span", { className: cn('rounded px-1.5 py-0.5 text-[10px]', worker.status === 'busy' ? 'bg-blue-500/[0.15] text-blue-200' :
                                                                worker.status === 'failed' ? 'bg-red-500/[0.15] text-red-200' :
                                                                    'bg-emerald-500/[0.15] text-emerald-200'), children: worker.status || 'unknown' })] }), _jsxs("div", { className: "mt-1 truncate text-slate-400", children: ["\u5F53\u524D\u4EFB\u52A1: ", worker.currentTaskId || '空闲'] }), _jsxs("div", { className: "mt-1 flex gap-2 text-[10px] text-slate-500", children: [_jsxs("span", { children: ["\u5B8C\u6210 ", worker.tasksCompleted ?? 0] }), _jsxs("span", { children: ["\u5931\u8D25 ", worker.tasksFailed ?? 0] }), worker.healthy === false ? _jsx("span", { className: "text-red-300", children: "unhealthy" }) : null] })] }, worker.id))) }))] }), _jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("h3", { className: "mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300", children: [_jsx(Activity, { className: "h-3.5 w-3.5 text-emerald-300" }), "\u5DE5\u4F5C\u533A"] }), _jsx("div", { className: "break-all rounded-md border border-white/10 bg-slate-950/50 p-2 text-xs text-slate-400", children: workspace || '未选择 workspace' })] })] }), showAIDialogue ? (_jsx("section", { "data-testid": "chief-engineer-dialogue", className: "min-h-0 overflow-hidden rounded-lg border border-white/[0.06] bg-slate-950/45", children: _jsx(AIDialoguePanel, { dialogueRole: "chief_engineer", roleDisplayName: "Chief Engineer", roleTheme: {
                                primary: 'cyan',
                                secondary: 'cyan-400',
                                gradient: 'from-cyan-500 to-cyan-700',
                            }, welcomeMessage: "Chief Engineer \u5DE5\u4F5C\u53F0\u5DF2\u5C31\u7EEA\u3002\u60A8\u53EF\u4EE5\u5BA1\u67E5 PM \u5408\u540C\u3001\u4EA7\u51FA\u65BD\u5DE5\u84DD\u56FE\uFF0C\u6216\u786E\u8BA4 Director \u6267\u884C\u524D\u7F6E\u6761\u4EF6\u3002", context: {
                                workspace,
                                task_count: tasks.length,
                                director_task_count: directorTaskEvidenceRows.length,
                                blueprint_count: blueprintEvidence.length,
                                diagnostics_ok: diagnostics?.ok ?? false,
                                diagnostics_issues: diagnostics?.issues ?? [],
                                chief_engineer_status: chiefStatus,
                                director_running: directorRunning,
                            }, workspace: workspace, hostKind: "electron_workbench", attachmentMode: "isolated", workflowExportTarget: "director", workflowExportLabel: "\u5BFC\u51FA Director" }) })) : null] }))] }));
}
function DiagnosticRow({ label, value, tone }) {
    const tones = {
        ready: 'text-emerald-200',
        degraded: 'text-amber-200',
        error: 'text-red-200',
        checking: 'text-slate-300',
    }, satisfies, Record;
    () => ;
    return (_jsxs("div", { className: "flex min-h-8 items-center justify-between gap-3 border-b border-white/5 py-1.5 last:border-b-0", children: [_jsx("span", { className: "text-xs text-slate-400", children: label }), _jsx("span", { className: cn('max-w-[12rem] truncate text-right text-xs font-medium', tones[tone]), title: value, children: value })] }));
}
function Metric({ label, value, tone }) {
    const tones = {
        slate: 'border-white/[0.08] bg-white/[0.04] text-slate-200',
        blue: 'border-blue-500/25 bg-blue-500/10 text-blue-200',
        amber: 'border-amber-500/25 bg-amber-500/10 text-amber-200',
        red: 'border-red-500/25 bg-red-500/10 text-red-200',
        emerald: 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200',
    }, satisfies, Record;
    _jsxs("typeof", { tone: true, string: true, children: ["; return (", _jsxs("div", { className: cn('rounded-md border px-2 py-2', tones[tone]), children: [_jsx("div", { className: "text-lg font-semibold", children: value }), _jsx("div", { className: "text-[10px] text-current/70", children: label })] }), "); }"] });
}
