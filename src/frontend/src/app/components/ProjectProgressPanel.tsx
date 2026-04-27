import { useState } from 'react';
import { Activity, AlertTriangle, ArrowRight, CheckCircle, ChevronDown, ChevronRight, Clock, ListChecks, Target } from 'lucide-react';
import type { PmTask, SuccessStats, PmState } from '@/types/task';
import type { EngineStatus } from '@/app/types/appContracts';
import {
  ProgressBar,
  CurrentTaskCard,
  TaskList,
} from './ProjectProgressPanel/index';
import { PlanBoard } from './PlanBoard';
import { UI_TERMS } from '@/app/constants/uiTerminology';
import { StatusBadge } from '@/app/components/ui/badge';
import { PhaseIndicator, QualityGateCard, ExecutionLog } from './pm';
import type { QualityGateData, LogEntry, Phase } from './pm';


interface ProjectProgressPanelProps {
  tasks: PmTask[];
  directorTasks?: PmTask[] | null;
  pmState?: PmState | null;
  focus?: string | null;
  notes?: string | null;
  goals?: string[] | null;
  planText?: string | null;
  planMtime?: string | null;
  planTextNormalized?: boolean;
  successStats?: SuccessStats | null;
  pmRunning?: boolean;
  engineStatus?: EngineStatus | null;
  onOpenDocsPanel?: () => void;
  className?: string;
  // 新增：详细状态
  qualityGate?: QualityGateData | null;
  executionLogs?: LogEntry[];
  currentPhase?: string;
  directorTaskSource?: 'realtime' | 'snapshot';
  directorRealtimeConnected?: boolean;
}

const toText = (value: unknown): string => (typeof value === 'string' ? value.trim() : '');

const clampText = (value: string, maxLen: number): string => {
  const text = value.trim();
  if (!text || text.length <= maxLen) return text;
  return text.slice(0, Math.max(0, maxLen - 1)).trimEnd() + '...';
};

const isTaskDone = (task: PmTask): boolean => {
  if (task.completed || task.done) return true;
  const status = String(task.status || task.state || '').toLowerCase();
  return ['done', 'complete', 'completed', 'success', 'passed', 'pass', 'ok'].some((key) =>
    status.includes(key)
  );
};

const isTaskActive = (task: PmTask): boolean => {
  const status = String(task.status || task.state || '').toLowerCase();
  return ['in_progress', 'running', 'executing'].some((key) => status.includes(key));
};

const taskKey = (task: PmTask): string => task.id || toText(task.title) || toText(task.goal);

const pickTaskSummary = (task: PmTask): string => task.summary || task.title || task.goal || '';

