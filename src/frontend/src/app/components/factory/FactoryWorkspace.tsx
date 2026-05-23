/** FactoryWorkspace - 无人值守开发工厂工作区 */
import { useEffect, useMemo, useState } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import {
  Activity,
  AlertCircle,
  BadgeCheck,
  Brain,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  FileCode,
  FileText,
  Hammer,
  Layers,
  Loader2,
  PackageCheck,
  Play,
  RotateCcw,
  Route,
  ShieldCheck,
  Square,
  Terminal,
  XCircle,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { PMWorkspace } from '@/app/components/pm';
import { DirectorWorkspace } from '@/app/components/director';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import type { FileEditEvent } from '@/app/hooks/useRuntime';
import type { FactoryAuditEvent, FactoryRunArtifact, FactoryRunStatus } from '@/hooks/useFactory';
import type { LogEntry } from '@/types/log';
import type { PmTask } from '@/types/task';

interface FactoryWorkspaceProps {
  workspace: string;
  onBackToMain: () => void;
  tasks: PmTask[];
  pmTasks?: PmTask[];
  directorTasks?: PmTask[];
  executionLogs?: LogEntry[];
  llmStreamEvents?: LogEntry[];
  processStreamEvents?: LogEntry[];
  fileEditEvents?: FileEditEvent[];
  currentRun?: FactoryRunStatus | null;
  events?: FactoryAuditEvent[];
  artifacts?: FactoryRunArtifact[];
  summaryMd?: string | null;
  summaryJson?: Record<string, unknown> | null;
  artifactsError?: string | null;
  isArtifactsLoading?: boolean;
  onStart?: () => void;
  onCancel?: () => void;
  isLoading?: boolean;
}

type FactoryPhase = 'idle' | 'planning' | 'executing' | 'verifying' | 'completed' | 'failed' | 'cancelled';
type FactoryRoleLayer = 'pm' | 'chief_engineer' | 'director';
type RunRoleStatus = FactoryRunStatus['roles'][string];

interface RoleLayerView {
  id: FactoryRoleLayer;
  title: string;
  subtitle: string;
  status: string;
  progress: number;
  metric: string;
  detail: string;
  icon: React.ReactNode;
  tone: {
    idle: string;
    active: string;
    text: string;
    progress: string;
  };
}

interface BlueprintEvidenceView {
  id: string;
  title: string;
  path: string;
  summary: string;
  source: 'task' | 'artifact';
}

const PHASE_CONFIG: Record<FactoryPhase, { label: string; color: string; icon: React.ReactNode }> = {
  idle: { label: '等待启动', color: 'text-slate-400', icon: <Hammer className="h-4 w-4" /> },
  planning: { label: '规划中', color: 'text-amber-300', icon: <ClipboardList className="h-4 w-4" /> },
  executing: { label: '执行中', color: 'text-indigo-300', icon: <Terminal className="h-4 w-4" /> },
  verifying: { label: '验证中', color: 'text-cyan-300', icon: <CheckCircle2 className="h-4 w-4" /> },
  completed: { label: '已完成', color: 'text-emerald-300', icon: <CheckCircle2 className="h-4 w-4" /> },
  failed: { label: '失败', color: 'text-red-300', icon: <AlertCircle className="h-4 w-4" /> },
  cancelled: { label: '已取消', color: 'text-orange-300', icon: <XCircle className="h-4 w-4" /> },
};

function normalizeToken(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase();
}

function mapRunToFactoryPhase(run?: FactoryRunStatus | null): FactoryPhase {
  const status = normalizeToken(run?.status);
  const phase = normalizeToken(run?.phase);
  const stage = normalizeToken(run?.current_stage);

  if (status === 'cancelled' || phase === 'cancelled') return 'cancelled';
  if (status === 'failed' || phase === 'failed') return 'failed';
  if (phase === 'completed' || status === 'completed') return 'completed';
  if (['verification', 'qa_gate', 'handover', 'quality_gate'].includes(phase) || stage.includes('quality')) {
    return 'verifying';
  }
  if (phase === 'implementation' || stage.includes('director')) return 'executing';
  if (stage.includes('chief') || stage.includes('blueprint')) return 'planning';
  if (['architect', 'planning', 'pending', 'intake', 'docs_check'].includes(phase) || stage.includes('pm')) {
    return 'planning';
  }
  return 'idle';
}

function mapRunToWorkspacePhase(run?: FactoryRunStatus | null): string {
  const phase = mapRunToFactoryPhase(run);
  if (phase === 'planning') return 'planning';
  if (phase === 'executing') return 'executing';
  if (phase === 'verifying') return 'verification';
  if (phase === 'completed') return 'completed';
  if (phase === 'failed' || phase === 'cancelled') return 'error';
  return 'idle';
}

function preferredRoleLayer(run?: FactoryRunStatus | null): FactoryRoleLayer {
  const stage = normalizeToken(run?.current_stage);
  const phase = mapRunToFactoryPhase(run);
  if (stage.includes('director') || phase === 'executing' || phase === 'verifying') return 'director';
  if (stage.includes('chief') || stage.includes('blueprint') || stage.includes('architect')) return 'chief_engineer';
  return 'pm';
}

function toEventLevel(event: FactoryAuditEvent): LogEntry['level'] {
  const type = normalizeToken(event.type);
  const resultStatus = normalizeToken(String((event.result as Record<string, unknown> | undefined)?.status || ''));

  if (type === 'cancelled') return 'warning';
  if (type === 'failed' || type === 'error') return 'error';
  if (type === 'stage_started') return 'exec';
  if (type === 'stage_completed' && resultStatus === 'failed') return 'error';
  if (type === 'stage_completed' && resultStatus === 'success') return 'success';
  if (type === 'completed') return 'success';
  return 'info';
}

function toActivityLogs(events: FactoryAuditEvent[]): LogEntry[] {
  return events.map((event, index) => {
    const message = String(event.message || event.type || 'Factory event').trim();
    const tags = [event.stage, event.type].filter((value): value is string => Boolean(value));
    return {
      id: String(event.event_id || `${event.type}-${index}`),
      timestamp: String(event.timestamp || new Date().toISOString()),
      level: toEventLevel(event),
      source: 'FACTORY',
      title: event.stage ? `阶段: ${event.stage}` : 'Factory 事件',
      message,
      details: event.result ? JSON.stringify(event.result, null, 2) : undefined,
      tags,
    };
  });
}

function formatBytes(size?: number): string {
  if (typeof size !== 'number' || Number.isNaN(size) || size < 0) {
    return 'size n/a';
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatSummaryValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return 'n/a';
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return JSON.stringify(value);
}

function toSummaryRows(summaryJson?: Record<string, unknown> | null): Array<[string, string]> {
  if (!summaryJson) {
    return [];
  }
  return Object.entries(summaryJson)
    .slice(0, 5)
    .map(([key, value]) => [key, formatSummaryValue(value)]);
}

function gateTone(gate: FactoryRunStatus['gates'][number]): string {
  const status = normalizeToken(gate.status);
  if (gate.passed || status === 'passed' || status === 'success') {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300';
  }
  if (status === 'failed' || status === 'error') {
    return 'border-red-500/30 bg-red-500/10 text-red-300';
  }
  if (status === 'running' || status === 'pending') {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
  }
  return 'border-slate-500/30 bg-slate-500/10 text-slate-300';
}

function roleStatusTone(status: string): string {
  const token = normalizeToken(status);
  if (['completed', 'ready', 'success', 'passed'].includes(token)) {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
  }
  if (['running', 'active', 'in_progress'].includes(token)) {
    return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200';
  }
  if (['failed', 'error', 'blocked'].includes(token)) {
    return 'border-red-500/30 bg-red-500/10 text-red-200';
  }
  if (['pending', 'waiting'].includes(token)) {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-200';
  }
  return 'border-slate-500/30 bg-slate-500/10 text-slate-300';
}

function taskStatusToken(task: PmTask): string {
  return normalizeToken(String(task.status || task.state || ''));
}

function isTaskDone(task: PmTask): boolean {
  const status = taskStatusToken(task);
  return task.done || task.completed === true || status === 'completed' || status === 'success' || status === 'done';
}

function isTaskRunning(task: PmTask): boolean {
  const status = taskStatusToken(task);
  return status === 'running' || status === 'in_progress' || status === 'active';
}

function taskRecord(task: PmTask): Record<string, unknown> {
  return task as unknown as Record<string, unknown>;
}

function taskMetadata(task: PmTask): Record<string, unknown> {
  return task.metadata && typeof task.metadata === 'object' ? task.metadata : {};
}

function readTaskString(task: PmTask, keys: string[]): string {
  const direct = taskRecord(task);
  const metadata = taskMetadata(task);
  for (const key of keys) {
    const directValue = direct[key];
    if (typeof directValue === 'string' && directValue.trim()) return directValue.trim();
    const metadataValue = metadata[key];
    if (typeof metadataValue === 'string' && metadataValue.trim()) return metadataValue.trim();
  }
  return '';
}

function hasBlueprintEvidence(task: PmTask): boolean {
  return Boolean(readTaskString(task, ['blueprint_id', 'blueprint_path', 'runtime_blueprint_path']));
}

function isChiefEngineerArtifact(artifact: FactoryRunArtifact): boolean {
  const path = normalizeToken(artifact.path);
  const name = normalizeToken(artifact.name);
  return (
    path.includes('runtime/blueprints/')
    || path.includes('runtime/state/blueprints/')
    || name.includes('blueprint')
  );
}

function basename(path: string): string {
  const normalized = String(path || '').replace(/\\/g, '/').trim();
  return normalized.split('/').filter(Boolean).pop() || normalized || 'blueprint';
}

function buildBlueprintEvidence(tasks: PmTask[], artifacts: FactoryRunArtifact[]): BlueprintEvidenceView[] {
  const rows: BlueprintEvidenceView[] = [];
  const seen = new Set<string>();

  for (const task of tasks) {
    if (!hasBlueprintEvidence(task)) continue;
    const blueprintId = readTaskString(task, ['blueprint_id']) || task.id;
    const path = readTaskString(task, ['blueprint_path', 'runtime_blueprint_path']) || blueprintId;
    const key = path || blueprintId;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      id: blueprintId,
      title: task.title || task.id || blueprintId,
      path,
      summary: task.summary || task.goal || task.description || '任务合同携带的 Chief Engineer 蓝图字段',
      source: 'task',
    });
  }

  for (const artifact of artifacts.filter(isChiefEngineerArtifact)) {
    const path = String(artifact.path || '').trim();
    const name = String(artifact.name || basename(path)).trim();
    const key = path || name;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    rows.push({
      id: name,
      title: name,
      path: path || name,
      summary: path.includes('runtime/state/blueprints/')
        ? 'Factory Chief Engineer review summary artifact'
        : 'Chief Engineer runtime blueprint artifact',
      source: 'artifact',
    });
  }

  return rows;
}

