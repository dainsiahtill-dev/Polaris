import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState, useMemo } from 'react';
import { Search, Filter, Play, CheckCircle2, Circle, Clock, AlertCircle, ArrowUpDown, FileCode, GitBranch, ListChecks, ShieldCheck, Target, Plus, Loader2, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { Badge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import { getPmTask, listPmTaskAssignments, searchPmTasks, } from '@/services/pmService';
import { listChiefEngineerBlueprints } from '@/services/chiefEngineerService';
import { pmTaskService } from '@/services/api';
import { TaskStatus } from '@/types/task';
import { TaskTraceInline } from '../common/TaskTraceInline';
import { TaskTraceTimeline } from '../common/TaskTraceTimeline';
function EndpointBadge({ endpoint, method, testId, }) {
    return (_jsx("span", { className: "shrink-0 rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] font-medium text-slate-500", title: endpoint, "data-endpoint": endpoint, "data-testid": testId, children: method ? `${method} API` : 'API' }));
}
function taskRecord(task) {
    return task;
}
function normalizeTaskId(value) {
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number' && Number.isFinite(value))
        return String(value);
    if (typeof value === 'bigint')
        return String(value);
    if (value && typeof value === 'object') {
        const record = value;
        return normalizeTaskId(record.id ?? record.task_id);
    }
    return '';
}
function metadataOf(task) {
    const metadata = taskRecord(task).metadata;
    return metadata && typeof metadata === 'object' ? metadata : {};
}
function readTaskValue(task, keys) {
    const direct = taskRecord(task);
    const metadata = metadataOf(task);
    for (const key of keys) {
        const directValue = direct[key];
        if (directValue !== undefined && directValue !== null)
            return directValue;
        const metadataValue = metadata[key];
        if (metadataValue !== undefined && metadataValue !== null)
            return metadataValue;
    }
    return undefined;
}
function readTaskString(task, keys) {
    const value = readTaskValue(task, keys);
    return typeof value === 'string' ? value.trim() : '';
}
function displayText(value) {
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number' && Number.isFinite(value))
        return String(value);
    if (typeof value === 'bigint')
        return String(value);
    return '';
}
function isReadableTaskText(value) {
    const text = displayText(value);
    if (!text)
        return false;
    return !/^\d+$/.test(text);
}
function readTaskDisplayString(task, keys) {
    const direct = taskRecord(task);
    const metadata = metadataOf(task);
    for (const key of keys) {
        const directValue = direct[key];
        if (isReadableTaskText(directValue))
            return displayText(directValue);
        const metadataValue = metadata[key];
        if (isReadableTaskText(metadataValue))
            return displayText(metadataValue);
    }
    return '';
}
function taskDisplayTitle(task) {
    return readTaskDisplayString(task, ['subject', 'title', 'name', 'goal', 'summary', 'description'])
        || normalizeTaskId(task.id)
        || '未命名任务';
}
function taskDisplaySummary(task) {
    const title = taskDisplayTitle(task);
    const summary = readTaskDisplayString(task, ['summary', 'goal', 'description']);
    return summary && summary !== title ? summary : '';
}
function toStringList(value) {
    if (!Array.isArray(value)) {
        const token = typeof value === 'string' ? value.trim() : '';
        return token ? [token] : [];
    }
    return value
        .map((item) => {
        if (typeof item === 'string')
            return item.trim();
        if (item && typeof item === 'object') {
            const record = item;
            return String(record.description || record.title || record.name || record.path || record.id || '').trim();
        }
        return String(item || '').trim();
    })
        .filter(Boolean);
}
function toAcceptanceCriteriaList(value) {
    return toStringList(value).map((description) => ({ description }));
}
function readTaskStringList(task, keys) {
    const values = [];
    for (const key of keys) {
        values.push(...toStringList(taskRecord(task)[key]));
        values.push(...toStringList(metadataOf(task)[key]));
    }
    return values.filter((item, index, all) => item.length > 0 && all.indexOf(item) === index);
}
function canonicalTaskMatchId(value) {
    const text = String(value || '').trim();
    if (!text)
        return '';
    const normalized = text.toLowerCase();
    const numericAlias = normalized.match(/^(?:task|pm-task|pm)[-_]?(\d+)$/);
    return numericAlias ? numericAlias[1] : normalized;
}
function readRecordString(record, keys) {
    for (const key of keys) {
        const value = record[key];
        const text = displayText(value);
        if (text)
            return text;
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
function blueprintTaskIdSet(rows) {
    const ids = new Set();
    for (const row of rows) {
        const taskId = runtimeBlueprintTaskId(row);
        const canonical = canonicalTaskMatchId(taskId);
        if (canonical)
            ids.add(canonical);
    }
    return ids;
}
function taskMatchIds(task) {
    const metadata = metadataOf(task);
    return [
        task.id,
        taskRecord(task).task_id,
        taskRecord(task).pm_task_id,
        taskRecord(task).taskId,
        taskRecord(task).pmTaskId,
        taskRecord(task).backlog_ref,
        taskRecord(task).external_task_id,
        metadata.task_id,
        metadata.pm_task_id,
        metadata.taskId,
        metadata.pmTaskId,
        metadata.backlog_ref,
        metadata.external_task_id,
    ]
        .map(canonicalTaskMatchId)
        .filter((item, index, all) => item.length > 0 && all.indexOf(item) === index);
}
function taskHasBlueprintEvidence(task, blueprintTaskIds) {
    if (readTaskString(task, ['blueprint_id', 'blueprintId', 'blueprint_path', 'blueprintPath', 'runtime_blueprint_path', 'runtimeBlueprintPath'])) {
        return true;
    }
    return taskMatchIds(task).some((taskId) => blueprintTaskIds.has(taskId));
}
function nestedTaskRecord(task, key) {
    const value = readTaskValue(task, [key]);
    return value && typeof value === 'object' && !Array.isArray(value)
        ? value
        : {};
}
function taskExecutionStatus(task) {
    const runtimeExecution = nestedTaskRecord(task, 'runtime_execution');
    return (readRecordString(runtimeExecution, ['effective_status', 'raw_status', 'status', 'state'])
        || readTaskString(task, ['status', 'state'])).toLowerCase();
}
function directorStage(task) {
    const status = taskExecutionStatus(task);
    if (task.done || task.completed || ['completed', 'done', 'success', 'passed'].includes(status)) {
        return { value: '已交付', tone: 'ready' };
    }
    if (['running', 'in_progress', 'claimed', 'pending_exec'].includes(status)) {
        return { value: '执行中', tone: 'running' };
    }
    if (['failed', 'failure', 'error', 'blocked'].includes(status)) {
        return { value: '执行失败', tone: 'failed' };
    }
    return { value: '待执行', tone: 'waiting' };
}
function ceBlueprintStage(task, blueprintEvidence) {
    if (taskHasBlueprintEvidence(task, blueprintEvidence.taskIds)) {
        return { value: '蓝图已生成', tone: 'ready' };
    }
    if (blueprintEvidence.loading) {
        return { value: '蓝图同步中', tone: 'running' };
    }
    if (blueprintEvidence.error) {
        return { value: '蓝图未知', tone: 'failed' };
    }
    return { value: '待蓝图', tone: 'waiting' };
}
function stageToneClass(tone) {
    if (tone === 'ready')
        return 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200';
    if (tone === 'running')
        return 'border-amber-500/25 bg-amber-500/10 text-amber-200';
    if (tone === 'failed')
        return 'border-red-500/25 bg-red-500/10 text-red-200';
    return 'border-slate-700 bg-slate-900/70 text-slate-400';
}
function readAcceptanceCriteria(task) {
    const qaContract = readTaskValue(task, ['qa_contract']);
    const qaCriteria = qaContract && typeof qaContract === 'object'
        ? toStringList(qaContract.acceptance_criteria)
        : [];
    return [
        ...toStringList(task.acceptance),
        ...readTaskStringList(task, ['acceptance_criteria', 'acceptanceCriteria', 'acceptance']),
        ...qaCriteria,
    ].filter((item, index, all) => item.length > 0 && all.indexOf(item) === index);
}
function readTaskSearchId(result) {
    const id = result.id ?? result.task_id;
    return normalizeTaskId(id);
}
function readTaskSearchMetadata(result) {
    const metadata = result.metadata;
    return metadata && typeof metadata === 'object' && !Array.isArray(metadata)
        ? metadata
        : {};
}
function readTaskSearchString(result, keys) {
    const metadata = readTaskSearchMetadata(result);
    for (const key of keys) {
        const value = result[key];
        if (typeof value === 'string' && value.trim()) {
            return value.trim();
        }
        const metadataValue = metadata[key];
        if (typeof metadataValue === 'string' && metadataValue.trim()) {
            return metadataValue.trim();
        }
    }
    return '';
}
function normalizeTaskSearchStatus(result) {
    const status = readTaskSearchString(result, ['status', 'state']).toLowerCase();
    if (status === 'completed' || status === 'done' || status === 'success')
        return TaskStatus.COMPLETED;
    if (status === 'running' || status === 'in_progress')
        return TaskStatus.IN_PROGRESS;
    if (status === 'blocked')
        return TaskStatus.BLOCKED;
    if (status === 'failed' || status === 'failure')
        return TaskStatus.FAILED;
    return TaskStatus.PENDING;
}
function readTaskSearchPriority(result) {
    const value = result.priority;
    if (typeof value === 'number' && Number.isFinite(value))
        return value;
    if (typeof value === 'string') {
        const parsed = Number.parseInt(value, 10);
        if (Number.isFinite(parsed))
            return parsed;
    }
    return 99;
}
function normalizeTaskSearchResult(result, sourceFallback = 'pm_task_search') {
    const id = readTaskSearchId(result);
    if (!id)
        return null;
    const status = normalizeTaskSearchStatus(result);
    const resultMetadata = readTaskSearchMetadata(result);
    const acceptance = [
        ...toStringList(result.acceptance),
        ...toStringList(result.acceptance_criteria),
    ].map((description) => ({ description }));
    const qaContract = result.qa_contract;
    return {
        id,
        title: readTaskSearchString(result, ['title', 'subject', 'name']) || id,
        goal: readTaskSearchString(result, ['goal']),
        summary: readTaskSearchString(result, ['summary', 'snippet', 'description']),
        description: readTaskSearchString(result, ['description']),
        status,
        done: result.done === true || result.completed === true || status === TaskStatus.COMPLETED,
        priority: readTaskSearchPriority(result),
        acceptance,
        execution_checklist: toStringList(result.execution_checklist ?? result.steps),
        target_files: toStringList(result.target_files ?? result.files ?? result.scope_paths),
        dependencies: toStringList(result.dependencies ?? result.blocked_by),
        qa_contract: qaContract && typeof qaContract === 'object' ? qaContract : undefined,
        blueprint_id: readTaskSearchString(result, ['blueprint_id', 'blueprintId']) || null,
        blueprint_path: readTaskSearchString(result, ['blueprint_path', 'blueprintPath']) || null,
        runtime_blueprint_path: readTaskSearchString(result, ['runtime_blueprint_path', 'runtimeBlueprintPath']) || null,
        metadata: {
            ...result,
            ...resultMetadata,
            source: readTaskSearchString(result, ['source']) || sourceFallback,
        },
    };
}
function nonEmptyTaskStrings(values) {
    return toStringList(values);
}
function mergePmTaskDetailProjection(base, detail) {
    const detailAcceptance = toAcceptanceCriteriaList(detail.acceptance);
    const baseAcceptance = toAcceptanceCriteriaList(base.acceptance);
    const detailAcceptanceCriteria = nonEmptyTaskStrings(detail.acceptance_criteria);
    const baseAcceptanceCriteria = nonEmptyTaskStrings(base.acceptance_criteria);
    const detailSteps = nonEmptyTaskStrings(detail.execution_checklist ?? detail.steps);
    const baseSteps = nonEmptyTaskStrings(base.execution_checklist ?? base.steps);
    const detailTargetFiles = nonEmptyTaskStrings(detail.target_files ?? detail.files ?? detail.scope_paths);
    const baseTargetFiles = nonEmptyTaskStrings(base.target_files ?? base.files ?? base.scope_paths);
    const detailDependencies = nonEmptyTaskStrings(detail.dependencies ?? detail.blocked_by);
    const baseDependencies = nonEmptyTaskStrings(base.dependencies ?? base.blocked_by);
    const baseMetadata = metadataOf(base);
    const detailMetadata = metadataOf(detail);
    return {
        ...base,
        ...detail,
        title: detail.title || base.title,
        subject: detail.subject || base.subject,
        goal: detail.goal || base.goal,
        summary: detail.summary || base.summary,
        description: detail.description || base.description,
        acceptance: detailAcceptance.length > 0 ? detailAcceptance : baseAcceptance,
        acceptance_criteria: detailAcceptanceCriteria.length > 0 ? detailAcceptanceCriteria : baseAcceptanceCriteria,
        execution_checklist: detailSteps.length > 0 ? detailSteps : baseSteps,
        target_files: detailTargetFiles.length > 0 ? detailTargetFiles : baseTargetFiles,
        dependencies: detailDependencies.length > 0 ? detailDependencies : baseDependencies,
        qa_contract: detail.qa_contract || base.qa_contract,
        metadata: {
            ...baseMetadata,
            ...detailMetadata,
            source: readTaskString(detail, ['source']) || 'pm_task_detail',
        },
    };
}
function assignmentRecord(assignment) {
    return assignment;
}
function readAssignmentString(assignment, keys) {
    const record = assignmentRecord(assignment);
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'string' && value.trim())
            return value.trim();
        if (typeof value === 'number' && Number.isFinite(value))
            return String(value);
    }
    return '';
}
function assignmentIdentity(assignment) {
    return readAssignmentString(assignment, [
        'assignee',
        'assigned_to',
        'director_id',
        'worker_id',
        'owner',
        'role',
        'id',
    ]) || 'unassigned';
}
function assignmentState(assignment) {
    return readAssignmentString(assignment, ['status', 'action', 'state', 'event']) || 'recorded';
}
function assignmentTime(assignment) {
    return readAssignmentString(assignment, ['assigned_at', 'updated_at', 'created_at', 'timestamp']);
}
function formatTaskSearchMeta(result) {
    const parts = ['PM search API'];
    const id = readTaskSearchId(result);
    if (id)
        parts.push(id);
    if (typeof result.score === 'number')
        parts.push(`score ${result.score.toFixed(2)}`);
    return parts.join(' · ');
}
export function PMTaskPanel({ tasks, selectedTaskId, onTaskSelect, onTaskCreated, pmRunning, taskTraceMap, workspace = '', createDisabledReason = '', }) {
    const [filter, setFilter] = useState('all');
    const [sort, setSort] = useState('priority');
    const [searchQuery, setSearchQuery] = useState('');
    const [showCreatePanel, setShowCreatePanel] = useState(false);
    const [createSubject, setCreateSubject] = useState('');
    const [createDescription, setCreateDescription] = useState('');
    const [createPriority, setCreatePriority] = useState('medium');
    const [createAcceptanceText, setCreateAcceptanceText] = useState('');
    const [taskSearchResults, setTaskSearchResults] = useState([]);
    const [taskSearchError, setTaskSearchError] = useState(null);
    const [isTaskSearchLoading, setIsTaskSearchLoading] = useState(false);
    const [backendSelectedTask, setBackendSelectedTask] = useState(null);
    const [taskDetailEvidence, setTaskDetailEvidence] = useState({
        taskId: '',
        loading: false,
        error: null,
        task: null,
    });
    const [assignmentEvidence, setAssignmentEvidence] = useState({
        taskId: '',
        loading: false,
        error: null,
        assignments: [],
        count: 0,
    });
    const [createEvidence, setCreateEvidence] = useState({
        loading: false,
        error: null,
        task: null,
    });
    const [blueprintEvidence, setBlueprintEvidence] = useState({
        loading: false,
        error: '',
        taskIds: new Set(),
    });
    const normalizedSelectedTaskId = normalizeTaskId(selectedTaskId);
    const selectedTaskProjection = useMemo(() => tasks.find((task) => normalizeTaskId(task.id) === normalizedSelectedTaskId) ??
        (normalizeTaskId(backendSelectedTask?.id) === normalizedSelectedTaskId ? backendSelectedTask : null), [backendSelectedTask, normalizedSelectedTaskId, tasks]);
    const selectedTask = useMemo(() => {
        const backendDetail = taskDetailEvidence.taskId === normalizedSelectedTaskId ? taskDetailEvidence.task : null;
        if (selectedTaskProjection && backendDetail) {
            return mergePmTaskDetailProjection(selectedTaskProjection, backendDetail);
        }
        return selectedTaskProjection ?? backendDetail;
    }, [normalizedSelectedTaskId, selectedTaskProjection, taskDetailEvidence]);
    const normalizedCreateDisabledReason = createDisabledReason.trim();
    const createTaskDisabled = normalizedCreateDisabledReason.length > 0;
    useEffect(() => {
        if (createTaskDisabled && showCreatePanel) {
            setShowCreatePanel(false);
        }
    }, [createTaskDisabled, showCreatePanel]);
    useEffect(() => {
        if (!workspace || tasks.length === 0) {
            setBlueprintEvidence({
                loading: false,
                error: '',
                taskIds: new Set(),
            });
            return undefined;
        }
        let isCurrent = true;
        setBlueprintEvidence((current) => ({
            ...current,
            loading: true,
            error: '',
        }));
        void listChiefEngineerBlueprints(workspace).then((result) => {
            if (!isCurrent)
                return;
            if (result.ok && result.data) {
                setBlueprintEvidence({
                    loading: false,
                    error: '',
                    taskIds: blueprintTaskIdSet(Array.isArray(result.data.blueprints) ? result.data.blueprints : []),
                });
                return;
            }
            setBlueprintEvidence({
                loading: false,
                error: result.error || 'Chief Engineer blueprint evidence unavailable',
                taskIds: new Set(),
            });
        }).catch((error) => {
            if (!isCurrent)
                return;
            setBlueprintEvidence({
                loading: false,
                error: error instanceof Error ? error.message : 'Chief Engineer blueprint evidence unavailable',
                taskIds: new Set(),
            });
        });
        return () => {
            isCurrent = false;
        };
    }, [tasks.length, workspace]);
    useEffect(() => {
        const query = searchQuery.trim();
        if (query.length < 2) {
            setTaskSearchResults([]);
            setTaskSearchError(null);
            setIsTaskSearchLoading(false);
            return undefined;
        }
        let isCurrent = true;
        setIsTaskSearchLoading(true);
        setTaskSearchError(null);
        const timeoutId = window.setTimeout(async () => {
            const result = await searchPmTasks(query, 20, workspace);
            if (!isCurrent)
                return;
            if (result.ok && result.data) {
                setTaskSearchResults(result.data.results || []);
            }
            else {
                setTaskSearchResults([]);
                setTaskSearchError(result.error || 'PM 任务搜索不可用');
            }
            setIsTaskSearchLoading(false);
        }, 250);
        return () => {
            isCurrent = false;
            window.clearTimeout(timeoutId);
        };
    }, [searchQuery, workspace]);
    useEffect(() => {
        const taskId = normalizedSelectedTaskId;
        if (!taskId) {
            setTaskDetailEvidence({
                taskId: '',
                loading: false,
                error: null,
                task: null,
            });
            return undefined;
        }
        let isCurrent = true;
        setTaskDetailEvidence({
            taskId,
            loading: true,
            error: null,
            task: null,
        });
        void getPmTask(taskId, workspace).then((result) => {
            if (!isCurrent)
                return;
            if (result.ok && result.data) {
                const task = normalizeTaskSearchResult(result.data, 'pm_task_detail');
                setTaskDetailEvidence({
                    taskId,
                    loading: false,
                    error: task ? null : 'PM 任务详情缺少任务 ID',
                    task,
                });
                return;
            }
            setTaskDetailEvidence({
                taskId,
                loading: false,
                error: result.error || 'PM 任务详情不可用',
                task: null,
            });
        }).catch((error) => {
            if (!isCurrent)
                return;
            setTaskDetailEvidence({
                taskId,
                loading: false,
                error: error instanceof Error ? error.message : 'PM 任务详情不可用',
                task: null,
            });
        });
        return () => {
            isCurrent = false;
        };
    }, [normalizedSelectedTaskId, workspace]);
    useEffect(() => {
        const taskId = normalizedSelectedTaskId;
        if (!taskId) {
            setAssignmentEvidence({
                taskId: '',
                loading: false,
                error: null,
                assignments: [],
                count: 0,
            });
            return undefined;
        }
        let isCurrent = true;
        setAssignmentEvidence({
            taskId,
            loading: true,
            error: null,
            assignments: [],
            count: 0,
        });
        void listPmTaskAssignments(taskId, 100, workspace).then((result) => {
            if (!isCurrent)
                return;
            if (result.ok && result.data) {
                const assignments = Array.isArray(result.data.assignments) ? result.data.assignments : [];
                setAssignmentEvidence({
                    taskId,
                    loading: false,
                    error: null,
                    assignments,
                    count: typeof result.data.count === 'number' ? result.data.count : assignments.length,
                });
                return;
            }
            setAssignmentEvidence({
                taskId,
                loading: false,
                error: result.error || 'PM 任务分配历史不可用',
                assignments: [],
                count: 0,
            });
        }).catch((error) => {
            if (!isCurrent)
                return;
            setAssignmentEvidence({
                taskId,
                loading: false,
                error: error instanceof Error ? error.message : 'PM 任务分配历史不可用',
                assignments: [],
                count: 0,
            });
        });
        return () => {
            isCurrent = false;
        };
    }, [normalizedSelectedTaskId, workspace]);
    const filteredTasks = useMemo(() => {
        let result = [...tasks];
        // Apply filter
        if (filter !== 'all') {
            result = result.filter((task) => {
                const status = task.status?.toLowerCase() || '';
                if (filter === 'pending')
                    return status === 'pending' || !status;
                if (filter === 'running')
                    return status === 'running' || status === 'in_progress';
                if (filter === 'completed')
                    return status === 'completed' || task.done;
                if (filter === 'blocked')
                    return status === 'blocked' || status === 'failed';
                return true;
            });
        }
        // Apply search
        if (searchQuery.trim()) {
            const query = searchQuery.toLowerCase();
            result = result.filter((task) => taskDisplayTitle(task).toLowerCase().includes(query) ||
                normalizeTaskId(task.id).toLowerCase().includes(query) ||
                taskDisplaySummary(task).toLowerCase().includes(query));
        }
        // Apply sort
        result.sort((a, b) => {
            if (sort === 'priority') {
                // priority is number, lower is higher priority
                const aPriority = typeof a.priority === 'number' ? a.priority : 99;
                const bPriority = typeof b.priority === 'number' ? b.priority : 99;
                return aPriority - bPriority;
            }
            if (sort === 'status') {
                const statusOrder = { running: 0, pending: 1, blocked: 2, completed: 3 };
                const aStatus = a.status || 'pending';
                const bStatus = b.status || 'pending';
                return statusOrder[aStatus] - statusOrder[bStatus];
            }
            if (sort === 'name') {
                return taskDisplayTitle(a).localeCompare(taskDisplayTitle(b));
            }
            return 0;
        });
        return result;
    }, [tasks, filter, sort, searchQuery]);
    const taskStats = useMemo(() => {
        return {
            all: tasks.length,
            pending: tasks.filter((t) => !t.status || t.status === 'pending').length,
            running: tasks.filter((t) => String(t.status) === 'running' || t.status === 'in_progress').length,
            completed: tasks.filter((t) => t.status === 'completed' || t.done).length,
            blocked: tasks.filter((t) => t.status === 'blocked' || t.status === 'failed').length,
        };
    }, [tasks]);
    const handleTaskClick = (task) => {
        setBackendSelectedTask(null);
        onTaskSelect(normalizeTaskId(task.id) || null);
    };
    const handleTaskSearchResultClick = (result) => {
        const task = normalizeTaskSearchResult(result);
        if (!task)
            return;
        setBackendSelectedTask(task);
        onTaskSelect(task.id);
    };
    const handleCreateTask = async () => {
        if (createTaskDisabled) {
            setCreateEvidence({
                loading: false,
                error: normalizedCreateDisabledReason,
                task: null,
            });
            return;
        }
        const subject = createSubject.trim();
        if (!subject) {
            setCreateEvidence({
                loading: false,
                error: '任务标题不能为空',
                task: null,
            });
            return;
        }
        const acceptance = createAcceptanceText
            .split(/\r?\n/)
            .map((item) => item.trim())
            .filter(Boolean);
        setCreateEvidence({
            loading: true,
            error: null,
            task: null,
        });
        const result = await pmTaskService.create({
            subject,
            description: createDescription.trim(),
            priority: createPriority,
            status: 'pending',
            acceptance,
        }, workspace);
        if (!result.ok || !result.data) {
            setCreateEvidence({
                loading: false,
                error: result.error || 'PM 任务创建失败',
                task: null,
            });
            return;
        }
        const createResponseRecord = result.data;
        const responseTitle = createResponseRecord.title;
        const createdTitle = typeof responseTitle === 'string' && responseTitle.trim().length > 0
            ? responseTitle.trim()
            : result.data.subject;
        const createdTask = normalizeTaskSearchResult({
            ...result.data,
            title: createdTitle,
            summary: result.data.description || subject,
            acceptance: result.data.acceptance || acceptance,
            metadata: {
                ...(result.data.metadata || {}),
                source: 'pm_task_create',
            },
        }, 'pm_task_create');
        if (!createdTask) {
            setCreateEvidence({
                loading: false,
                error: 'PM 任务创建成功，但响应缺少任务 ID',
                task: null,
            });
            return;
        }
        onTaskCreated?.(createdTask);
        setBackendSelectedTask(createdTask);
        onTaskSelect(createdTask.id);
        setCreateEvidence({
            loading: false,
            error: null,
            task: createdTask,
        });
        setCreateSubject('');
        setCreateDescription('');
        setCreateAcceptanceText('');
        setShowCreatePanel(false);
    };
    const searchTerm = searchQuery.trim();
    const showBackendSearch = searchTerm.length >= 2;
    const validTaskSearchResults = taskSearchResults.filter((result) => Boolean(readTaskSearchId(result)));
    return (_jsxs("div", { "data-testid": "pm-task-panel", className: "h-full flex", children: [_jsxs("div", { className: "flex-1 flex flex-col min-w-0 border-r border-white/10", children: [_jsxs("div", { "data-testid": "pm-task-toolbar", className: "h-14 flex items-center gap-3 px-4 border-b border-white/10 bg-white/[0.02]", children: [_jsxs("div", { className: "relative flex-1 max-w-sm", children: [_jsx(Search, { className: "absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" }), _jsx(Input, { placeholder: "\u641C\u7D22\u4EFB\u52A1...", value: searchQuery, onChange: (e) => setSearchQuery(e.target.value), className: "pl-9 h-9 bg-white/5 border-white/10 text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50" })] }), _jsxs("div", { className: "flex items-center gap-1 p-1 rounded-lg bg-white/5 border border-white/10", children: [_jsx(FilterButton, { active: filter === 'all', count: taskStats.all, onClick: () => setFilter('all'), children: "\u5168\u90E8" }), _jsx(FilterButton, { active: filter === 'pending', count: taskStats.pending, onClick: () => setFilter('pending'), children: "\u5F85\u529E" }), _jsx(FilterButton, { active: filter === 'running', count: taskStats.running, onClick: () => setFilter('running'), children: "\u8FDB\u884C\u4E2D" }), _jsx(FilterButton, { active: filter === 'blocked', count: taskStats.blocked, onClick: () => setFilter('blocked'), children: "\u963B\u585E" }), _jsx(FilterButton, { active: filter === 'completed', count: taskStats.completed, onClick: () => setFilter('completed'), children: "\u5B8C\u6210" })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs(Button, { variant: "ghost", size: "sm", onClick: () => setSort(sort === 'priority' ? 'status' : 'priority'), className: "text-slate-400 hover:text-slate-200", children: [_jsx(ArrowUpDown, { className: "w-3.5 h-3.5 mr-1.5" }), sort === 'priority' ? '优先级' : sort === 'status' ? '状态' : '名称'] }), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => {
                                            if (createTaskDisabled) {
                                                setCreateEvidence({
                                                    loading: false,
                                                    error: normalizedCreateDisabledReason,
                                                    task: null,
                                                });
                                                return;
                                            }
                                            setShowCreatePanel((current) => !current);
                                        }, disabled: createTaskDisabled, "data-testid": "pm-task-create-toggle", title: normalizedCreateDisabledReason || '创建 PM 任务', className: cn('text-slate-400 hover:bg-amber-500/10 hover:text-amber-200', showCreatePanel && 'bg-amber-500/10 text-amber-200'), children: [_jsx(Plus, { className: "mr-1.5 h-3.5 w-3.5" }), "\u521B\u5EFA\u4EFB\u52A1"] }), _jsx("span", { className: "rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200", children: "\u6765\u6E90: PM \u5408\u540C" })] })] }), showCreatePanel && (_jsxs("div", { className: "border-b border-amber-500/[0.15] bg-slate-950/35 px-4 py-3", "data-testid": "pm-task-create-panel", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-amber-200", children: [_jsx(Plus, { className: "h-3.5 w-3.5" }), "PM Task Create"] }), _jsx(EndpointBadge, { endpoint: "/v2/pm/tasks", method: "POST", testId: "pm-task-create-endpoint" })] }), _jsxs("div", { className: "grid gap-2 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_130px_auto]", children: [_jsx(Input, { value: createSubject, onChange: (event) => setCreateSubject(event.target.value), placeholder: "\u4EFB\u52A1\u6807\u9898", "data-testid": "pm-task-create-subject", className: "h-9 border-white/10 bg-white/5 text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50" }), _jsx(Input, { value: createDescription, onChange: (event) => setCreateDescription(event.target.value), placeholder: "\u76EE\u6807 / \u63CF\u8FF0", "data-testid": "pm-task-create-description", className: "h-9 border-white/10 bg-white/5 text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50" }), _jsxs("select", { value: createPriority, onChange: (event) => setCreatePriority(event.target.value), "data-testid": "pm-task-create-priority", className: "h-9 rounded-md border border-white/10 bg-slate-950 px-2 text-xs text-slate-200 focus:border-amber-500/50 focus:outline-none", children: [_jsx("option", { value: "high", children: "high" }), _jsx("option", { value: "medium", children: "medium" }), _jsx("option", { value: "low", children: "low" })] }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => { void handleCreateTask(); }, disabled: createEvidence.loading || createTaskDisabled, "data-testid": "pm-task-create-submit", title: normalizedCreateDisabledReason || '提交 PM 任务', className: "border-amber-500/30 text-amber-200 hover:bg-amber-500/10", children: [createEvidence.loading ? _jsx(Loader2, { className: "mr-1.5 h-3.5 w-3.5 animate-spin" }) : _jsx(Plus, { className: "mr-1.5 h-3.5 w-3.5" }), "\u63D0\u4EA4"] })] }), _jsx("textarea", { value: createAcceptanceText, onChange: (event) => setCreateAcceptanceText(event.target.value), placeholder: "\u9A8C\u6536\u6807\u51C6\uFF0C\u6BCF\u884C\u4E00\u6761", "data-testid": "pm-task-create-acceptance", className: "mt-2 min-h-16 w-full resize-y rounded-md border border-white/10 bg-white/5 px-3 py-2 text-xs leading-5 text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none" })] })), (createEvidence.loading || createEvidence.error || createEvidence.task) && (_jsx("div", { className: cn('border-b px-4 py-2 text-xs', createEvidence.error
                            ? 'border-red-500/20 bg-red-500/10 text-red-100'
                            : 'border-amber-500/[0.15] bg-slate-950/45 text-slate-300'), "data-testid": "pm-task-create-evidence", "data-endpoint": "/v2/pm/tasks", children: _jsxs("div", { className: "flex flex-wrap items-center gap-x-3 gap-y-1", children: [_jsx("span", { className: "font-semibold text-amber-100", children: "PM task create" }), _jsx(EndpointBadge, { endpoint: "/v2/pm/tasks", method: "POST", testId: "pm-task-create-evidence-endpoint" }), createEvidence.loading ? (_jsx("span", { className: "text-slate-400", children: "\u6B63\u5728\u521B\u5EFA..." })) : createEvidence.error ? (_jsx("span", { className: "text-red-200", children: createEvidence.error })) : createEvidence.task ? (_jsxs("span", { className: "text-emerald-300", children: ["created \u00B7 ", createEvidence.task.id, " \u00B7 ", createEvidence.task.title] })) : null] }) })), showBackendSearch && (_jsxs("div", { className: "border-b border-white/10 bg-slate-950/20 px-4 py-2", "data-testid": "pm-task-search-panel", children: [_jsxs("div", { className: "mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500", children: [_jsx("span", { children: "\u540E\u7AEF\u4EFB\u52A1\u641C\u7D22" }), _jsx("span", { "data-testid": "pm-task-search-count", children: isTaskSearchLoading ? 'searching' : `${validTaskSearchResults.length} matches` })] }), isTaskSearchLoading ? (_jsxs("div", { className: "flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-400", children: [_jsx(Clock, { className: "h-3.5 w-3.5 animate-pulse text-amber-400" }), "\u6B63\u5728\u8C03\u7528 /v2/pm/search/tasks"] })) : taskSearchError ? (_jsx("div", { className: "rounded-md border border-red-500/20 bg-red-500/10 px-2 py-2 text-[11px] leading-relaxed text-red-200", "data-testid": "pm-task-search-error", children: taskSearchError })) : validTaskSearchResults.length > 0 ? (_jsx("div", { className: "grid gap-1 md:grid-cols-2", "data-testid": "pm-task-search-results", children: validTaskSearchResults.map((result, index) => (_jsx(TaskSearchResultRow, { result: result, onSelect: () => handleTaskSearchResultClick(result) }, `${readTaskSearchId(result)}-${index}`))) })) : (_jsx("div", { className: "rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-500", "data-testid": "pm-task-search-empty", children: "\u540E\u7AEF\u672A\u8FD4\u56DE\u5339\u914D\u4EFB\u52A1" }))] })), _jsx("div", { "data-testid": "pm-task-list", className: "flex-1 overflow-auto", children: filteredTasks.length === 0 ? (_jsxs("div", { "data-testid": "pm-task-empty", className: "h-full flex flex-col items-center justify-center text-slate-500", children: [_jsx(Filter, { className: "w-12 h-12 mb-4 opacity-20" }), _jsx("p", { className: "text-sm", children: "\u6682\u65E0\u4EFB\u52A1" }), _jsx("p", { className: "text-xs text-slate-600 mt-1", children: "\u4EFB\u52A1\u5C06\u663E\u793A\u5728\u8FD9\u91CC" })] })) : (_jsx("div", { "data-testid": "pm-task-list-content", className: "divide-y divide-white/5", children: filteredTasks.map((task) => (_jsx(TaskListItem, { task: task, selected: normalizedSelectedTaskId === normalizeTaskId(task.id), onClick: () => handleTaskClick(task), pmRunning: pmRunning, taskTraceMap: taskTraceMap, blueprintEvidence: blueprintEvidence }, normalizeTaskId(task.id) || taskDisplayTitle(task)))) })) })] }), selectedTask && (_jsx(TaskDetailPanel, { task: selectedTask, onClose: () => onTaskSelect(null), taskTraceMap: taskTraceMap, detailEvidence: taskDetailEvidence.taskId === normalizeTaskId(selectedTask.id) ? taskDetailEvidence : null, assignmentEvidence: assignmentEvidence.taskId === normalizeTaskId(selectedTask.id) ? assignmentEvidence : null }))] }));
}
function TaskSearchResultRow({ result, onSelect, }) {
    const task = normalizeTaskSearchResult(result);
    if (!task)
        return null;
    const summary = taskDisplaySummary(task) || 'PM backend returned a task match';
    return (_jsxs("button", { type: "button", onClick: onSelect, className: "cursor-pointer rounded-md border border-white/10 bg-white/[0.035] px-3 py-2 text-left transition-colors hover:border-amber-400/30 hover:bg-amber-500/10", "data-testid": "pm-task-search-result", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("p", { className: "min-w-0 flex-1 truncate text-xs font-medium text-slate-200", children: taskDisplayTitle(task) }), _jsx(StatusBadge, { status: task.status, done: task.done })] }), _jsx("p", { className: "mt-1 truncate text-[11px] text-slate-400", children: summary }), _jsx("p", { className: "mt-1 truncate text-[10px] text-slate-500", children: formatTaskSearchMeta(result) })] }));
}
function TaskStageChip({ icon, label, value, tone, }) {
    return (_jsxs("span", { className: cn('inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px]', stageToneClass(tone)), children: [icon, _jsx("span", { className: "font-medium text-slate-300", children: label }), _jsx("span", { className: "font-mono", children: value })] }));
}
function TaskListItem({ task, selected, onClick, pmRunning, taskTraceMap, blueprintEvidence }) {
    const status = task.status?.toLowerCase() || 'pending';
    const isRunning = status === 'running' || status === 'in_progress';
    const isCompleted = status === 'completed' || task.done;
    const isBlocked = status === 'blocked' || status === 'failed';
    const title = taskDisplayTitle(task);
    const summary = taskDisplaySummary(task);
    const ceStage = ceBlueprintStage(task, blueprintEvidence);
    const executionStage = directorStage(task);
    return (_jsxs("div", { onClick: onClick, "data-testid": "pm-task-item", "data-task-id": task.id, className: cn('group flex items-center gap-3 px-4 py-3 cursor-pointer transition-all duration-200', 
        // Running state: pulse animation + amber border highlight
        isRunning && pmRunning && 'animate-pulse border-l-4 border-amber-500 bg-amber-500/10', 
        // Completed state: subtle styling
        isCompleted && 'opacity-70', 
        // Blocked/Failed state: red border highlight
        isBlocked && 'border-l-4 border-red-500 bg-red-500/10', 
        // Selected state (when not running)
        selected && !isRunning && 'bg-amber-500/10 border-l-2 border-amber-500', 
        // Default hover state
        !selected && !isRunning && !isBlocked && 'hover:bg-white/5 border-l-2 border-transparent'), children: [_jsx("div", { className: "flex-shrink-0", children: isCompleted ? (_jsx("div", { className: "w-5 h-5 rounded-full bg-emerald-500/20 flex items-center justify-center", children: _jsx(CheckCircle2, { className: "w-3.5 h-3.5 text-emerald-400" }) })) : isRunning ? (_jsx("div", { className: "w-5 h-5 rounded-full bg-amber-500/20 flex items-center justify-center animate-pulse", children: _jsx(Play, { className: "w-3 h-3 text-amber-400" }) })) : isBlocked ? (_jsx("div", { className: "w-5 h-5 rounded-full bg-red-500/20 flex items-center justify-center", children: _jsx(AlertCircle, { className: "w-3.5 h-3.5 text-red-400" }) })) : (_jsx("div", { className: "w-5 h-5 rounded-full border-2 border-slate-600 group-hover:border-slate-500" })) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("p", { className: cn('text-sm font-medium truncate', isCompleted ? 'text-slate-500 line-through' : 'text-slate-200'), children: title }), task.priority !== undefined && (_jsx(PriorityBadge, { priority: task.priority }))] }), summary && (_jsx("p", { className: "text-xs text-slate-500 truncate mt-0.5", children: summary })), _jsxs("div", { "data-testid": "pm-task-flow", className: "mt-2 flex flex-wrap items-center gap-1.5", children: [_jsx(TaskStageChip, { icon: _jsx(ListChecks, { className: "h-3 w-3" }), label: "PM \u5408\u540C", value: "\u5DF2\u751F\u6210", tone: "ready" }), _jsx("span", { className: "font-mono text-[10px] text-slate-600", children: "\u2192" }), _jsx(TaskStageChip, { icon: _jsx(ShieldCheck, { className: "h-3 w-3" }), label: "Chief Engineer", value: ceStage.value, tone: ceStage.tone }), _jsx("span", { className: "font-mono text-[10px] text-slate-600", children: "\u2192" }), _jsx(TaskStageChip, { icon: _jsx(FileCode, { className: "h-3 w-3" }), label: "Director", value: executionStage.value, tone: executionStage.tone })] }), taskTraceMap?.has(task.id) && (_jsx(TaskTraceInline, { traces: taskTraceMap.get(task.id) || [], maxLines: 1, className: "mt-2" }))] })] }));
}
// Priority Badge Component
function PriorityBadge({ priority }) {
    // priority is number, lower = higher priority
    const configs = {
        0: { color: 'text-red-400 bg-red-500/10 border-red-500/20', label: 'P0' },
        1: { color: 'text-red-400 bg-red-500/10 border-red-500/20', label: 'P1' },
        2: { color: 'text-amber-400 bg-amber-500/10 border-amber-500/20', label: 'P2' },
        3: { color: 'text-amber-400 bg-amber-500/10 border-amber-500/20', label: 'P3' },
        4: { color: 'text-slate-400 bg-slate-500/10 border-slate-500/20', label: 'P4' },
        5: { color: 'text-slate-400 bg-slate-500/10 border-slate-500/20', label: 'P5' },
    };
    const numPriority = typeof priority === 'number' ? priority : parseInt(String(priority), 10) || 99;
    const config = configs[numPriority] || { color: 'text-slate-400 bg-slate-500/10 border-slate-500/20', label: `P${numPriority}` };
    return (_jsx(Badge, { variant: "outline", className: cn('text-[10px] px-1.5 py-0 h-4', config.color), children: config.label }));
}
function FilterButton({ children, active, count, onClick }) {
    return (_jsxs("button", { onClick: onClick, className: cn('px-2.5 py-1 rounded-md text-xs font-medium transition-all duration-200 flex items-center gap-1', active
            ? 'bg-amber-500/20 text-amber-400'
            : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'), children: [children, _jsx("span", { className: cn('text-[10px]', active ? 'text-amber-400/70' : 'text-slate-600'), children: count })] }));
}
function TaskDetailPanel({ task, onClose, taskTraceMap, detailEvidence = null, assignmentEvidence = null, }) {
    const blueprintId = readTaskString(task, ['blueprint_id', 'blueprintId']);
    const blueprintPath = readTaskString(task, ['blueprint_path', 'blueprintPath', 'runtime_blueprint_path']);
    const owner = readTaskString(task, ['assignee', 'assigned_to', 'assignedTo', 'assigned_worker', 'worker_id']);
    const source = readTaskString(task, ['source', 'director_task_source']) || 'pm_contract';
    const executionSteps = readTaskStringList(task, ['execution_checklist', 'execution_steps', 'executionSteps', 'steps', 'checklist']);
    const acceptanceCriteria = readAcceptanceCriteria(task);
    const targetFiles = readTaskStringList(task, ['target_files', 'targetFiles', 'scope_paths', 'files']);
    const dependencies = readTaskStringList(task, ['dependencies', 'blocked_by', 'blockedBy']);
    const qaContract = readTaskValue(task, ['qa_contract']);
    const title = taskDisplayTitle(task);
    const backendDetailSource = detailEvidence?.task
        ? readTaskString(detailEvidence.task, ['source']) || 'pm_task_detail'
        : '';
    return (_jsxs("div", { "data-testid": "pm-task-detail", className: "w-96 flex flex-col border-l border-white/10 bg-slate-950/30", children: [_jsxs("div", { className: "h-14 flex items-center justify-between px-4 border-b border-white/10", children: [_jsx("h3", { className: "text-sm font-semibold text-slate-200", children: "\u4EFB\u52A1\u8BE6\u60C5" }), _jsx(Button, { variant: "ghost", size: "sm", onClick: onClose, className: "text-slate-400 hover:text-slate-200", children: "\u5173\u95ED" })] }), _jsxs("div", { className: "flex-1 overflow-auto p-4 space-y-4", children: [_jsxs("div", { children: [_jsx("label", { className: "text-xs text-slate-500 uppercase tracking-wider", children: "\u6807\u9898" }), _jsx("p", { className: "text-sm text-slate-200 mt-1", children: title })] }), _jsxs("div", { children: [_jsx("label", { className: "text-xs text-slate-500 uppercase tracking-wider", children: "\u72B6\u6001" }), _jsx("div", { className: "flex items-center gap-2 mt-1", children: _jsx(StatusBadge, { status: task.status || 'pending', done: task.done }) })] }), task.priority !== undefined && (_jsxs("div", { children: [_jsx("label", { className: "text-xs text-slate-500 uppercase tracking-wider", children: "\u4F18\u5148\u7EA7" }), _jsx("div", { className: "mt-1", children: _jsx(PriorityBadge, { priority: task.priority }) })] })), task.goal && (_jsxs("div", { children: [_jsx("label", { className: "text-xs text-slate-500 uppercase tracking-wider", children: "\u76EE\u6807" }), _jsx("p", { className: "text-sm text-slate-300 mt-1 whitespace-pre-wrap", children: task.goal })] })), task.summary && (_jsxs("div", { children: [_jsx("label", { className: "text-xs text-slate-500 uppercase tracking-wider", children: "\u6458\u8981" }), _jsx("p", { className: "text-sm text-slate-300 mt-1 whitespace-pre-wrap", children: task.summary })] })), _jsxs("div", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", "data-testid": "pm-task-detail-provenance", children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(Target, { className: "h-3.5 w-3.5 text-amber-300" }), "\u5408\u540C\u6765\u6E90"] }), _jsxs("div", { className: "flex flex-wrap gap-1.5 text-[10px] text-slate-300", children: [_jsx(DetailChip, { label: "PM", value: task.id }), _jsx(DetailChip, { label: "Source", value: source }), _jsx(DetailChip, { label: "Owner", value: owner || '未分配' }), _jsx(DetailChip, { label: "Blueprint", value: blueprintId || blueprintPath || '未绑定' })] }), blueprintPath ? (_jsx("div", { className: "mt-2 truncate rounded-md border border-cyan-400/20 bg-cyan-500/5 px-2 py-1 text-[11px] text-cyan-100", title: blueprintPath, children: blueprintPath })) : null] }), _jsxs("div", { className: "rounded-lg border border-cyan-400/20 bg-cyan-500/5 p-3", "data-testid": "pm-task-backend-detail", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(FileCode, { className: "h-3.5 w-3.5 text-cyan-300" }), _jsx("span", { children: "\u540E\u7AEF\u4EFB\u52A1\u8BE6\u60C5" })] }), _jsx(EndpointBadge, { endpoint: `/v2/pm/tasks/${task.id}`, testId: "pm-task-backend-detail-endpoint" })] }), detailEvidence?.loading ? (_jsxs("div", { className: "flex items-center gap-2 rounded-md border border-cyan-500/20 bg-cyan-500/10 px-2 py-2 text-[11px] text-cyan-100", children: [_jsx(Clock, { className: "h-3.5 w-3.5 animate-pulse" }), "\u6B63\u5728\u8BFB\u53D6\u540E\u7AEF PM \u4EFB\u52A1\u8BE6\u60C5..."] })) : detailEvidence?.error ? (_jsxs("div", { className: "flex items-center gap-2 rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-2 text-[11px] text-amber-100", children: [_jsx(AlertCircle, { className: "h-3.5 w-3.5" }), _jsx("span", { children: detailEvidence.error })] })) : detailEvidence?.task ? (_jsxs("div", { className: "grid grid-cols-2 gap-2 text-[11px] text-slate-300", children: [_jsx(DetailChip, { label: "Hydrated", value: detailEvidence.task.id }), _jsx(DetailChip, { label: "Source", value: backendDetailSource || 'pm_task_detail' }), _jsx(DetailChip, { label: "Status", value: String(detailEvidence.task.status || 'pending') }), _jsx(DetailChip, { label: "Priority", value: String(detailEvidence.task.priority ?? 'unknown') })] })) : (_jsx("div", { className: "rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-500", children: "\u9009\u62E9\u4EFB\u52A1\u540E\u8BFB\u53D6\u540E\u7AEF\u8BE6\u60C5\u5408\u540C" }))] }), _jsxs("div", { className: "rounded-lg border border-purple-400/20 bg-purple-500/5 p-3", "data-testid": "pm-task-assignments-panel", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(GitBranch, { className: "h-3.5 w-3.5 text-purple-300" }), _jsx("span", { children: "\u5206\u914D\u5386\u53F2" })] }), _jsx(EndpointBadge, { endpoint: `/v2/pm/tasks/${task.id}/assignments`, testId: "pm-task-assignments-endpoint" })] }), assignmentEvidence?.loading ? (_jsxs("div", { className: "flex items-center gap-2 rounded-md border border-purple-500/20 bg-purple-500/10 px-2 py-2 text-[11px] text-purple-100", children: [_jsx(Clock, { className: "h-3.5 w-3.5 animate-pulse" }), "\u6B63\u5728\u8BFB\u53D6 PM \u4EFB\u52A1\u5206\u914D\u5386\u53F2..."] })) : assignmentEvidence?.error ? (_jsxs("div", { className: "flex items-center gap-2 rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-2 text-[11px] text-amber-100", children: [_jsx(AlertCircle, { className: "h-3.5 w-3.5" }), _jsx("span", { children: assignmentEvidence.error })] })) : assignmentEvidence && assignmentEvidence.assignments.length > 0 ? (_jsxs("div", { className: "space-y-1.5", children: [_jsxs("div", { className: "flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500", children: [_jsx("span", { children: "Assignment Evidence" }), _jsxs("span", { "data-testid": "pm-task-assignment-count", children: [assignmentEvidence.count, " records"] })] }), assignmentEvidence.assignments.slice(0, 5).map((assignment, index) => {
                                        const identity = assignmentIdentity(assignment);
                                        const state = assignmentState(assignment);
                                        const time = assignmentTime(assignment);
                                        const key = readAssignmentString(assignment, ['id']) || `${identity}-${state}-${index}`;
                                        return (_jsxs("div", { className: "rounded-md border border-white/10 bg-slate-950/45 px-2 py-1.5 text-[11px]", "data-testid": "pm-task-assignment-row", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "min-w-0 truncate text-slate-200", children: identity }), _jsx("span", { className: "shrink-0 rounded bg-purple-500/10 px-1.5 py-0.5 text-[10px] text-purple-100", children: state })] }), time ? _jsx("div", { className: "mt-1 truncate text-[10px] text-slate-500", children: time }) : null] }, key));
                                    })] })) : (_jsx("div", { className: "rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-500", children: "\u540E\u7AEF\u672A\u8FD4\u56DE\u4EFB\u52A1\u5206\u914D\u5386\u53F2" }))] }), _jsx(TaskContractSection, { icon: _jsx(ListChecks, { className: "h-3.5 w-3.5 text-blue-300" }), title: "\u6267\u884C\u6B65\u9AA4", items: executionSteps, emptyText: "PM \u5408\u540C\u672A\u63D0\u4F9B\u6267\u884C\u6B65\u9AA4" }), _jsx(TaskContractSection, { icon: _jsx(ShieldCheck, { className: "h-3.5 w-3.5 text-emerald-300" }), title: "\u9A8C\u6536\u6807\u51C6", items: acceptanceCriteria, emptyText: "PM \u5408\u540C\u672A\u63D0\u4F9B\u9A8C\u6536\u6807\u51C6" }), _jsx(TaskContractSection, { icon: _jsx(FileCode, { className: "h-3.5 w-3.5 text-cyan-300" }), title: "\u76EE\u6807\u6587\u4EF6/\u4F5C\u7528\u57DF", items: targetFiles, emptyText: "PM \u5408\u540C\u672A\u58F0\u660E\u76EE\u6807\u6587\u4EF6" }), _jsx(TaskContractSection, { icon: _jsx(GitBranch, { className: "h-3.5 w-3.5 text-amber-300" }), title: "\u4F9D\u8D56/\u963B\u585E", items: dependencies, emptyText: "\u65E0\u4F9D\u8D56\u6216\u963B\u585E\u58F0\u660E" }), qaContract && typeof qaContract === 'object' && Object.keys(qaContract).length > 0 ? (_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-medium text-slate-200", children: [_jsx(ShieldCheck, { className: "h-3.5 w-3.5 text-emerald-300" }), "QA \u5408\u540C"] }), _jsx("pre", { className: "max-h-40 overflow-auto rounded-md border border-white/10 bg-slate-950/70 p-2 text-[10px] text-slate-400", children: JSON.stringify(qaContract, null, 2) })] })) : null, taskTraceMap?.has(task.id) && (_jsx("div", { className: "pt-4 border-t border-white/10", children: _jsx(TaskTraceTimeline, { traces: taskTraceMap.get(task.id) || [], maxTraces: 20, expanded: true }) })), _jsxs("div", { className: "pt-4 border-t border-white/10", children: [_jsx("label", { className: "text-xs text-slate-500 uppercase tracking-wider", children: "\u539F\u59CB\u6570\u636E" }), _jsx("pre", { className: "mt-2 p-3 rounded-lg bg-slate-950 border border-white/10 text-[10px] text-slate-500 font-mono overflow-auto", children: JSON.stringify(task, null, 2) })] })] })] }));
}
function TaskContractSection({ icon, title, items, emptyText, }) {
    return (_jsxs("section", { className: "rounded-lg border border-white/10 bg-white/[0.035] p-3", children: [_jsxs("div", { className: "mb-2 flex items-center gap-2 text-xs font-medium text-slate-200", children: [icon, title] }), items.length === 0 ? (_jsx("div", { className: "text-[11px] text-slate-500", children: emptyText })) : (_jsx("ul", { className: "space-y-1.5", children: items.map((item, index) => (_jsxs("li", { className: "flex gap-2 text-[11px] leading-5 text-slate-300", children: [_jsx("span", { className: "mt-2 h-1 w-1 shrink-0 rounded-full bg-amber-400" }), _jsx("span", { className: "break-words", children: item })] }, `${title}-${index}`))) }))] }));
}
function DetailChip({ label, value }) {
    return (_jsxs("span", { className: "max-w-full truncate rounded-md border border-white/10 bg-slate-950/55 px-2 py-1", title: `${label}: ${value}`, children: [_jsx("span", { className: "text-slate-500", children: label }), _jsx("span", { className: "mx-1 text-slate-600", children: "\u00B7" }), _jsx("span", { children: value })] }));
}
// Status Badge Component
function StatusBadge({ status, done }) {
    const configs = {
        pending: { icon: Circle, color: 'text-slate-400 bg-slate-500/10 border-slate-500/20', label: '待办' },
        running: { icon: Play, color: 'text-amber-400 bg-amber-500/10 border-amber-500/20', label: '进行中' },
        in_progress: { icon: Play, color: 'text-amber-400 bg-amber-500/10 border-amber-500/20', label: '进行中' },
        completed: { icon: CheckCircle2, color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20', label: '已完成' },
        blocked: { icon: AlertCircle, color: 'text-red-400 bg-red-500/10 border-red-500/20', label: '阻塞' },
        failed: { icon: AlertCircle, color: 'text-red-400 bg-red-500/10 border-red-500/20', label: '失败' },
    };
    const config = configs[status] || configs.pending;
    const Icon = config.icon;
    if (done) {
        return (_jsxs(Badge, { variant: "outline", className: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20", children: [_jsx(CheckCircle2, { className: "w-3 h-3 mr-1" }), "\u5DF2\u5B8C\u6210"] }));
    }
    return (_jsxs(Badge, { variant: "outline", className: config.color, children: [_jsx(Icon, { className: "w-3 h-3 mr-1" }), config.label] }));
}
