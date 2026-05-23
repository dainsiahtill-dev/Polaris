import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Brain,
  CheckCircle2,
  ChevronLeft,
  FileCode,
  FilePlus,
  FileText,
  Hammer,
  Loader2,
  MessageSquare,
  Play,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { AIDialoguePanel } from '@/app/components/ai-dialogue';
import {
  generateChiefEngineerBlueprint,
  getChiefEngineerDiagnostics,
  getChiefEngineerBlueprint,
  getChiefEngineerBlueprintStatus,
  listChiefEngineerBlueprints,
} from '@/services/chiefEngineerService';
import {
  getRoleCapabilities,
  resolveRoleCapabilities,
} from '@/services/roleSessionService';
import {
  clearRoleKernelCache,
  getDirectorStatus,
  getRoleKernelCacheStats,
  getRoleKernelLLMEvents,
  getRoleKernelTokenBudgetStats,
  listDirectorTaskFallbackRows,
  listDirectorWorkers,
  type DirectorStatus,
  type DirectorFallbackTaskRow,
  type DirectorWorker,
  type RoleKernelCacheStats,
  type RoleKernelLLMEvent,
  type RoleKernelLLMEventsResponse,
  type RoleKernelTokenBudgetStats,
} from '@/services';
import type {
  ChiefEngineerDiagnosticsResponse,
  ChiefEngineerTaskBlueprintResultResponse,
} from '@/services/chiefEngineerService';
import type { EngineStatus } from '@/app/types/appContracts';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntime';
import type { PmTask } from '@/types/task';
import type {
  ChiefEngineerBlueprintDetailV1,
  ChiefEngineerBlueprintSummaryV1,
} from '@/types/roleContracts';

interface ChiefEngineerWorkspaceProps {
  workspace: string;
  tasks: PmTask[];
  workers: RuntimeWorkerState[];
  pmState: Record<string, unknown> | null;
  engineStatus: EngineStatus | null;
  directorRunning: boolean;
  isStartingDirector?: boolean;
  onBackToMain: () => void;
  onEnterDirectorWorkspace: () => void;
  onToggleDirector: () => void | boolean | Promise<void | boolean>;
  onOpenSettings?: () => void;
}

interface BlueprintEvidence {
  taskId: string;
  taskTitle: string;
  blueprintId: string;
  blueprintPath: string;
  source: string;
  summary: string;
  targetFiles: string[];
}

type RuntimeBlueprintSummary = ChiefEngineerBlueprintSummaryV1;
type RuntimeBlueprintDetailResponse = ChiefEngineerBlueprintDetailV1;
type DiagnosticsResponse = ChiefEngineerDiagnosticsResponse;
type DiagnosticTone = 'ready' | 'degraded' | 'error' | 'checking';
type TaskEvidenceRow = PmTask | DirectorFallbackTaskRow;

interface BlueprintStatusCheckState {
  loading: boolean;
  error: string;
  result: ChiefEngineerTaskBlueprintResultResponse | null;
}

interface DirectorToggleStatusEvidence {
  triggered: boolean;
  loading: boolean;
  data: DirectorStatus | null;
  error: string | null;
}

function normalizeToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function metadataOf(task: TaskEvidenceRow): Record<string, unknown> {
  return task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
}

function readString(task: TaskEvidenceRow, keys: string[]): string {
  const metadata = metadataOf(task);
  const direct = task as unknown as Record<string, unknown>;
  for (const key of keys) {
    const value = direct[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    const metaValue = metadata[key];
    if (typeof metaValue === 'string' && metaValue.trim()) return metaValue.trim();
  }
  return '';
}

function readStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === 'string') return item.trim();
      if (item && typeof item === 'object') {
        const record = item as Record<string, unknown>;
        return String(record.path || record.file || record.name || record.title || record.id || '').trim();
      }
      return String(item || '').trim();
    })
    .filter(Boolean);
}

function readTaskStringList(task: TaskEvidenceRow, keys: string[]): string[] {
  const metadata = metadataOf(task);
  const direct = task as unknown as Record<string, unknown>;
  for (const key of keys) {
    const directList = readStringList(direct[key]);
    if (directList.length > 0) return directList;
    const metadataList = readStringList(metadata[key]);
    if (metadataList.length > 0) return metadataList;
  }
  return [];
}

function readWorkerText(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return '';
}

function readWorkerNumber(record: Record<string, unknown>, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return undefined;
}

function readWorkerBoolean(record: Record<string, unknown>, keys: string[]): boolean | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string' && value.trim()) {
      const normalized = value.trim().toLowerCase();
      if (['true', 'healthy', 'ok', 'ready'].includes(normalized)) return true;
      if (['false', 'unhealthy', 'failed', 'error'].includes(normalized)) return false;
    }
  }
  return undefined;
}