export function ProjectProgressPanel({
  tasks,
  directorTasks,
  pmState,
  focus,
  notes,
  goals,
  planText,
  planMtime,
  planTextNormalized,
  successStats,
  pmRunning,
  engineStatus,
  onOpenDocsPanel,
  className,
  // 新增
  qualityGate,
  executionLogs = [],
  currentPhase = 'idle',
  directorTaskSource = 'snapshot',
  directorRealtimeConnected = false,
}: ProjectProgressPanelProps) {
  const [isGoalsExpanded, setIsGoalsExpanded] = useState(true);
  const normalizedTasks = Array.isArray(tasks)
    ? tasks.filter((task): task is PmTask => Boolean(task && typeof task === 'object'))
    : [];
  const normalizedDirectorTasks = Array.isArray(directorTasks)
    ? directorTasks.filter((task): task is PmTask => Boolean(task && typeof task === 'object'))
    : [];
  const totalTasks = normalizedTasks.length;
  const completedIdsRaw = Array.isArray(pmState?.completed_task_ids) ? pmState.completed_task_ids : [];
  const completedIds = completedIdsRaw
    .map((item: unknown) => (typeof item === 'string' ? item.trim() : ''))
    .filter((item: string) => item.length > 0) as string[];

  const completedSet = new Set(completedIds);
  const completedInList = normalizedTasks.filter((task) => completedSet.has(taskKey(task))).length;
  const doneCount = normalizedTasks.filter((task) => isTaskDone(task) || completedSet.has(taskKey(task))).length;
  const reportedCompletedRaw = pmState?.completed_task_count;
  const reportedCompletedCount =
    typeof reportedCompletedRaw === 'number'
      ? reportedCompletedRaw
      : typeof reportedCompletedRaw === 'string'
        ? Number(reportedCompletedRaw)
        : null;
  const reportedCompleted =
    reportedCompletedCount !== null && Number.isFinite(reportedCompletedCount)
      ? Math.max(0, reportedCompletedCount)
      : 0;
  const completedCount = totalTasks > 0
    ? Math.max(doneCount, completedInList, reportedCompleted)
    : reportedCompleted > 0
      ? reportedCompleted
      : completedSet.size;
  const lastTaskId = toText(pmState?.last_director_task_id);
  const lastTaskTitle = toText(pmState?.last_director_task_title);
  const lastStatus = toText(pmState?.last_director_status).toLowerCase();
  const lastUpdated = toText(pmState?.last_updated_ts);
  const iterationRaw = pmState?.pm_iteration;
  const iteration =
    typeof iterationRaw === 'number'
      ? iterationRaw
      : typeof iterationRaw === 'string'
        ? Number(iterationRaw)
        : null;

  // 从 Engine 状态获取 Director 当前任务（实时来源优先）
  const engineDirectorTaskId = engineStatus?.roles?.Director?.task_id;
  const engineDirectorTaskTitle = engineStatus?.roles?.Director?.task_title;
  const engineDirectorStatus = engineStatus?.roles?.Director?.status;
  const engineDirectorDetail = engineStatus?.roles?.Director?.detail;

  // PM 当前任务高亮策略：Engine task_id/title 命中 > nextPending 推断 > lastTaskId/Title 回退
  let highlightedTask: PmTask | undefined;
  let currentIndex = -1;

  if ((engineDirectorTaskId || engineDirectorTaskTitle) && normalizedTasks.length > 0) {
    // 策略1：Engine 的 Director task_id/title 命中 PM 任务
    const engineTaskIndex = normalizedTasks.findIndex(
      (task) => task.id === engineDirectorTaskId || task.title === engineDirectorTaskTitle
    );
    if (engineTaskIndex >= 0) {
      highlightedTask = normalizedTasks[engineTaskIndex];
      currentIndex = engineTaskIndex;
    }
  }

  if (!highlightedTask) {
    // 策略2：nextPending 推断
    const nextPendingTask = normalizedTasks.find((task) => {
      const key = taskKey(task);
      if (!key) return false;
      return !completedSet.has(key) && !isTaskDone(task);
    });
    if (nextPendingTask) {
      highlightedTask = nextPendingTask;
      currentIndex = normalizedTasks.findIndex((t) => t.id === nextPendingTask.id);
    }
  }

  if (!highlightedTask) {
    // 策略3：lastTaskId/Title 回退
    currentIndex = normalizedTasks.findIndex(
      (task) => (lastTaskId && task.id === lastTaskId) || (lastTaskTitle && task.title === lastTaskTitle),
    );
    if (currentIndex >= 0) {
      highlightedTask = normalizedTasks[currentIndex];
    }
  }

  const liveDirectorTask = normalizedDirectorTasks.find((task) => isTaskActive(task))
    ?? normalizedDirectorTasks.find((task) => !isTaskDone(task));
  const directorCompletedCount = normalizedDirectorTasks.filter((task) => isTaskDone(task)).length;
  const directorTaskLabel = liveDirectorTask?.title
    || liveDirectorTask?.goal
    || engineDirectorTaskTitle
    || lastTaskTitle;
  const positionIndex = currentIndex >= 0 ? currentIndex : totalTasks > 0 ? 0 : -1;

  // 状态展示：pm_state 缺失时回退使用 Engine Director 状态
  const effectiveStatus = lastStatus || engineDirectorStatus?.toLowerCase() || '';
  const effectiveDetail = toText(pmState?.last_director_detail) || engineDirectorDetail || '';

  let progress = 0;
  let progressHint = '待 PM 出具任务';
  let progressMode: 'done' | 'position' | 'success' | 'idle' = 'idle';

  if (totalTasks > 0 && completedCount > 0) {
    progress = completedCount / totalTasks;
    progressHint = `已完成 ${Math.min(completedCount, totalTasks)}/${totalTasks}`;
    progressMode = 'done';
  } else if (totalTasks > 0 && positionIndex >= 0) {
    progress = (positionIndex + 1) / totalTasks;
    progressHint = `当前任务 ${positionIndex + 1}/${totalTasks}（估算）`;
    progressMode = 'position';
  } else if (typeof successStats?.rate === 'number') {
    progress = successStats.rate;
    progressHint = `历史成功率 ${Math.round(progress * 100)}%（估算）`;
    progressMode = 'success';
  }

  progress = Math.max(0, Math.min(1, progress));

  const statusIcon =
    effectiveStatus === 'success' ? (
      <CheckCircle className="size-4 text-emerald-300" />
    ) : effectiveStatus === 'blocked' ? (
      <AlertTriangle className="size-4 text-amber-300" />
    ) : (
      <Activity className="size-4 text-slate-300" />
    );

  const focusText = focus ? clampText(focus, 160) : '';
  const notesText = notes ? clampText(notes, 180) : '';
  const currentSummary = clampText(directorTaskLabel || '', 160);
  const goalList = Array.isArray(goals) ? goals.filter((item) => typeof item === 'string' && item.trim().length > 0) : [];
  const directorQueueHint = normalizedDirectorTasks.length > 0
    ? `${directorCompletedCount}/${normalizedDirectorTasks.length} Director queue 已完成`
    : directorTaskSource === 'realtime'
      ? directorRealtimeConnected
        ? 'Director live queue 为空'
        : 'Director live queue 已断开'
      : 'Director queue 待同步';

  return (
    <div
      data-testid="project-progress-panel"
      className={`border-b border-white/5 bg-transparent px-5 py-4 flex flex-col min-h-0 overflow-y-auto ${className || ''}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="flex size-10 items-center justify-center rounded-xl bg-white/5 text-accent">
            <Target className="size-5" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-heading font-bold text-text-main">PM 政务进度</span>
              {pmRunning ? (
                <StatusBadge color="success" variant="dot" pulse className="animate-pulse shadow-[0_0_8px_rgba(90,138,106,0.3)]">
                  {UI_TERMS.states.running}
                </StatusBadge>
              ) : (
                <StatusBadge color="default" variant="soft">{UI_TERMS.states.idle}</StatusBadge>
              )}
              {iteration !== null && Number.isFinite(iteration) ? (
                <StatusBadge color="accent" variant="soft">
                  轮次 {iteration}
                </StatusBadge>
              ) : null}
              <StatusBadge
                color={
                  directorTaskSource === 'realtime'
                    ? directorRealtimeConnected
                      ? 'accent'
                      : 'warning'
                    : 'default'
                }
                variant="dot"
                pulse={directorTaskSource === 'realtime' && directorRealtimeConnected}
              >
                {directorTaskSource === 'realtime'
                  ? directorRealtimeConnected
                    ? 'Director live queue'
                    : 'Director live 断线'
                  : 'Director snapshot 回退'}
              </StatusBadge>
            </div>
            <div className="mt-1 text-xs text-text-muted">
              {focusText || notesText ? (
                <>
                  {focusText ? <span>Focus: <span className="text-text-main">{focusText}</span></span> : null}
                  {focusText && notesText ? <span className="mx-2 text-white/10">|</span> : null}
                  {notesText ? <span>批注: {notesText}</span> : null}
                </>
              ) : (
                <span>PM 正在整理任务</span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {onOpenDocsPanel ? (
            <button
              type="button"
              data-testid="open-docs-init"
              onClick={onOpenDocsPanel}
              className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-200 transition-colors hover:bg-emerald-500/20"
            >
              <span>生成计划</span>
              <ArrowRight className="size-3" />
            </button>
          ) : null}
          <div
            className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-text-main backdrop-blur-sm"
            title={effectiveDetail || undefined}
          >
            {statusIcon}
            <StatusBadge
              color={
                effectiveStatus === 'success' ? 'success'
                : effectiveStatus === 'blocked' ? 'warning'
                : effectiveStatus === 'failure' || effectiveStatus === 'failed' ? 'error'
                : 'default'
              }
              variant="outlined"
              className="font-mono uppercase"
            >
              {effectiveStatus || '未有回执'}
            </StatusBadge>
            {lastUpdated ? (
              <>
                <span className="text-white/10">|</span>
                <Clock className="size-3 text-text-dim" />
                <span className="text-text-dim">{lastUpdated}</span>
              </>
            ) : null}
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_300px]">
        <ProgressBar
          progress={progress}
          progressHint={progressHint}
          progressMode={progressMode}
          totalTasks={totalTasks}
          completedCount={completedCount}
          successRate={successStats?.rate}
        />

        <div className="rounded-2xl border border-white/10 bg-white/5 p-4 backdrop-blur-md hover:border-accent/30 transition-all flex flex-col">
           <CurrentTaskCard
              currentSummary={currentSummary}
              lastTaskId={liveDirectorTask?.id || lastTaskId}
           />
           <div className="mt-3 text-xs text-text-muted">
             <span className="font-mono">{directorQueueHint}</span>
           </div>
        </div>
      </div>

      {/* 阶段指示器 */}
      {pmRunning && currentPhase && currentPhase !== 'idle' && (
        <div className="mt-4">
          <PhaseIndicator
            currentPhase={currentPhase as Phase}
            qualityScore={qualityGate?.score}
            retryAttempt={qualityGate?.attempt}
            maxRetries={qualityGate?.maxAttempts}
          />
        </div>
      )}

      {/* 质量门控卡片 */}
      {pmRunning && qualityGate && currentPhase === 'planning' && (
        <div className="mt-4">
          <QualityGateCard data={qualityGate} />
        </div>
      )}

      {/* 执行日志 */}
      {pmRunning && executionLogs.length > 0 && (
        <div className="mt-4">
          <ExecutionLog logs={executionLogs} maxHeight="180px" />
        </div>
      )}

      {/* 可折叠的 Focus 总览 */}
      {goalList.length > 0 && (
        <div className="mt-4 rounded-2xl border border-white/5 bg-white/5 overflow-hidden">
          <button
            onClick={() => setIsGoalsExpanded(!isGoalsExpanded)}
            className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/5 transition-colors"
          >
            <div className="flex items-center gap-2">
              {isGoalsExpanded ? (
                <ChevronDown className="w-4 h-4 text-text-muted" />
              ) : (
                <ChevronRight className="w-4 h-4 text-text-muted" />
              )}
              <span className="text-xs font-medium uppercase tracking-wide text-text-muted">Focus 总览</span>
            </div>
            <span className="text-xs font-mono text-text-muted">{goalList.length} 项</span>
          </button>
          
          {isGoalsExpanded && (
            <div className="px-4 pb-4 max-h-40 overflow-auto custom-scrollbar">
              <div className="mt-2 space-y-2 text-xs text-text-main">
                {goalList.map((item, idx) => (
                  <div key={`${idx}-${item}`} className="flex items-start gap-2">
                    <span className="mt-0.5 text-accent font-mono text-[10px]">{idx + 1}.</span>
                    <span className="leading-relaxed">{item}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="mt-4">
        <PlanBoard
          planText={planText ?? ''}
          planMtime={planMtime}
          planTextNormalized={planTextNormalized}
        />
      </div>

      <div className="mt-4 flex min-h-0 flex-1 flex-col">
        <div className="mb-2 flex items-center justify-between text-xs text-text-muted">
          <div className="flex items-center gap-2">
            <ListChecks className="size-4 text-text-dim" />
            <span className="font-medium uppercase tracking-wide">任务队列（PM → Director）</span>
          </div>
          <span className="font-mono">{totalTasks ? `${totalTasks} \u9879` : '\u6682\u65e0\u4efb\u52a1'}</span>
        </div>
        <TaskList
          tasks={normalizedTasks}
          completedSet={completedSet}
          currentTaskKey={highlightedTask ? taskKey(highlightedTask) : undefined}
          taskKey={taskKey}
          isTaskDone={isTaskDone}
          clampText={clampText}
        />
      </div>
    </div>
  );
}

