/** FactoryWorkspace - 无人值守开发工厂工作区 */
import { useMemo } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import {
  AlertCircle,
  Brain,
  CheckCircle2,
  FileCode,
  Hammer,
  Loader2,
  RotateCcw,
  Square,
  Terminal,
  Play,
  XCircle,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { PMWorkspace } from '@/app/components/pm';
import { DirectorWorkspace } from '@/app/components/director';
import { RealtimeActivityPanel } from '@/app/components/common/RealtimeActivityPanel';
import type { FileEditEvent } from '@/app/hooks/useRuntime';
import type { FactoryAuditEvent, FactoryRunStatus } from '@/hooks/useFactory';
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
  onStart?: () => void;
  onCancel?: () => void;
  isLoading?: boolean;
}

type FactoryPhase = 'idle' | 'planning' | 'executing' | 'verifying' | 'completed' | 'failed' | 'cancelled';

function normalizeToken(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase();
}

function mapRunToFactoryPhase(run?: FactoryRunStatus | null): FactoryPhase {
  const status = normalizeToken(run?.status);
  const phase = normalizeToken(run?.phase);

  if (status === 'cancelled' || phase === 'cancelled') return 'cancelled';
  if (status === 'failed' || phase === 'failed') return 'failed';
  if (phase === 'completed' || status === 'completed') return 'completed';
  if (['verification', 'qa_gate', 'handover'].includes(phase)) return 'verifying';
  if (phase === 'implementation') return 'executing';
  if (['architect', 'planning', 'pending', 'intake', 'docs_check'].includes(phase)) return 'planning';
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

const PHASE_CONFIG: Record<FactoryPhase, { label: string; color: string; icon: React.ReactNode }> = {
  idle: { label: '等待启动', color: 'text-slate-400', icon: <Hammer className="w-4 h-4" /> },
  planning: { label: '规划中', color: 'text-purple-400', icon: <Brain className="w-4 h-4" /> },
  executing: { label: '执行中', color: 'text-amber-400', icon: <Terminal className="w-4 h-4" /> },
  verifying: { label: '验证中', color: 'text-cyan-400', icon: <CheckCircle2 className="w-4 h-4" /> },
  completed: { label: '已完成', color: 'text-emerald-400', icon: <CheckCircle2 className="w-4 h-4" /> },
  failed: { label: '失败', color: 'text-red-400', icon: <AlertCircle className="w-4 h-4" /> },
  cancelled: { label: '已取消', color: 'text-orange-400', icon: <XCircle className="w-4 h-4" /> },
};

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
  const completedPmTasks = pmWorkflowTasks.filter((task) => task.status === 'completed' || task.done).length;
  const activeDirectorTasks = directorWorkflowTasks.filter((task) => {
    const status = normalizeToken(String(task.status || task.state || ''));
    return status === 'running' || status === 'in_progress';
  }).length;

  const activityLogs = useMemo(() => toActivityLogs(events), [events]);

  return (
    <div className="h-screen flex flex-col bg-slate-950">
      <header className="h-16 flex items-center justify-between px-4 border-b border-emerald-500/20 bg-gradient-to-r from-slate-900 via-slate-900 to-emerald-950/20">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onBackToMain}
            className="p-2 rounded-lg hover:bg-white/10 transition-colors"
          >
            <RotateCcw className="w-4 h-4 text-slate-400" />
          </button>
          <div className="w-px h-6 bg-white/10" />
          <Hammer className="w-5 h-5 text-emerald-400" />
          <div>
            <h1 className="text-sm font-semibold text-slate-200">Factory 模式</h1>
            <p className="text-[10px] text-slate-500">无人值守开发工厂</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div
            className={cn(
              'flex items-center gap-2 rounded-lg border px-3 py-1.5',
              factoryPhase === 'planning' && 'border-purple-500/30 bg-purple-500/10',
              factoryPhase === 'executing' && 'border-amber-500/30 bg-amber-500/10',
              factoryPhase === 'verifying' && 'border-cyan-500/30 bg-cyan-500/10',
              factoryPhase === 'completed' && 'border-emerald-500/30 bg-emerald-500/10',
              factoryPhase === 'failed' && 'border-red-500/30 bg-red-500/10',
              factoryPhase === 'cancelled' && 'border-orange-500/30 bg-orange-500/10',
              factoryPhase === 'idle' && 'border-slate-500/30 bg-slate-500/10'
            )}
          >
            {(isRunActive || isLoading) ? (
              <Loader2 className={cn('w-4 h-4 animate-spin', phaseConfig.color)} />
            ) : (
              phaseConfig.icon
            )}
            <span className={cn('text-sm font-medium', phaseConfig.color)}>{phaseConfig.label}</span>
          </div>

          <StatusChip label="phase" value={currentRun?.phase || 'pending'} />
          <StatusChip label="status" value={currentRun?.status || 'idle'} />
          <StatusChip label="stage" value={currentRun?.current_stage || 'n/a'} />
          <StatusChip label="progress" value={`${Math.round(currentRun?.progress || 0)}%`} />

          <div className="flex items-center gap-2">
            {canStart && onStart && (
              <Button
                size="sm"
                onClick={onStart}
                disabled={isLoading}
                className="bg-emerald-600 hover:bg-emerald-700"
              >
                {isLoading ? (
                  <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                ) : (
                  <Play className="w-4 h-4 mr-1" />
                )}
                {isLoading ? '启动中...' : '启动'}
              </Button>
            )}
            {canCancel && onCancel && (
              <Button size="sm" variant="destructive" onClick={onCancel} disabled={isLoading}>
                <Square className="w-4 h-4 mr-1" />
                取消
              </Button>
            )}
          </div>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        <aside className="w-72 border-r border-white/5 bg-slate-950/50 p-4 space-y-6">
          <section>
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-4">流程阶段</h3>
            <div className="space-y-2">
              {[
                { key: 'planning', label: '规划 / 架构' },
                { key: 'executing', label: '实现 / 调度' },
                { key: 'verifying', label: '验证 / QA' },
              ].map((item) => (
                <div
                  key={item.key}
                  className={cn(
                    'flex items-center gap-3 px-3 py-2 rounded-lg border transition-all',
                    factoryPhase === item.key
                      ? 'border-emerald-500/50 bg-emerald-500/10'
                      : 'border-white/5 bg-white/5'
                  )}
                >
                  <div
                    className={cn(
                      'w-2 h-2 rounded-full',
                      factoryPhase === item.key ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'
                    )}
                  />
                  <span
                    className={cn(
                      'text-sm',
                      factoryPhase === item.key ? 'text-emerald-300' : 'text-slate-500'
                    )}
                  >
                    {item.label}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section>
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-4">角色状态</h3>
            <div className="space-y-2">
              {Object.entries(currentRun?.roles || {}).map(([roleKey, role]) => (
                <div key={roleKey} className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-slate-200">{role.role}</span>
                    <span className="text-xs text-slate-400">{role.status}</span>
                  </div>
                  {role.detail && (
                    <p className="mt-1 text-xs text-slate-500">{role.detail}</p>
                  )}
                </div>
              ))}
              {!currentRun?.roles || Object.keys(currentRun.roles).length === 0 ? (
                <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-500">
                  暂无角色状态
                </div>
              ) : null}
            </div>
          </section>

          <section>
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-4">任务概览</h3>
            <div className="space-y-2 text-xs">
              <div className="flex justify-between text-slate-500">
                <span>PM 章奏</span>
                <span className="text-slate-300">{pmWorkflowTasks.length}</span>
              </div>
              <div className="flex justify-between text-slate-500">
                <span>已完成章奏</span>
                <span className="text-emerald-400">{completedPmTasks}</span>
              </div>
              <div className="flex justify-between text-slate-500">
                <span>工部队列</span>
                <span className="text-cyan-400">{directorWorkflowTasks.length}</span>
              </div>
              <div className="flex justify-between text-slate-500">
                <span>工部执行中</span>
                <span className="text-amber-400">{activeDirectorTasks}</span>
              </div>
            </div>
          </section>

          {currentRun?.failure && (
            <section className="rounded-xl border border-red-500/30 bg-red-500/10 p-3">
              <div className="flex items-center gap-2 text-red-300">
                <AlertCircle className="w-4 h-4" />
                <span className="text-sm font-medium">失败信息</span>
              </div>
              <p className="mt-2 text-xs text-red-200">{currentRun.failure.detail}</p>
              {currentRun.failure.suggested_action && (
                <p className="mt-2 text-xs text-red-300/80">
                  建议: {currentRun.failure.suggested_action}
                </p>
              )}
            </section>
          )}
        </aside>

        <PanelGroup direction="horizontal" className="flex-1">
          <Panel defaultSize={70} minSize={45}>
            <div className="h-full flex">
              <div className="w-1/2 border-r border-white/5">
                <div className="h-full overflow-hidden">
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
                </div>
              </div>
              <div className="w-1/2">
                <div className="h-full overflow-hidden">
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
                </div>
              </div>
            </div>
          </Panel>

          <PanelResizeHandle className="w-1 bg-white/5 hover:bg-emerald-500/30 transition-colors" />

          <Panel defaultSize={30} minSize={25}>
            <RealtimeActivityPanel
              executionLogs={activityLogs}
              llmStreamEvents={[]}
              processStreamEvents={[]}
              currentPhase={workspacePhase}
              isRunning={isRunActive || isLoading}
              role="director"
            />
          </Panel>
        </PanelGroup>
      </div>
    </div>
  );
}

function StatusChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="text-xs text-slate-200">{value}</div>
    </div>
  );
}