function getRunRole(roles: FactoryRunStatus['roles'] | undefined, keys: string[]): RunRoleStatus | null {
  if (!roles) return null;
  const normalizedKeys = keys.map(normalizeToken);
  for (const key of keys) {
    const direct = roles[key];
    if (direct) return direct;
  }
  for (const [key, role] of Object.entries(roles)) {
    const normalizedKey = normalizeToken(key);
    const normalizedRoleName = normalizeToken(role.role);
    if (normalizedKeys.includes(normalizedKey) || normalizedKeys.includes(normalizedRoleName)) {
      return role;
    }
  }
  return null;
}

function percent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function buildRoleLayers({
  currentRun,
  pmTasks,
  directorTasks,
  blueprintEvidenceCount,
}: {
  currentRun: FactoryRunStatus | null;
  pmTasks: PmTask[];
  directorTasks: PmTask[];
  blueprintEvidenceCount: number;
}): RoleLayerView[] {
  const pmRole = getRunRole(currentRun?.roles, ['pm']);
  const chiefRole = getRunRole(currentRun?.roles, ['chief_engineer', 'chiefengineer', 'architect']);
  const directorRole = getRunRole(currentRun?.roles, ['director']);

  const completedPmTasks = pmTasks.filter(isTaskDone).length;
  const runningDirectorTasks = directorTasks.filter(isTaskRunning).length;
  const completedDirectorTasks = directorTasks.filter(isTaskDone).length;
  const pmProgress = pmRole ? percent(pmRole.progress) : pmTasks.length > 0 ? percent((completedPmTasks / pmTasks.length) * 100) : 0;
  const chiefProgress = chiefRole ? percent(chiefRole.progress) : blueprintEvidenceCount > 0 ? 100 : 0;
  const directorProgress = directorRole
    ? percent(directorRole.progress)
    : directorTasks.length > 0
      ? percent((completedDirectorTasks / directorTasks.length) * 100)
      : 0;

  return [
    {
      id: 'pm',
      title: 'PM',
      subtitle: '任务合同层',
      status: pmRole?.status || (pmTasks.length > 0 ? 'ready' : 'idle'),
      progress: pmProgress,
      metric: `${completedPmTasks}/${pmTasks.length}`,
      detail: pmRole?.detail || pmRole?.current_task || '规划目标、范围、验收与任务拆分',
      icon: <ClipboardList className="h-4 w-4" />,
      tone: {
        idle: 'border-amber-500/20 bg-amber-500/5 hover:border-amber-400/40',
        active: 'border-amber-400/50 bg-amber-500/12 shadow-[0_0_24px_rgba(245,158,11,0.12)]',
        text: 'text-amber-100',
        progress: 'from-amber-500 to-yellow-300',
      },
    },
    {
      id: 'chief_engineer',
      title: 'Chief Engineer',
      subtitle: '蓝图交接层',
      status: chiefRole?.status || (blueprintEvidenceCount > 0 ? 'ready' : 'waiting'),
      progress: chiefProgress,
      metric: `${blueprintEvidenceCount} evidence`,
      detail: chiefRole?.detail || chiefRole?.current_task || '审阅任务，沉淀施工蓝图与 Director 交接条件',
      icon: <Brain className="h-4 w-4" />,
      tone: {
        idle: 'border-cyan-500/20 bg-cyan-500/5 hover:border-cyan-400/40',
        active: 'border-cyan-400/50 bg-cyan-500/12 shadow-[0_0_24px_rgba(34,211,238,0.12)]',
        text: 'text-cyan-100',
        progress: 'from-cyan-500 to-sky-300',
      },
    },
    {
      id: 'director',
      title: 'Director',
      subtitle: '执行交付层',
      status: directorRole?.status || (runningDirectorTasks > 0 ? 'running' : directorTasks.length > 0 ? 'ready' : 'idle'),
      progress: directorProgress,
      metric: `${runningDirectorTasks} running`,
      detail: directorRole?.detail || directorRole?.current_task || '领取任务，执行文件变更、命令与验证',
      icon: <Hammer className="h-4 w-4" />,
      tone: {
        idle: 'border-indigo-500/20 bg-indigo-500/5 hover:border-indigo-400/40',
        active: 'border-indigo-400/50 bg-indigo-500/12 shadow-[0_0_24px_rgba(99,102,241,0.14)]',
        text: 'text-indigo-100',
        progress: 'from-indigo-500 to-violet-300',
      },
    },
  ];
}

