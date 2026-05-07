import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Brain,
  CheckCircle2,
  ChevronLeft,
  FileCode,
  FileText,
  Hammer,
  Loader2,
  Pause,
  Play,
  ShieldCheck,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { apiFetch } from '@/api';
import type { EngineStatus } from '@/app/types/appContracts';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntime';
import type { PmTask } from '@/types/task';
import type {
  ChiefEngineerBlueprintListV1,
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
  onToggleDirector: () => void;
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
type RuntimeBlueprintListResponse = ChiefEngineerBlueprintListV1;

function normalizeToken(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function metadataOf(task: PmTask): Record<string, unknown> {
  return task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
}

function readString(task: PmTask, keys: string[]): string {
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

function readTaskStringList(task: PmTask, keys: string[]): string[] {
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

function taskTitle(task: PmTask): string {
  return readString(task, ['title', 'subject', 'goal', 'summary']) || String(task.id || '未命名任务');
}

function taskStatus(task: PmTask): 'unclaimed' | 'running' | 'blocked' | 'failed' | 'completed' {
  const status = normalizeToken(task.status || task.state);
  if (task.done || task.completed || ['completed', 'done', 'success', 'passed'].includes(status)) return 'completed';
  if (['failed', 'error'].includes(status)) return 'failed';
  if (['blocked', 'cancelled', 'canceled'].includes(status)) return 'blocked';
  if (['running', 'in_progress', 'claimed', 'pending_exec'].includes(status)) return 'running';
  return 'unclaimed';
}

function buildBlueprintEvidence(tasks: PmTask[]): BlueprintEvidence[] {
  return tasks
    .map((task) => {
      const blueprintId = readString(task, ['blueprint_id', 'blueprintId']);
      const blueprintPath = readString(task, ['blueprint_path', 'runtime_blueprint_path']);
      const summary = readString(task, ['blueprint_summary', 'summary', 'goal']);
      if (!blueprintId && !blueprintPath) return null;
      return {
        taskId: String(task.id || '').trim(),
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

function buildRuntimeBlueprintEvidence(rows: RuntimeBlueprintSummary[]): BlueprintEvidence[] {
  return rows
    .filter((row) => row && typeof row === 'object' && String(row.blueprint_id || '').trim())
    .map((row) => ({
      taskId: String(row.blueprint_id).trim(),
      taskTitle: String(row.title || row.blueprint_id).trim(),
      blueprintId: String(row.blueprint_id).trim(),
      blueprintPath: '',
      source: String(row.source || 'runtime/blueprints').trim(),
      summary: String(row.summary || '').trim(),
      targetFiles: Array.isArray(row.target_files) ? row.target_files.map((item) => String(item).trim()).filter(Boolean) : [],
    }));
}

function roleStatus(engineStatus: EngineStatus | null, role: string): string {
  const roles = engineStatus?.roles;
  const rolePayload = roles?.[role] || roles?.[role.toLowerCase()];
  return String(rolePayload?.status || '').trim();
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
}: ChiefEngineerWorkspaceProps) {
  const [runtimeBlueprints, setRuntimeBlueprints] = useState<RuntimeBlueprintSummary[]>([]);
  const [blueprintApiError, setBlueprintApiError] = useState('');
  const taskBlueprintEvidence = useMemo(() => buildBlueprintEvidence(tasks), [tasks]);

  useEffect(() => {
    if (!workspace) {
      setRuntimeBlueprints([]);
      setBlueprintApiError('');
      return;
    }
    let cancelled = false;
    const loadBlueprints = async () => {
      try {
        const response = await apiFetch('/v2/chief-engineer/blueprints');
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const payload = (await response.json()) as RuntimeBlueprintListResponse;
        if (!cancelled) {
          setRuntimeBlueprints(Array.isArray(payload.blueprints) ? payload.blueprints : []);
          setBlueprintApiError('');
        }
      } catch (error) {
        if (!cancelled) {
          setRuntimeBlueprints([]);
          setBlueprintApiError(error instanceof Error ? error.message : '蓝图 API 暂不可用');
        }
      }
    };
    void loadBlueprints();
    return () => {
      cancelled = true;
    };
  }, [workspace]);

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
    const rows = tasks.map(taskStatus);
    return {
      total: rows.length,
      unclaimed: rows.filter((item) => item === 'unclaimed').length,
      running: rows.filter((item) => item === 'running').length,
      blocked: rows.filter((item) => item === 'blocked').length,
      failed: rows.filter((item) => item === 'failed').length,
      completed: rows.filter((item) => item === 'completed').length,
    };
  }, [tasks]);

  const chiefStatus = roleStatus(engineStatus, 'ChiefEngineer') || roleStatus(engineStatus, 'chief_engineer') || 'idle';
  const directorRows = workers.filter((worker) => worker && typeof worker === 'object');
  const lastDirectorStatus = String(pmState?.last_director_status || '').trim();
  const startDirectorBlocked = !directorRunning && tasks.length > 0 && blueprintEvidence.length === 0;

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
            variant="outline"
            size="sm"
            onClick={onToggleDirector}
            disabled={isStartingDirector || startDirectorBlocked}
            title={startDirectorBlocked ? '缺少 Chief Engineer 蓝图证据，不能从 CE 页直接启动 Director' : undefined}
            data-testid="chief-engineer-start-director"
            className="border-cyan-500/30 text-cyan-200 hover:bg-cyan-500/10"
          >
            {isStartingDirector ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Play className="mr-1.5 h-3.5 w-3.5" />}
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

      <main className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_340px] gap-4 overflow-hidden p-4">
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
                      <span className="rounded-md bg-white/10 px-2 py-1 text-[10px] text-slate-300">{item.blueprintId}</span>
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
          </div>
        </section>

        <aside className="flex min-h-0 flex-col gap-3 overflow-auto">
          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <ShieldCheck className="h-3.5 w-3.5 text-cyan-300" />
              Director 任务池
            </h3>
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
          </section>

          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <Hammer className="h-3.5 w-3.5 text-indigo-300" />
              当前 Director 列表
            </h3>
            {directorRows.length === 0 ? (
              <div data-testid="chief-engineer-director-empty" className="rounded-md border border-white/10 bg-slate-950/50 p-3 text-xs text-slate-400">
                暂无 Director worker 心跳。启动 Director 后这里显示每个 worker 的状态和当前任务。
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
      </main>
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