function normalizeDirectorWorkerRows(rows: DirectorWorker[] | null | undefined): RuntimeWorkerState[] {
  if (!Array.isArray(rows)) {
    return [];
  }
  return rows
    .map((row): RuntimeWorkerState | null => {
      if (!row || typeof row !== 'object') return null;
      const record = row as Record<string, unknown>;
      const id = readWorkerText(record, ['id', 'worker_id', 'name']);
      if (!id) return null;
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
    .filter((row): row is RuntimeWorkerState => Boolean(row));
}

function mergeDirectorWorkers(
  realtimeWorkers: RuntimeWorkerState[],
  backendWorkers: RuntimeWorkerState[],
): RuntimeWorkerState[] {
  const merged = new Map<string, RuntimeWorkerState>();
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

function taskTitle(task: TaskEvidenceRow): string {
  return readString(task, ['title', 'subject', 'goal', 'summary']) || String(task.id || '未命名任务');
}

function taskObjective(task: TaskEvidenceRow): string {
  return readString(task, ['goal', 'description', 'summary', 'subject', 'title']) || taskTitle(task);
}

function taskHandoffId(task: TaskEvidenceRow): string {
  return readString(task, ['pm_task_id', 'pmTaskId', 'task_id', 'taskId', 'id']) || String(task.id || '').trim();
}

function taskHasBlueprintEvidence(task: TaskEvidenceRow): boolean {
  return Boolean(readString(task, ['blueprint_id', 'blueprintId', 'blueprint_path', 'runtime_blueprint_path']));
}

function taskStatus(task: TaskEvidenceRow): 'unclaimed' | 'running' | 'blocked' | 'failed' | 'completed' {
  const status = normalizeToken(task.status || task.state);
  const direct = task as unknown as Record<string, unknown>;
  if (direct.done || direct.completed || ['completed', 'done', 'success', 'passed'].includes(status)) return 'completed';
  if (['failed', 'error'].includes(status)) return 'failed';
  if (['blocked', 'cancelled', 'canceled'].includes(status)) return 'blocked';
  if (['running', 'in_progress', 'claimed', 'pending_exec'].includes(status)) return 'running';
  return 'unclaimed';
}

function buildBlueprintEvidence(tasks: TaskEvidenceRow[]): BlueprintEvidence[] {
  return tasks
    .map((task) => {
      const blueprintId = readString(task, ['blueprint_id', 'blueprintId']);
      const blueprintPath = readString(task, ['blueprint_path', 'runtime_blueprint_path']);
      const summary = readString(task, ['blueprint_summary', 'summary', 'goal']);
      if (!blueprintId && !blueprintPath) return null;
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
    .filter((item): item is BlueprintEvidence => Boolean(item));
}

function runtimeBlueprintTaskId(row: RuntimeBlueprintSummary): string {
  const raw = row.raw && typeof row.raw === 'object' ? row.raw as Record<string, unknown> : {};
  return String(raw.task_id || raw.pm_task_id || raw.taskId || '').trim();
}

function buildRuntimeBlueprintEvidence(rows: RuntimeBlueprintSummary[]): BlueprintEvidence[] {
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

function mergeTaskEvidenceRows(
  liveTasks: PmTask[],
  backendTasks: DirectorFallbackTaskRow[],
): TaskEvidenceRow[] {
  const merged = new Map<string, TaskEvidenceRow>();
  for (const task of backendTasks) {
    const id = String(task.id || '').trim();
    if (id) {
      merged.set(id, task);
    }
  }
  for (const task of liveTasks) {
    const id = String(task.id || '').trim();
    if (id) {
      merged.set(id, task);
    }
  }
  return Array.from(merged.values());
}

function roleStatus(engineStatus: EngineStatus | null, role: string): string {
  const roles = engineStatus?.roles;
  const rolePayload = roles?.[role] || roles?.[role.toLowerCase()];
  return String(rolePayload?.status || '').trim();
}

function readRecordNumber(record: Record<string, unknown> | null | undefined, keys: string[]): number | undefined {
  if (!record) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return undefined;
}

function readKernelEventString(event: RoleKernelLLMEvent | null | undefined, keys: string[]): string {
  if (!event) return '';
  for (const key of keys) {
    const value = event[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return '';
}

function formatChiefKernelEvent(event: RoleKernelLLMEvent | null | undefined): string {
  if (!event) return 'events=0';
  const eventType = readKernelEventString(event, ['event_type', 'type']) || 'event';
  const model = readKernelEventString(event, ['model', 'model_name']);
  const tokens = readRecordNumber(event, ['tokens', 'token_count', 'total_tokens']);
  return [
    eventType,
    model,
    typeof tokens === 'number' ? `${tokens} tokens` : '',
  ].filter(Boolean).join(' · ');
}

function formatChiefCacheStats(stats: RoleKernelCacheStats | null): string {
  if (!stats) return 'unavailable';
  const hits = readRecordNumber(stats, ['hits']) ?? 0;
  const misses = readRecordNumber(stats, ['misses']) ?? 0;
  const size = readRecordNumber(stats, ['size']) ?? 0;
  const hitRate = readRecordNumber(stats, ['hit_rate']);
  return `hits=${hits} · misses=${misses} · size=${size}${typeof hitRate === 'number' ? ` · hit=${hitRate}%` : ''}`;
}

function formatChiefTokenBudget(stats: RoleKernelTokenBudgetStats | null): string {
  if (!stats) return 'unavailable';
  const total = readRecordNumber(stats, ['total', 'total_budget']);
  const available = readRecordNumber(stats, ['available_conversation', 'remaining']);
  const used = readRecordNumber(stats, ['used_tokens']);
  return [
    typeof total === 'number' ? `total=${total}` : '',
    typeof available === 'number' ? `available=${available}` : '',
    typeof used === 'number' ? `used=${used}` : '',
  ].filter(Boolean).join(' · ') || 'stats ready';
}

function diagnosticsTone(diagnostics: DiagnosticsResponse | null, error: string): DiagnosticTone {
  if (error) return 'error';
  if (!diagnostics) return 'checking';
  return diagnostics.ok ? 'ready' : 'degraded';
}

function blueprintSummaryFromResult(
  result: ChiefEngineerTaskBlueprintResultResponse,
): RuntimeBlueprintSummary | null {
  const blueprintId = String(result.blueprint_id || '').trim();
  if (!blueprintId) return null;
  const raw = result.blueprint && typeof result.blueprint === 'object' ? result.blueprint : {};
  const rawRecord = raw as Record<string, unknown>;
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

export function ChiefEngineerWorkspace({
  workspace,
  tasks,
  workers,
  pmState,
  engineStatus,
  directorRunning,
  isStartingDirector,
  onBackToMain,
  onEnterDirectorWorkspace,
  onToggleDirector,
  onOpenSettings,
}: ChiefEngineerWorkspaceProps) {
  const [runtimeBlueprints, setRuntimeBlueprints] = useState<RuntimeBlueprintSummary[]>([]);
  const [blueprintApiError, setBlueprintApiError] = useState('');
  const [diagnostics, setDiagnostics] = useState<DiagnosticsResponse | null>(null);
  const [diagnosticsError, setDiagnosticsError] = useState('');
  const [chiefCapabilities, setChiefCapabilities] = useState<string[]>([]);
  const [chiefCapabilitiesError, setChiefCapabilitiesError] = useState('');
  const [chiefLLMEvents, setChiefLLMEvents] = useState<RoleKernelLLMEventsResponse | null>(null);
  const [chiefLLMEventsError, setChiefLLMEventsError] = useState('');
  const [chiefKernelCacheStats, setChiefKernelCacheStats] = useState<RoleKernelCacheStats | null>(null);
  const [chiefKernelCacheError, setChiefKernelCacheError] = useState('');
  const [chiefKernelCacheClearStatus, setChiefKernelCacheClearStatus] = useState('');
  const [chiefKernelCacheClearing, setChiefKernelCacheClearing] = useState(false);
  const [chiefKernelTokenBudgetStats, setChiefKernelTokenBudgetStats] = useState<RoleKernelTokenBudgetStats | null>(null);
  const [chiefKernelTokenBudgetError, setChiefKernelTokenBudgetError] = useState('');
  const [chiefBackendEvidenceLoading, setChiefBackendEvidenceLoading] = useState(false);
  const [selectedBlueprintId, setSelectedBlueprintId] = useState('');
  const [blueprintDetail, setBlueprintDetail] = useState<RuntimeBlueprintDetailResponse | null>(null);
  const [blueprintDetailError, setBlueprintDetailError] = useState('');
  const [blueprintDetailLoading, setBlueprintDetailLoading] = useState(false);
  const [generatingTaskId, setGeneratingTaskId] = useState('');
  const [generateError, setGenerateError] = useState('');
  const [blueprintStatusChecks, setBlueprintStatusChecks] = useState<Record<string, BlueprintStatusCheckState>>({});
  const [backendDirectorTasks, setBackendDirectorTasks] = useState<DirectorFallbackTaskRow[]>([]);
  const [directorTaskApiError, setDirectorTaskApiError] = useState('');
  const [directorTaskLoading, setDirectorTaskLoading] = useState(false);
  const [backendDirectorWorkers, setBackendDirectorWorkers] = useState<RuntimeWorkerState[]>([]);
  const [directorWorkerApiError, setDirectorWorkerApiError] = useState('');
  const [directorWorkerLoading, setDirectorWorkerLoading] = useState(false);
  const [directorToggleStatusEvidence, setDirectorToggleStatusEvidence] = useState<DirectorToggleStatusEvidence>({
    triggered: false,
    loading: false,
    data: null,
    error: null,
  });
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
        listChiefEngineerBlueprints(),
        getChiefEngineerDiagnostics(),
      ]);
      if (cancelled) {
        return;
      }
      if (blueprintResult.ok && blueprintResult.data) {
        setRuntimeBlueprints(Array.isArray(blueprintResult.data.blueprints) ? blueprintResult.data.blueprints : []);
        setBlueprintApiError('');
      } else {
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
          getRoleKernelLLMEvents('chief_engineer', { limit: 5 }),
          getRoleKernelCacheStats('chief_engineer'),
          getRoleKernelTokenBudgetStats('chief_engineer'),
        ]);
        if (cancelled) {
          return;
        }

        if (capabilityResult.ok && capabilityResult.data) {
          setChiefCapabilities(resolveRoleCapabilities(capabilityResult.data, 'electron_workbench').sort());
          setChiefCapabilitiesError('');
        } else {
          setChiefCapabilities([]);
          setChiefCapabilitiesError(capabilityResult.error || 'Chief Engineer capabilities unavailable');
        }

        if (llmResult.ok && llmResult.data) {
          setChiefLLMEvents({
            ...llmResult.data,
            events: Array.isArray(llmResult.data.events) ? llmResult.data.events : [],
          });
          setChiefLLMEventsError('');
        } else {
          setChiefLLMEvents(null);
          setChiefLLMEventsError(llmResult.error || 'Chief Engineer LLM events unavailable');
        }

        if (cacheResult.ok && cacheResult.data) {
          setChiefKernelCacheStats(cacheResult.data);
          setChiefKernelCacheError('');
        } else {
          setChiefKernelCacheStats(null);
          setChiefKernelCacheError(cacheResult.error || 'Chief Engineer cache stats unavailable');
        }

        if (tokenBudgetResult.ok && tokenBudgetResult.data) {
          setChiefKernelTokenBudgetStats(tokenBudgetResult.data);
          setChiefKernelTokenBudgetError('');
        } else {
          setChiefKernelTokenBudgetStats(null);
          setChiefKernelTokenBudgetError(tokenBudgetResult.error || 'Chief Engineer token budget unavailable');
        }
      } catch (err) {
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
      } finally {
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
      setBackendDirectorTasks([]);
      setDirectorTaskApiError('');
      setDirectorTaskLoading(false);
      setBackendDirectorWorkers([]);
      setDirectorWorkerApiError('');
      setDirectorWorkerLoading(false);
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const syncDirectorWorkers = async () => {
      setDirectorWorkerLoading(true);
      try {
        const result = await listDirectorWorkers();
        if (cancelled) {
          return;
        }
        if (result.ok && Array.isArray(result.data)) {
          setBackendDirectorWorkers(normalizeDirectorWorkerRows(result.data));
          setDirectorWorkerApiError('');
        } else {
          setDirectorWorkerApiError(result.error || 'Director worker backend unavailable');
        }
      } catch (err) {
        if (!cancelled) {
          setDirectorWorkerApiError(err instanceof Error ? err.message : 'Director worker backend unavailable');
        }
      } finally {
        if (!cancelled) {
          setDirectorWorkerLoading(false);
        }
      }
    };

    const syncDirectorTasks = async () => {
      setDirectorTaskLoading(true);
      try {
        const result = await listDirectorTaskFallbackRows(directorRunning);
        if (cancelled) {
          return;
        }
        if (result.ok && Array.isArray(result.data)) {
          setBackendDirectorTasks(result.data);
          setDirectorTaskApiError('');
        } else {
          setDirectorTaskApiError(result.error || 'Director task backend unavailable');
        }
      } catch (err) {
        if (!cancelled) {
          setDirectorTaskApiError(err instanceof Error ? err.message : 'Director task backend unavailable');
        }
      } finally {
        if (!cancelled) {
          setDirectorTaskLoading(false);
        }
      }
    };

    void syncDirectorTasks();
    void syncDirectorWorkers();
    timer = setInterval(() => {
      void syncDirectorTasks();
      void syncDirectorWorkers();
    }, directorRunning ? 2500 : 6000);

    return () => {
      cancelled = true;
      if (timer) {
        clearInterval(timer);
      }
    };
  }, [workspace, directorRunning]);

  const directorTaskEvidenceRows = useMemo(
    () => mergeTaskEvidenceRows(tasks, backendDirectorTasks),
    [tasks, backendDirectorTasks],
  );
  const taskBlueprintEvidence = useMemo(
    () => buildBlueprintEvidence(directorTaskEvidenceRows),
    [directorTaskEvidenceRows],
  );
  const blueprintEvidence = useMemo(() => {
    const byKey = new Map<string, BlueprintEvidence>();
    for (const item of buildRuntimeBlueprintEvidence(runtimeBlueprints)) {
      byKey.set(item.blueprintId || item.blueprintPath || item.taskId, item);
    }
    for (const item of taskBlueprintEvidence) {
      const key = item.blueprintId || item.blueprintPath || item.taskId;
      if (!byKey.has(key)) {
        byKey.set(key, item);
      }
    }
    return Array.from(byKey.values());
  }, [runtimeBlueprints, taskBlueprintEvidence]);
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
  const directorRows = useMemo(
    () => mergeDirectorWorkers(
      workers.filter((worker) => worker && typeof worker === 'object'),
      backendDirectorWorkers,
    ),
    [workers, backendDirectorWorkers],
  );
  const lastDirectorStatus = String(pmState?.last_director_status || '').trim();
  const missingBlueprintHandoffTasks = useMemo(
    () => {
      const evidenceTaskIds = new Set(
        blueprintEvidence
          .map((item) => String(item.taskId || '').trim())
          .filter(Boolean),
      );
      const seen = new Set<string>();
      return directorTaskEvidenceRows
        .filter((task) => {
          const taskId = taskHandoffId(task);
          if (!taskId || seen.has(taskId)) return false;
          if (taskHasBlueprintEvidence(task) || evidenceTaskIds.has(taskId) || taskStatus(task) === 'completed') {
            return false;
          }
          seen.add(taskId);
          return true;
        })
    },
    [blueprintEvidence, directorTaskEvidenceRows],
  );
  const startDirectorBlocked = !directorRunning && missingBlueprintHandoffTasks.length > 0;
  const blueprintCandidateTasks = useMemo(
    () => missingBlueprintHandoffTasks.slice(0, 4),
    [missingBlueprintHandoffTasks],
  );
  const diagnosticsState = diagnosticsTone(diagnostics, diagnosticsError);
  const workspaceDiagnosticTone: DiagnosticTone = !diagnostics ? 'checking' : diagnostics.workspace.ok ? 'ready' : 'error';
  const blueprintDiagnosticTone: DiagnosticTone = !diagnostics
    ? 'checking'
    : diagnostics.blueprints.ok
      ? diagnostics.blueprints.status === 'empty'
        ? 'degraded'
        : 'ready'
      : 'error';
  const handoffDiagnosticTone: DiagnosticTone = !diagnostics
    ? 'checking'
    : diagnostics.blueprints.director_handoff_ready
      ? 'ready'
      : 'degraded';

  const refreshChiefEngineerDiagnostics = async () => {
    if (!workspace) {
      setDiagnostics(null);
      setDiagnosticsError('');
      return;
    }

    const diagnosticsResult = await getChiefEngineerDiagnostics();
    if (diagnosticsResult.ok && diagnosticsResult.data) {
      setDiagnostics(diagnosticsResult.data);
      setDiagnosticsError('');
      return;
    }
    setDiagnostics(null);
    setDiagnosticsError(diagnosticsResult.error || '诊断 API 暂不可用');
  };

  const loadBlueprintDetail = async (blueprintId: string) => {
    const token = String(blueprintId || '').trim();
    if (!token) return;
    setSelectedBlueprintId(token);
    setBlueprintDetail(null);
    setBlueprintDetailError('');
    setBlueprintDetailLoading(true);
    const result = await getChiefEngineerBlueprint(token);
    if (result.ok && result.data) {
      setBlueprintDetail(result.data);
    } else {
      setBlueprintDetailError(result.error || '蓝图详情 API 暂不可用');
    }
    setBlueprintDetailLoading(false);
  };

  const handleGenerateBlueprint = async (task: TaskEvidenceRow) => {
    const taskId = taskHandoffId(task);
    if (!taskId) return;
    setGeneratingTaskId(taskId);
    setGenerateError('');
    const result = await generateChiefEngineerBlueprint({
      task_id: taskId,
      objective: taskObjective(task),
      context: {
        source: 'chief_engineer_desktop',
        task_title: taskTitle(task),
        goal: readString(task, ['goal']),
        summary: readString(task, ['summary']),
        acceptance: readTaskStringList(task, ['acceptance']),
        target_files: readTaskStringList(task, ['target_files', 'scope_paths', 'files']),
      },
    });
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

  const handleCheckBlueprintStatus = async (task: TaskEvidenceRow) => {
    const taskId = taskHandoffId(task);
    if (!taskId) return;
    setBlueprintStatusChecks((current) => ({
      ...current,
      [taskId]: {
        loading: true,
        error: '',
        result: current[taskId]?.result ?? null,
      },
    }));
    const result = await getChiefEngineerBlueprintStatus(taskId);
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
    } else {
      setChiefKernelCacheStats(null);
      setChiefKernelCacheError(statsResult.error || 'Chief Engineer cache stats unavailable');
    }
    setChiefKernelCacheClearing(false);
  };

  const handleToggleDirector = async () => {
    setDirectorToggleStatusEvidence({
      triggered: true,
      loading: true,
      data: null,
      error: null,
    });
    try {
      await Promise.resolve(onToggleDirector());
      const statusResult = await getDirectorStatus();
      if (statusResult.ok && statusResult.data) {
        setDirectorToggleStatusEvidence({
          triggered: true,
          loading: false,
          data: statusResult.data,
          error: null,
        });
        return;
      }
      setDirectorToggleStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: statusResult.error || 'Director status unavailable',
      });
    } catch (error) {
      setDirectorToggleStatusEvidence({
        triggered: true,
        loading: false,
        data: null,
        error: error instanceof Error ? error.message : 'Director status unavailable',
      });
    }
  };

  const latestChiefLLMEvent = chiefLLMEvents?.events[0] ?? null;
  const chiefLLMEventCount = chiefLLMEvents?.count
    ?? readRecordNumber(chiefLLMEvents?.stats, ['total'])
    ?? chiefLLMEvents?.events.length
    ?? 0;
  const directorToggleBusy = Boolean(isStartingDirector || directorToggleStatusEvidence.loading);

  return (
    <div data-testid="chief-engineer-workspace" className="flex h-full flex-col overflow-hidden bg-gradient-to-br from-slate-950 via-slate-900 to-cyan-950/30 text-slate-100">
      <header className="flex h-14 items-center justify-between border-b border-cyan-500/20 bg-slate-950/80 px-4">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBackToMain}
            data-testid="chief-engineer-workspace-back"
            className="text-slate-400 hover:bg-white/5 hover:text-slate-100"
          >
            <ChevronLeft className="mr-1 h-4 w-4" />
            返回
          </Button>
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-200 ring-1 ring-cyan-400/30">
              <Brain className="h-4 w-4" />
            </div>
            <div>
              <h1 className="text-sm font-semibold text-cyan-100">Chief Engineer</h1>
              <p className="text-[10px] uppercase tracking-wider text-cyan-400/70">Blueprint Control Room</p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowAIDialogue((current) => !current)}
            data-testid="chief-engineer-toggle-dialogue"
            className={cn(
              'text-slate-300 hover:bg-white/5 hover:text-white',
              showAIDialogue && 'bg-cyan-500/10 text-cyan-100',
            )}
          >
            <MessageSquare className="mr-1.5 h-3.5 w-3.5" />
            对话
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onOpenSettings}
            disabled={!onOpenSettings}
            data-testid="chief-engineer-open-settings"
            title={onOpenSettings ? '系统配置' : '系统配置需由主界面打开'}
            className="text-slate-300 hover:bg-white/5 hover:text-white"
          >
            <Settings className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => { void handleToggleDirector(); }}
            disabled={directorToggleBusy || startDirectorBlocked}
            title={startDirectorBlocked ? '缺少 Chief Engineer 蓝图证据，不能从 CE 页直接启动 Director' : undefined}
            data-testid="chief-engineer-start-director"
            className="border-cyan-500/30 text-cyan-200 hover:bg-cyan-500/10"
          >
            {directorToggleBusy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Play className="mr-1.5 h-3.5 w-3.5" />}
            {directorRunning ? '停止 Director' : '启动 Director'}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onEnterDirectorWorkspace}
            data-testid="chief-engineer-enter-director"
            className="text-slate-300 hover:bg-white/5 hover:text-white"
          >
            Director 看板
            <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      <section
        className="border-b border-cyan-500/15 bg-slate-950/75 px-4 py-2 text-xs text-slate-300"
        data-testid="chief-engineer-backend-strip"
      >
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 font-medium text-cyan-100">Capabilities</span>
            <span className="shrink-0 font-mono text-[11px] text-cyan-300">
              /v2/roles/capabilities/chief_engineer?host_kind=electron_workbench
            </span>
            {chiefBackendEvidenceLoading ? (
              <span className="text-slate-400">读取中...</span>
            ) : chiefCapabilitiesError ? (
              <span className="text-rose-300">{chiefCapabilitiesError}</span>
            ) : (
              <span className="truncate text-emerald-300">
                {chiefCapabilities.length > 0 ? chiefCapabilities.slice(0, 5).join(', ') : 'none'}
              </span>
            )}
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 font-medium text-cyan-100">LLM events</span>
            <span className="shrink-0 font-mono text-[11px] text-cyan-300">
              /v2/chief-engineer/llm-events?limit=5
            </span>
            {chiefBackendEvidenceLoading ? (
              <span className="text-slate-400">读取中...</span>
            ) : chiefLLMEventsError ? (
              <span className="text-rose-300">{chiefLLMEventsError}</span>
            ) : (
              <span className="truncate text-emerald-300">
                events={chiefLLMEventCount}
                {latestChiefLLMEvent ? ` · ${formatChiefKernelEvent(latestChiefLLMEvent)}` : ''}
              </span>
            )}
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 font-medium text-cyan-100">Kernel cache</span>
            <span className="shrink-0 font-mono text-[11px] text-cyan-300">
              /v2/chief-engineer/cache-stats
            </span>
            {chiefBackendEvidenceLoading ? (
              <span className="text-slate-400">读取中...</span>
            ) : chiefKernelCacheError ? (
              <span className="text-rose-300">{chiefKernelCacheError}</span>
            ) : (
              <span className="truncate text-emerald-300">{formatChiefCacheStats(chiefKernelCacheStats)}</span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => { void handleClearChiefKernelCache(); }}
              disabled={chiefKernelCacheClearing}
              data-testid="chief-engineer-kernel-cache-clear"
              className="h-6 px-1.5 text-[10px] text-cyan-200 hover:bg-cyan-500/10 hover:text-cyan-100"
            >
              {chiefKernelCacheClearing ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
              clear
            </Button>
            {chiefKernelCacheClearStatus ? (
              <span data-testid="chief-engineer-kernel-cache-clear-result" className="truncate text-cyan-200">
                /v2/chief-engineer/cache-clear · {chiefKernelCacheClearStatus}
              </span>
            ) : null}
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 font-medium text-cyan-100">Token budget</span>
            <span className="shrink-0 font-mono text-[11px] text-cyan-300">
              /v2/chief-engineer/token-budget-stats
            </span>
            {chiefBackendEvidenceLoading ? (
              <span className="text-slate-400">读取中...</span>
            ) : chiefKernelTokenBudgetError ? (
              <span className="text-rose-300">{chiefKernelTokenBudgetError}</span>
            ) : (
              <span className="truncate text-emerald-300">{formatChiefTokenBudget(chiefKernelTokenBudgetStats)}</span>
            )}
          </div>
        </div>
      </section>

      {directorToggleStatusEvidence.triggered ? (
        <section
          className="border-b border-cyan-500/15 bg-slate-950/70 px-4 py-2 text-xs text-slate-300"
          data-testid="chief-engineer-director-status-evidence"
        >
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
            <div className="flex min-w-0 items-center gap-2">
              <span className="shrink-0 font-medium text-cyan-100">Director status</span>
              <span className="shrink-0 font-mono text-[11px] text-cyan-300">/v2/director/status?source=auto</span>
              {directorToggleStatusEvidence.loading ? (
                <span className="text-slate-400">读取中...</span>
              ) : directorToggleStatusEvidence.error ? (
                <span className="text-rose-300">{directorToggleStatusEvidence.error}</span>
              ) : directorToggleStatusEvidence.data ? (
                <span className={cn(
                  'truncate',
                  directorToggleStatusEvidence.data.running ? 'text-emerald-300' : 'text-slate-300',
                )}>
                  {directorToggleStatusEvidence.data.running ? 'running' : 'idle'}
                  {' · '}
                  pid={directorToggleStatusEvidence.data.pid ?? 'none'}
                  {directorToggleStatusEvidence.data.mode ? ` · mode=${directorToggleStatusEvidence.data.mode}` : ''}
                  {directorToggleStatusEvidence.data.source ? ` · source=${directorToggleStatusEvidence.data.source}` : ''}
                </span>
              ) : (
                <span className="text-slate-400">未返回状态</span>
              )}
            </div>
          </div>
        </section>
      ) : null}

      <main
        className={cn(
          'grid min-h-0 flex-1 gap-4 overflow-hidden p-4',
          showAIDialogue
            ? 'grid-cols-[minmax(0,1fr)_340px_380px]'
            : 'grid-cols-[minmax(0,1fr)_340px]',
        )}
      >
        <section className="min-h-0 overflow-auto rounded-lg border border-white/10 bg-white/[0.035]">
          <div className="border-b border-white/10 px-4 py-3">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-100">施工蓝图证据</h2>
                <p className="mt-1 text-xs text-slate-400">仅展示从 PM/CE/Director 任务合同中读取到的真实字段。</p>
                {blueprintApiError ? (
                  <p className="mt-1 text-[11px] text-amber-300">蓝图 API 暂不可用: {blueprintApiError}</p>
                ) : null}
              </div>
              <span data-testid="chief-engineer-status" className="rounded-md border border-cyan-500/25 bg-cyan-500/10 px-2 py-1 text-[10px] uppercase tracking-wider text-cyan-200">
                {chiefStatus}
              </span>
            </div>
          </div>

          <div className="space-y-3 p-4">
            {blueprintEvidence.length === 0 ? (
              <div data-testid="chief-engineer-blueprint-empty" className="rounded-lg border border-amber-500/25 bg-amber-500/10 p-4 text-sm text-amber-100">
                <div className="flex items-center gap-2 font-medium">
                  <AlertTriangle className="h-4 w-4" />
                  未发现已落盘的 Chief Engineer 蓝图证据
                </div>
                <p className="mt-2 text-xs leading-5 text-amber-100/75">
                  当前不会伪造蓝图内容。需要 PM/CE 链路写入 `blueprint_id`、`blueprint_path` 或 `runtime_blueprint_path` 后，这里才展示蓝图记录。
                </p>
              </div>
            ) : (
              blueprintEvidence.map((item) => (
                <article key={`${item.taskId}-${item.blueprintId || item.blueprintPath}`} className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-sm font-medium text-cyan-100">
                        <FileText className="h-4 w-4 shrink-0" />
                        <span className="truncate">{item.taskTitle}</span>
                      </div>
                      {item.summary ? <p className="mt-2 text-xs leading-5 text-slate-300">{item.summary}</p> : null}
                    </div>
                    {item.blueprintId ? (
                      <div className="flex shrink-0 items-center gap-1">
                        <span className="rounded-md bg-white/10 px-2 py-1 text-[10px] text-slate-300">{item.blueprintId}</span>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => { void loadBlueprintDetail(item.blueprintId); }}
                          data-testid={`chief-engineer-blueprint-open-${item.blueprintId}`}
                          className="h-6 px-2 text-[10px] text-cyan-200 hover:bg-cyan-500/10 hover:text-cyan-100"
                          title="读取 Chief Engineer 蓝图详情"
                        >
                          <FileCode className="mr-1 h-3 w-3" />
                          详情
                        </Button>
                      </div>
                    ) : null}
                  </div>
                  {item.blueprintPath ? (
                    <div className="mt-2 truncate rounded-md border border-white/10 bg-slate-950/50 px-2 py-1 text-[11px] text-slate-400" title={item.blueprintPath}>
                      {item.blueprintPath}
                    </div>
                  ) : null}
                  <div
                    className="mt-2 inline-flex rounded-md border border-white/10 bg-slate-950/55 px-2 py-1 text-[10px] text-cyan-200"
                    data-testid="chief-engineer-blueprint-provenance"
                    title={`source: ${item.source}`}
                  >
                    source · {item.source}
                  </div>
                  {item.targetFiles.length > 0 ? (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {item.targetFiles.slice(0, 8).map((file) => (
                        <span key={file} className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-300">
                          {file}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </article>
              ))
            )}

            {blueprintCandidateTasks.length > 0 ? (
              <div data-testid="chief-engineer-blueprint-candidates" className="rounded-lg border border-cyan-500/20 bg-slate-950/45 p-3">
                <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-cyan-200">
                  <FilePlus className="h-3.5 w-3.5" />
                  待生成蓝图
                </div>
                <div className="space-y-2">
                  {blueprintCandidateTasks.map((task) => {
                    const taskId = taskHandoffId(task);
                    const isGenerating = generatingTaskId === taskId;
                    const statusCheck = blueprintStatusChecks[taskId];
                    return (
                      <div key={taskId} className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-2">
                        <div className="flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            <div className="truncate text-xs font-medium text-slate-200">{taskTitle(task)}</div>
                            <div className="mt-0.5 truncate text-[10px] text-slate-500">{taskObjective(task)}</div>
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => { void handleCheckBlueprintStatus(task); }}
                              disabled={Boolean(statusCheck?.loading)}
                              data-testid={`chief-engineer-blueprint-status-${taskId}`}
                              className="h-7 px-2 text-[10px] text-slate-300 hover:bg-white/5 hover:text-cyan-100"
                            >
                              {statusCheck?.loading ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <CheckCircle2 className="mr-1 h-3 w-3" />}
                              状态
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => { void handleGenerateBlueprint(task); }}
                              disabled={isGenerating}
                              data-testid={`chief-engineer-blueprint-generate-${taskId}`}
                              className="h-7 px-2 text-[10px] text-cyan-200 hover:bg-cyan-500/10 hover:text-cyan-100"
                            >
                              {isGenerating ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <FilePlus className="mr-1 h-3 w-3" />}
                              生成
                            </Button>
                          </div>
                        </div>
                        {statusCheck ? (
                          <div
                            data-testid={`chief-engineer-blueprint-status-result-${taskId}`}
                            className={cn(
                              'mt-2 rounded-md border px-2 py-1.5 text-[11px]',
                              statusCheck.error
                                ? 'border-red-500/25 bg-red-500/10 text-red-100'
                                : 'border-cyan-500/20 bg-cyan-500/5 text-cyan-100',
                            )}
                          >
                            <div className="mb-1 font-mono text-[10px] text-slate-500">
                              /v2/chief-engineer/blueprints/status
                            </div>
                            {statusCheck.error ? (
                              <div>{statusCheck.error}</div>
                            ) : statusCheck.loading ? (
                              <div>正在读取蓝图状态...</div>
                            ) : statusCheck.result ? (
                              <div className="space-y-1">
                                <div>
                                  status · {statusCheck.result.status || 'unknown'}
                                  {statusCheck.result.blueprint_id ? ` / ${statusCheck.result.blueprint_id}` : ''}
                                </div>
                                {statusCheck.result.summary ? (
                                  <div className="line-clamp-2 text-slate-300">{statusCheck.result.summary}</div>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
                {generateError ? (
                  <div data-testid="chief-engineer-blueprint-generate-error" className="mt-2 rounded-md border border-red-500/25 bg-red-500/10 px-2 py-1.5 text-xs text-red-100">
                    {generateError}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>

        <aside className="flex min-h-0 flex-col gap-3 overflow-auto">
          <section data-testid="chief-engineer-diagnostics" className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" />
                CE 诊断
              </h3>
              <span
                data-testid="chief-engineer-diagnostics-status"
                className={cn(
                  'rounded-md border px-2 py-1 text-[10px] uppercase tracking-wider',
                  diagnosticsState === 'ready' && 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
                  diagnosticsState === 'degraded' && 'border-amber-500/30 bg-amber-500/10 text-amber-200',
                  diagnosticsState === 'error' && 'border-red-500/30 bg-red-500/10 text-red-200',
                  diagnosticsState === 'checking' && 'border-slate-500/30 bg-slate-500/10 text-slate-300',
                )}
              >
                {diagnosticsState}
              </span>
            </div>
            <div className="space-y-2">
              <DiagnosticRow
                label="Workspace"
                value={diagnostics?.workspace.status || 'checking'}
                tone={workspaceDiagnosticTone}
              />
              <DiagnosticRow
                label="Blueprints"
                value={diagnostics ? `${diagnostics.blueprints.loadable}/${diagnostics.blueprints.total}` : 'checking'}
                tone={blueprintDiagnosticTone}
              />
              <DiagnosticRow
                label="Director handoff"
                value={diagnostics?.blueprints.director_handoff_ready ? 'ready' : diagnostics ? 'no blueprint' : 'checking'}
                tone={handoffDiagnosticTone}
              />
            </div>
            {diagnosticsError ? (
              <div data-testid="chief-engineer-diagnostics-error" className="mt-3 rounded-md border border-red-500/25 bg-red-500/10 px-2 py-2 text-xs text-red-100">
                {diagnosticsError}
              </div>
            ) : null}
            {diagnostics?.issues.length ? (
              <div data-testid="chief-engineer-diagnostics-issues" className="mt-3 rounded-md border border-amber-500/20 bg-amber-500/10 px-2 py-2 text-[11px] text-amber-100">
                {diagnostics.issues.join(', ')}
              </div>
            ) : null}
          </section>

          <section data-testid="chief-engineer-director-task-pool" className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <ShieldCheck className="h-3.5 w-3.5 text-cyan-300" />
              Director 任务池
            </h3>
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] text-slate-500">
                /v2/director/tasks
              </span>
              <span
                data-testid="chief-engineer-director-task-source"
                className="rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] text-slate-500"
              >
                {directorTaskEvidenceRows.length > tasks.length ? 'backend fallback' : 'runtime snapshot'}
              </span>
            </div>
            {directorTaskApiError ? (
              <div data-testid="chief-engineer-director-task-error" className="mb-2 rounded-md border border-amber-500/25 bg-amber-500/10 p-2 text-xs text-amber-100">
                {directorTaskApiError}
              </div>
            ) : null}
            <div className="grid grid-cols-2 gap-2 text-center">
              <Metric label="未领取" value={stats.unclaimed} tone="slate" />
              <Metric label="执行中" value={stats.running} tone="blue" />
              <Metric label="阻塞" value={stats.blocked} tone="amber" />
              <Metric label="报错" value={stats.failed} tone="red" />
              <Metric label="完成" value={stats.completed} tone="emerald" />
              <Metric label="总计" value={stats.total} tone="cyan" />
            </div>
            {lastDirectorStatus ? (
              <div className="mt-3 rounded-md border border-white/10 bg-slate-950/50 px-2 py-2 text-xs text-slate-300">
                最近 Director 状态: {lastDirectorStatus}
              </div>
            ) : null}
            {directorTaskLoading && stats.total === 0 ? (
              <div data-testid="chief-engineer-director-task-loading" className="mt-3 rounded-md border border-white/10 bg-slate-950/50 px-2 py-2 text-xs text-slate-400">
                正在读取 Director 任务池...
              </div>
            ) : null}
          </section>

          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <FileCode className="h-3.5 w-3.5 text-cyan-300" />
              蓝图详情
            </h3>
            {!selectedBlueprintId ? (
              <div data-testid="chief-engineer-blueprint-detail-empty" className="rounded-md border border-white/10 bg-slate-950/50 p-3 text-xs text-slate-400">
                选择左侧蓝图后，这里展示后端持久化的原始 blueprint payload。
              </div>
            ) : blueprintDetailLoading ? (
              <div data-testid="chief-engineer-blueprint-detail-loading" className="flex items-center gap-2 rounded-md border border-cyan-500/20 bg-cyan-500/10 p-3 text-xs text-cyan-100">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                正在读取 {selectedBlueprintId}
              </div>
            ) : blueprintDetailError ? (
              <div data-testid="chief-engineer-blueprint-detail-error" className="rounded-md border border-red-500/25 bg-red-500/10 p-3 text-xs text-red-100">
                {selectedBlueprintId}: {blueprintDetailError}
              </div>
            ) : blueprintDetail ? (
              <div data-testid="chief-engineer-blueprint-detail" className="min-w-0 rounded-md border border-cyan-500/20 bg-slate-950/60">
                <div className="flex items-center justify-between gap-2 border-b border-white/10 px-3 py-2 text-[11px]">
                  <span className="truncate font-mono text-cyan-100">{blueprintDetail.blueprint_id}</span>
                  <span className="shrink-0 rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-400">
                    {blueprintDetail.source || 'runtime/blueprints'}
                  </span>
                </div>
                <pre className="max-h-72 overflow-auto p-3 text-[10px] leading-4 text-slate-300">
                  {JSON.stringify(blueprintDetail.blueprint, null, 2)}
                </pre>
              </div>
            ) : null}
          </section>

          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
                <Hammer className="h-3.5 w-3.5 text-indigo-300" />
                当前 Director 列表
              </h3>
              <span className="rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] text-slate-500">
                /v2/director/workers
              </span>
            </div>
            {directorWorkerApiError ? (
              <div data-testid="chief-engineer-director-worker-error" className="mb-2 rounded-md border border-amber-500/25 bg-amber-500/10 p-2 text-xs text-amber-100">
                {directorWorkerApiError}
              </div>
            ) : null}
            {directorRows.length === 0 ? (
              <div data-testid="chief-engineer-director-empty" className="rounded-md border border-white/10 bg-slate-950/50 p-3 text-xs text-slate-400">
                {directorWorkerLoading
                  ? '正在读取 Director worker 心跳...'
                  : '暂无 Director worker 心跳。启动 Director 后这里显示每个 worker 的状态和当前任务。'}
              </div>
            ) : (
              <div data-testid="chief-engineer-director-list" className="space-y-2">
                {directorRows.map((worker) => (
                  <div key={worker.id} className="rounded-md border border-white/10 bg-slate-950/50 p-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium text-slate-200">{worker.name || worker.id}</span>
                      <span className={cn(
                        'rounded px-1.5 py-0.5 text-[10px]',
                        worker.status === 'busy' ? 'bg-blue-500/15 text-blue-200' :
                          worker.status === 'failed' ? 'bg-red-500/15 text-red-200' :
                            'bg-emerald-500/15 text-emerald-200',
                      )}>
                        {worker.status || 'unknown'}
                      </span>
                    </div>
                    <div className="mt-1 truncate text-slate-400">
                      当前任务: {worker.currentTaskId || '空闲'}
                    </div>
                    <div className="mt-1 flex gap-2 text-[10px] text-slate-500">
                      <span>完成 {worker.tasksCompleted ?? 0}</span>
                      <span>失败 {worker.tasksFailed ?? 0}</span>
                      {worker.healthy === false ? <span className="text-red-300">unhealthy</span> : null}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <Activity className="h-3.5 w-3.5 text-emerald-300" />
              工作区
            </h3>
            <div className="break-all rounded-md border border-white/10 bg-slate-950/50 p-2 text-xs text-slate-400">
              {workspace || '未选择 workspace'}
            </div>
          </section>
        </aside>

        {showAIDialogue ? (
          <section
            data-testid="chief-engineer-dialogue"
            className="min-h-0 overflow-hidden rounded-lg border border-cyan-500/20 bg-slate-950/45"
          >
            <AIDialoguePanel
              dialogueRole="chief_engineer"
              roleDisplayName="Chief Engineer"
              roleTheme={{
                primary: 'cyan',
                secondary: 'cyan-400',
                gradient: 'from-cyan-500 to-cyan-700',
              }}
              welcomeMessage="Chief Engineer 工作台已就绪。您可以审查 PM 合同、产出施工蓝图，或确认 Director 执行前置条件。"
              context={{
                workspace,
                task_count: tasks.length,
                director_task_count: directorTaskEvidenceRows.length,
                blueprint_count: blueprintEvidence.length,
                diagnostics_ok: diagnostics?.ok ?? false,
                diagnostics_issues: diagnostics?.issues ?? [],
                chief_engineer_status: chiefStatus,
                director_running: directorRunning,
              }}
              workspace={workspace}
              hostKind="electron_workbench"
              attachmentMode="isolated"
              workflowExportTarget="director"
              workflowExportLabel="导出 Director"
            />
          </section>
        ) : null}
      </main>
    </div>
  );
}

function DiagnosticRow({ label, value, tone }: { label: string; value: string; tone: DiagnosticTone }) {
  const tones = {
    ready: 'text-emerald-200',
    degraded: 'text-amber-200',
    error: 'text-red-200',
    checking: 'text-slate-300',
  } satisfies Record<DiagnosticTone, string>;
  return (
    <div className="flex min-h-8 items-center justify-between gap-3 border-b border-white/5 py-1.5 last:border-b-0">
      <span className="text-xs text-slate-400">{label}</span>
      <span className={cn('max-w-[12rem] truncate text-right text-xs font-medium', tones[tone])} title={value}>
        {value}
      </span>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone: 'slate' | 'blue' | 'amber' | 'red' | 'emerald' | 'cyan' }) {
  const tones = {
    slate: 'border-slate-500/20 bg-slate-500/10 text-slate-200',
    blue: 'border-blue-500/25 bg-blue-500/10 text-blue-200',
    amber: 'border-amber-500/25 bg-amber-500/10 text-amber-200',
    red: 'border-red-500/25 bg-red-500/10 text-red-200',
    emerald: 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200',
    cyan: 'border-cyan-500/25 bg-cyan-500/10 text-cyan-200',
  } satisfies Record<typeof tone, string>;
  return (
    <div className={cn('rounded-md border px-2 py-2', tones[tone])}>
      <div className="text-lg font-semibold">{value}</div>
      <div className="text-[10px] text-current/70">{label}</div>
    </div>
  );
}