export function FactoryWorkspace({
  workspace,
  onBackToMain,
  tasks,
  pmTasks,
  directorTasks,
  executionLogs = [],
  llmStreamEvents = [],
  processStreamEvents = [],
  fileEditEvents = [],
  currentRun = null,
  events = [],
  artifacts,
  summaryMd,
  summaryJson,
  artifactsError,
  isArtifactsLoading = false,
  onStart,
  onCancel,
  isLoading = false,
}: FactoryWorkspaceProps) {
  const factoryPhase = mapRunToFactoryPhase(currentRun);
  const workspacePhase = mapRunToWorkspacePhase(currentRun);
  const phaseConfig = PHASE_CONFIG[factoryPhase];
  const runStatus = normalizeToken(currentRun?.status);
  const isRunActive = runStatus === 'running' || runStatus === 'recovering';
  const canStart = !currentRun || ['completed', 'failed', 'cancelled'].includes(runStatus);
  const canCancel = runStatus === 'running';

  const pmWorkflowTasks = pmTasks ?? tasks;
  const directorWorkflowTasks = directorTasks ?? tasks;
  const activityLogs = useMemo(() => toActivityLogs(events), [events]);
  const gateResults = currentRun?.gates || [];
  const deliveryArtifacts = artifacts || currentRun?.artifacts || [];
  const blueprintEvidence = useMemo(
    () => buildBlueprintEvidence(pmWorkflowTasks, deliveryArtifacts),
    [deliveryArtifacts, pmWorkflowTasks]
  );
  const summaryMarkdown = String(summaryMd ?? currentRun?.summary_md ?? '').trim();
  const summaryRows = toSummaryRows(summaryJson ?? currentRun?.summary_json ?? null);
  const artifactErrorMessage = String(artifactsError || currentRun?.artifacts_error || '').trim();
  const suggestedLayer = preferredRoleLayer(currentRun);
  const [activeLayer, setActiveLayer] = useState<FactoryRoleLayer>(() => suggestedLayer);

  useEffect(() => {
    setActiveLayer(suggestedLayer);
  }, [suggestedLayer]);

  const roleLayers = useMemo(
    () => buildRoleLayers({
      currentRun,
      pmTasks: pmWorkflowTasks,
      directorTasks: directorWorkflowTasks,
      blueprintEvidenceCount: blueprintEvidence.length,
    }),
    [blueprintEvidence.length, currentRun, directorWorkflowTasks, pmWorkflowTasks]
  );
  const activeLayerView = roleLayers.find((layer) => layer.id === activeLayer) || roleLayers[0];

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#070b14] text-slate-100">
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-white/10 bg-slate-950/95 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onBackToMain}
            className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-white/10 hover:text-slate-100"
            aria-label="返回主界面"
          >
            <RotateCcw className="h-4 w-4" />
          </button>
          <div className="h-6 w-px bg-white/10" />
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-emerald-400/25 bg-emerald-500/10 text-emerald-200">
            <Hammer className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-sm font-semibold text-slate-100">Factory 模式</h1>
              <span className="rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-slate-400">
                role layers
              </span>
            </div>
            <p className="truncate text-[11px] text-slate-500">{workspace || '未设置工作区'}</p>
          </div>
        </div>

        <div className="flex min-w-0 items-center gap-2">
          <div
            className={cn(
              'flex items-center gap-2 rounded-lg border px-3 py-1.5',
              factoryPhase === 'planning' && 'border-amber-500/30 bg-amber-500/10',
              factoryPhase === 'executing' && 'border-indigo-500/30 bg-indigo-500/10',
              factoryPhase === 'verifying' && 'border-cyan-500/30 bg-cyan-500/10',
              factoryPhase === 'completed' && 'border-emerald-500/30 bg-emerald-500/10',
              factoryPhase === 'failed' && 'border-red-500/30 bg-red-500/10',
              factoryPhase === 'cancelled' && 'border-orange-500/30 bg-orange-500/10',
              factoryPhase === 'idle' && 'border-slate-500/30 bg-slate-500/10'
            )}
          >
            {(isRunActive || isLoading) ? (
              <Loader2 className={cn('h-4 w-4 animate-spin', phaseConfig.color)} />
            ) : (
              phaseConfig.icon
            )}
            <span className={cn('text-sm font-medium', phaseConfig.color)}>{phaseConfig.label}</span>
          </div>

          <StatusChip label="phase" value={currentRun?.phase || 'pending'} />
          <StatusChip label="status" value={currentRun?.status || 'idle'} />
          <StatusChip label="stage" value={currentRun?.current_stage || 'n/a'} />
          <StatusChip label="progress" value={`${Math.round(currentRun?.progress || 0)}%`} />

          <div className="ml-1 flex items-center gap-2">
            {canStart && onStart && (
              <Button
                size="sm"
                onClick={onStart}
                disabled={isLoading}
                className="bg-emerald-600 hover:bg-emerald-700"
              >
                {isLoading ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Play className="mr-1 h-4 w-4" />
                )}
                {isLoading ? '启动中...' : '启动'}
              </Button>
            )}
            {canCancel && onCancel && (
              <Button size="sm" variant="destructive" onClick={onCancel} disabled={isLoading}>
                <Square className="mr-1 h-4 w-4" />
                取消
              </Button>
            )}
          </div>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <section className="shrink-0 border-b border-white/10 bg-slate-950/70 px-4 py-3">
          <RoleLayerRail
            layers={roleLayers}
            activeLayer={activeLayerView.id}
            suggestedLayer={suggestedLayer}
            onSelect={setActiveLayer}
          />
        </section>

        <PanelGroup direction="horizontal" className="min-h-0 flex-1">
          <Panel defaultSize={72} minSize={48}>
            <section className="h-full min-w-0 overflow-hidden" data-testid="factory-focused-layer">
              {activeLayerView.id === 'pm' && (
                <PMWorkspace
                  tasks={pmWorkflowTasks}
                  pmState={null}
                  pmRunning={factoryPhase === 'planning'}
                  workspace={workspace}
                  onBackToMain={onBackToMain}
                  onTogglePm={() => {}}
                  onRunPmOnce={() => {}}
                  executionLogs={executionLogs}
                  llmStreamEvents={llmStreamEvents}
                  processStreamEvents={processStreamEvents}
                  currentPhase={workspacePhase}
                  factoryMode={true}
                />
              )}
              {activeLayerView.id === 'chief_engineer' && (
                <FactoryChiefEngineerLayer
                  workspace={workspace}
                  tasks={pmWorkflowTasks}
                  blueprintEvidence={blueprintEvidence}
                  roleStatus={getRunRole(currentRun?.roles, ['chief_engineer', 'chiefengineer', 'architect'])}
                  currentRun={currentRun}
                />
              )}
              {activeLayerView.id === 'director' && (
                <DirectorWorkspace
                  workspace={workspace}
                  onBackToMain={onBackToMain}
                  tasks={directorWorkflowTasks}
                  directorRunning={factoryPhase === 'executing'}
                  onToggleDirector={() => {}}
                  fileEditEvents={fileEditEvents}
                  executionLogs={executionLogs}
                  llmStreamEvents={llmStreamEvents}
                  processStreamEvents={processStreamEvents}
                  currentPhase={workspacePhase}
                  factoryMode={true}
                />
              )}
            </section>
          </Panel>

          <PanelResizeHandle className="w-1 bg-white/5 transition-colors hover:bg-emerald-500/30" />

          <Panel defaultSize={28} minSize={24} maxSize={38}>
            <FactoryOperationsRail
              currentRun={currentRun}
              factoryPhase={factoryPhase}
              workspacePhase={workspacePhase}
              activeLayer={activeLayerView.id}
              activityLogs={activityLogs}
              gateResults={gateResults}
              deliveryArtifacts={deliveryArtifacts}
              summaryMarkdown={summaryMarkdown}
              summaryRows={summaryRows}
              artifactErrorMessage={artifactErrorMessage}
              isArtifactsLoading={isArtifactsLoading}
              isRunning={isRunActive || isLoading}
            />
          </Panel>
        </PanelGroup>
      </main>
    </div>
  );
}

function RoleLayerRail({
  layers,
  activeLayer,
  suggestedLayer,
  onSelect,
}: {
  layers: RoleLayerView[];
  activeLayer: FactoryRoleLayer;
  suggestedLayer: FactoryRoleLayer;
  onSelect: (layer: FactoryRoleLayer) => void;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
          <Layers className="h-3.5 w-3.5 text-emerald-300" />
          <span>角色分层</span>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
          <Route className="h-3.5 w-3.5" />
          <span>PM</span>
          <ChevronRight className="h-3 w-3" />
          <span>Chief Engineer</span>
          <ChevronRight className="h-3 w-3" />
          <span>Director</span>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        {layers.map((layer) => (
          <button
            key={layer.id}
            type="button"
            onClick={() => onSelect(layer.id)}
            data-testid={`factory-role-layer-${layer.id}`}
            className={cn(
              'group min-h-[92px] rounded-lg border p-3 text-left transition-colors',
              activeLayer === layer.id ? layer.tone.active : layer.tone.idle
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <div className={cn('rounded-md border border-white/10 bg-white/10 p-1.5', layer.tone.text)}>
                  {layer.icon}
                </div>
                <div className="min-w-0">
                  <div className={cn('truncate text-sm font-semibold', layer.tone.text)}>{layer.title}</div>
                  <div className="truncate text-[11px] text-slate-500">{layer.subtitle}</div>
                </div>
              </div>
              <span className={cn('rounded-md border px-1.5 py-0.5 text-[10px] uppercase', roleStatusTone(layer.status))}>
                {layer.status}
              </span>
            </div>

            <div className="mt-3 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-[11px] text-slate-400">{layer.detail}</div>
                <div className="mt-1 font-mono text-[11px] text-slate-500">{layer.metric}</div>
              </div>
              {suggestedLayer === layer.id ? (
                <span className="shrink-0 rounded-md border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-200">
                  current
                </span>
              ) : null}
            </div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-900">
              <div
                className={cn('h-full rounded-full bg-gradient-to-r transition-all duration-500', layer.tone.progress)}
                style={{ width: `${layer.progress}%` }}
              />
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function FactoryChiefEngineerLayer({
  workspace,
  tasks,
  blueprintEvidence,
  roleStatus,
  currentRun,
}: {
  workspace: string;
  tasks: PmTask[];
  blueprintEvidence: BlueprintEvidenceView[];
  roleStatus: RunRoleStatus | null;
  currentRun: FactoryRunStatus | null;
}) {
  const candidateTasks = tasks.filter((task) => !hasBlueprintEvidence(task)).slice(0, 5);
  const status = roleStatus?.status || (blueprintEvidence.length > 0 ? 'ready' : 'waiting');
  const handoffReady = blueprintEvidence.length > 0 || normalizeToken(currentRun?.current_stage).includes('director');

  return (
    <div data-testid="factory-chief-layer" className="flex h-full flex-col overflow-hidden bg-gradient-to-br from-slate-950 via-slate-900 to-cyan-950/25">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-cyan-500/20 bg-slate-950/80 px-4">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-cyan-400/30 bg-cyan-500/10 text-cyan-100">
            <Brain className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-cyan-100">Chief Engineer</h2>
            <p className="text-[10px] uppercase tracking-wider text-cyan-400/70">Blueprint Handoff Layer</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={cn('rounded-md border px-2 py-1 text-[10px] uppercase tracking-wider', roleStatusTone(status))}>
            {status}
          </span>
          <span className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-slate-400">
            {workspace || 'workspace n/a'}
          </span>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_340px] gap-4 overflow-hidden p-4">
        <section className="min-h-0 overflow-auto rounded-lg border border-cyan-500/15 bg-white/[0.035]">
          <div className="border-b border-white/10 px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-100">施工蓝图证据</h3>
                <p className="mt-1 text-xs text-slate-500">展示任务合同字段和 Factory 运行时蓝图产物。</p>
              </div>
              <span className="rounded-md border border-cyan-500/25 bg-cyan-500/10 px-2 py-1 text-[10px] text-cyan-100">
                {blueprintEvidence.length} ready
              </span>
            </div>
          </div>

          <div className="space-y-3 p-4">
            {blueprintEvidence.length > 0 ? (
              blueprintEvidence.map((evidence) => {
                return (
                  <article key={`${evidence.source}-${evidence.id}-${evidence.path}`} className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 text-sm font-medium text-cyan-100">
                          <FileText className="h-4 w-4 shrink-0" />
                          <span className="truncate">{evidence.title}</span>
                        </div>
                        <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">
                          {evidence.summary}
                        </p>
                      </div>
                      <span className="shrink-0 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[10px] text-slate-300">
                        {evidence.source}
                      </span>
                    </div>
                    {evidence.path ? (
                      <div className="mt-2 truncate rounded-md border border-white/10 bg-slate-950/55 px-2 py-1 text-[11px] text-slate-400" title={evidence.path}>
                        {evidence.path}
                      </div>
                    ) : null}
                  </article>
                );
              })
            ) : (
              <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 p-4 text-sm text-amber-100">
                <div className="flex items-center gap-2 font-medium">
                  <AlertCircle className="h-4 w-4" />
                  暂无 Chief Engineer 蓝图证据
                </div>
                <p className="mt-2 text-xs leading-5 text-amber-100/75">
                  等待 PM/CE 链路写入 `blueprint_id`、`blueprint_path` 或 `runtime_blueprint_path` 后，再开放 Director 交接判断。
                </p>
              </div>
            )}
          </div>
        </section>

        <aside className="flex min-h-0 flex-col gap-3 overflow-auto">
          <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <Route className="h-3.5 w-3.5 text-cyan-300" />
              交接状态
            </div>
            <div className="space-y-2">
              <MetricRow label="Blueprint evidence" value={String(blueprintEvidence.length)} tone="text-cyan-200" />
              <MetricRow label="Candidate tasks" value={String(candidateTasks.length)} tone="text-amber-200" />
              <MetricRow label="Director handoff" value={handoffReady ? 'ready' : 'waiting'} tone={handoffReady ? 'text-emerald-200' : 'text-amber-200'} />
              <MetricRow label="Factory stage" value={currentRun?.current_stage || 'n/a'} tone="text-slate-300" />
            </div>
          </section>

          <section className="min-h-0 rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
              <ClipboardList className="h-3.5 w-3.5 text-amber-300" />
              待蓝图任务
            </div>
            <div className="space-y-2">
              {candidateTasks.length > 0 ? (
                candidateTasks.map((task) => (
                  <div key={task.id} className="rounded-md border border-white/10 bg-slate-950/45 px-2 py-2">
                    <div className="truncate text-xs font-medium text-slate-200">{task.title || task.id}</div>
                    <div className="mt-1 truncate text-[10px] text-slate-500">{task.goal || task.summary || task.description || '等待蓝图输入'}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-2 text-xs text-emerald-100">
                  当前任务均已具备蓝图字段或暂无 PM 任务。
                </div>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

function FactoryOperationsRail({
  currentRun,
  factoryPhase,
  workspacePhase,
  activeLayer,
  activityLogs,
  gateResults,
  deliveryArtifacts,
  summaryMarkdown,
  summaryRows,
  artifactErrorMessage,
  isArtifactsLoading,
  isRunning,
}: {
  currentRun: FactoryRunStatus | null;
  factoryPhase: FactoryPhase;
  workspacePhase: string;
  activeLayer: FactoryRoleLayer;
  activityLogs: LogEntry[];
  gateResults: FactoryRunStatus['gates'];
  deliveryArtifacts: FactoryRunArtifact[];
  summaryMarkdown: string;
  summaryRows: Array<[string, string]>;
  artifactErrorMessage: string;
  isArtifactsLoading: boolean;
  isRunning: boolean;
}) {
  return (
    <aside className="flex h-full min-w-0 flex-col overflow-hidden border-l border-white/10 bg-slate-950/80" data-testid="factory-operations-rail">
      <section className="shrink-0 border-b border-white/10 p-3">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
            <Activity className="h-3.5 w-3.5 text-emerald-300" />
            Operations
          </div>
          <span className={cn('rounded-md border px-1.5 py-0.5 text-[10px] uppercase', roleStatusTone(currentRun?.status || 'idle'))}>
            {currentRun?.status || 'idle'}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <MiniMetric label="Layer" value={activeLayer === 'chief_engineer' ? 'CE' : activeLayer.toUpperCase()} />
          <MiniMetric label="Phase" value={factoryPhase} />
          <MiniMetric label="Run" value={currentRun?.run_id || 'n/a'} />
          <MiniMetric label="Progress" value={`${Math.round(currentRun?.progress || 0)}%`} />
        </div>
      </section>

      <section className="min-h-[260px] flex-[1.05] overflow-hidden border-b border-white/10">
        <RealtimeActivityPanel
          executionLogs={activityLogs}
          llmStreamEvents={[]}
          processStreamEvents={[]}
          currentPhase={workspacePhase}
          isRunning={isRunning}
          role={activeLayer === 'pm' ? 'pm' : 'director'}
        />
      </section>

      <section className="min-h-0 flex-1 overflow-y-auto p-3">
        <FactoryAuditEvidencePanel
          gateResults={gateResults}
          deliveryArtifacts={deliveryArtifacts}
          summaryMarkdown={summaryMarkdown}
          summaryRows={summaryRows}
          artifactErrorMessage={artifactErrorMessage}
          isArtifactsLoading={isArtifactsLoading}
          failure={currentRun?.failure}
        />
      </section>
    </aside>
  );
}

function FactoryAuditEvidencePanel({
  gateResults,
  deliveryArtifacts,
  summaryMarkdown,
  summaryRows,
  artifactErrorMessage,
  isArtifactsLoading,
  failure,
}: {
  gateResults: FactoryRunStatus['gates'];
  deliveryArtifacts: FactoryRunArtifact[];
  summaryMarkdown: string;
  summaryRows: Array<[string, string]>;
  artifactErrorMessage: string;
  isArtifactsLoading: boolean;
  failure?: FactoryRunStatus['failure'];
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-emerald-300">
        <ShieldCheck className="h-4 w-4" />
        <h3 className="text-xs font-semibold uppercase tracking-wider">总监审计 / 交付证据</h3>
      </div>

      <section>
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-300">
          <BadgeCheck className="h-3.5 w-3.5 text-cyan-300" />
          <span>Gates</span>
        </div>
        <div className="space-y-2">
          {gateResults.length > 0 ? (
            gateResults.map((gate) => (
              <div key={gate.gate_name} className={cn('rounded-lg border px-3 py-2 text-xs', gateTone(gate))}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{gate.gate_name}</span>
                  <span className="shrink-0 uppercase">{gate.status || 'n/a'}</span>
                </div>
                <div className="mt-1 flex items-center justify-between gap-2 text-[10px] opacity-80">
                  <span>{gate.passed ? 'passed' : 'blocked'}</span>
                  {typeof gate.score === 'number' && <span>score {gate.score}</span>}
                </div>
                {gate.message && (
                  <p className="mt-1 text-[10px] leading-relaxed opacity-80">{gate.message}</p>
                )}
              </div>
            ))
          ) : (
            <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
              <p className="text-xs text-slate-400">暂无质量门结果</p>
              <p className="mt-1 text-[10px] text-slate-600">等待可审计门禁记录</p>
            </div>
          )}
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-300">
          <PackageCheck className="h-3.5 w-3.5 text-emerald-300" />
          <span>Artifacts</span>
        </div>
        <div className="space-y-2">
          {isArtifactsLoading && (
            <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-300" />
              <span>同步证据中</span>
            </div>
          )}
          {artifactErrorMessage && (
            <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              产物同步失败: {artifactErrorMessage}
            </div>
          )}
          {deliveryArtifacts.length > 0 ? (
            deliveryArtifacts.map((artifact) => (
              <div key={`${artifact.path}-${artifact.name}`} className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <FileText className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                    <span className="truncate text-xs text-slate-200">{artifact.name}</span>
                  </div>
                  <span className="shrink-0 text-[10px] text-slate-500">{formatBytes(artifact.size)}</span>
                </div>
                <p className="mt-1 break-all text-[10px] leading-relaxed text-slate-500">{artifact.path}</p>
              </div>
            ))
          ) : (
            <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
              <p className="text-xs text-slate-400">暂无交付产物</p>
              <p className="mt-1 text-[10px] text-slate-600">等待 Director 证据文件</p>
            </div>
          )}
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-300">
          <FileCode className="h-3.5 w-3.5 text-purple-300" />
          <span>Summary</span>
        </div>
        {summaryMarkdown ? (
          <div className="max-h-28 overflow-y-auto rounded-lg border border-white/10 bg-white/5 px-3 py-2">
            <p className="whitespace-pre-line text-xs leading-relaxed text-slate-300">{summaryMarkdown}</p>
          </div>
        ) : summaryRows.length > 0 ? (
          <div className="space-y-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs">
            {summaryRows.map(([key, value]) => (
              <div key={key} className="flex justify-between gap-2">
                <span className="text-slate-500">{key}</span>
                <span className="min-w-0 truncate text-right text-slate-300">{value}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
            <p className="text-xs text-slate-400">暂无交付摘要</p>
            <p className="mt-1 text-[10px] text-slate-600">等待终态摘要</p>
          </div>
        )}
      </section>

      {failure && (
        <section role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 p-3">
          <div className="flex items-center gap-2 text-red-300">
            <AlertCircle className="h-4 w-4" />
            <span className="text-sm font-medium">失败信息</span>
          </div>
          <p className="mt-2 text-xs text-red-200">{failure.detail}</p>
          {failure.suggested_action && (
            <p className="mt-2 text-xs text-red-300/80">建议: {failure.suggested_action}</p>
          )}
        </section>
      )}
    </div>
  );
}

function StatusChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="max-w-24 truncate text-xs text-slate-200">{value}</div>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="truncate text-xs font-medium text-slate-200">{value}</div>
    </div>
  );
}

function MetricRow({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-slate-950/45 px-2 py-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className={cn('min-w-0 truncate text-right font-medium', tone)}>{value}</span>
    </div>
  );
}
