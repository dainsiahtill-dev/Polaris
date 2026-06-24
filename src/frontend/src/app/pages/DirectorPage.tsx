/**
 * Director 工作区页面组件
 *
 * 展示 Director 任务执行界面
 */

import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { DirectorWorkspace } from '@/app/components/director';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { Toaster } from '@/app/components/ui/sonner';
import { getRoleLlmBlockedReason } from '@/app/hooks/useLlmRuntimeGate';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntime';

export interface DirectorPageProps {
  /** 工作区路径 */
  workspace: string;
  /** 任务列表 */
  tasks: PmTask[];
  /** Worker 列表 */
  workers?: RuntimeWorkerState[];
  /** Director 是否运行中 */
  directorRunning: boolean;
  /** PM 是否运行中 */
  pmRunning?: boolean;
  /** 是否正在启动 */
  isStarting: boolean;
  /** 是否正在停止 */
  isStopping: boolean;
  /** Director 切换回调 */
  onToggleDirector: () => void | boolean | Promise<void | boolean>;
  /** 外部统一计算的 Director 启动阻断原因 */
  directorStartBlockedReason?: string;
  /** 打开系统配置 */
  onOpenSettings?: () => void;
  /** 当前任务 ID */
  currentTaskId?: string | null;
  /** 当前任务标题 */
  currentTaskTitle?: string | null;
  /** 当前任务状态 */
  currentTaskStatus?: string | null;
  /** 返回主界面回调 */
  onBackToMain: () => void;
  /** 文件编辑事件 */
  fileEditEvents?: unknown[];
  /** 执行日志 */
  executionLogs?: LogEntry[];
  /** LLM 流事件 */
  llmStreamEvents?: LogEntry[];
  /** 进程流事件 */
  processStreamEvents?: LogEntry[];
  /** 当前阶段 */
  currentPhase?: string;
  /** 任务进度映射 */
  taskProgressMap?: unknown;
  /** 任务追踪映射 */
  taskTraceMap?: Parameters<typeof DirectorWorkspace>[0]['taskTraceMap'];
  /** WebSocket 连接状态 */
  websocketLive: boolean;
  /** WebSocket 重连状态 */
  websocketReconnecting: boolean;
  /** WebSocket 重连次数 */
  websocketAttemptCount: number;
  /** 内部测试模式下才允许展示 Factory Bench 状态。 */
  internalBenchEnabled?: boolean;
  /** LLM 运行时状态 */
  llmRuntimeState: {
    state: 'READY' | 'BLOCKED' | 'DEGRADED' | 'UNKNOWN';
    blockedRoles: string[];
    requiredRoles: string[];
    lastUpdated: string | null;
  };
  /** 是否需要代理 */
  agentsRequired?: boolean;
  /** 草稿是否就绪 */
  agentsDraftReady?: boolean;
  /** 草稿是否生成失败 */
  agentsDraftFailed?: boolean;
  /** 质量门 */
  qualityGate?: unknown;
  /** 平台 Run Ledger 投影 */
  controlPlaneProjection?: Parameters<typeof LlmRuntimeOverlay>[0]['controlPlaneProjection'];
  /** 错误通知回调 */
  notifyError: (message: string) => void;
}

function resolveDirectorStartBlockedReason({
  directorRunning,
  agentsRequired,
  agentsDraftReady,
  agentsDraftFailed,
  llmRuntimeState,
}: {
  directorRunning: boolean;
  agentsRequired: boolean;
  agentsDraftReady: boolean;
  agentsDraftFailed: boolean;
  llmRuntimeState: DirectorPageProps['llmRuntimeState'];
}): string {
  if (directorRunning) {
    return '';
  }
  if (agentsRequired) {
    if (agentsDraftFailed) {
      return 'AGENTS 草稿生成失败，请返回主界面重新生成或人工处理后再启动 Director。';
    }
    return agentsDraftReady
      ? '需要先确认 AGENTS.md 后才能启动 Director。'
      : 'AGENTS.md 审核未完成，等待草稿生成或人工确认后才能启动 Director。';
  }
  return getRoleLlmBlockedReason(llmRuntimeState, 'director', 'Director');
}

/**
 * Director 工作区页面
 */
export function DirectorPage({
  workspace,
  tasks,
  workers,
  directorRunning,
  pmRunning = false,
  isStarting,
  isStopping,
  onToggleDirector,
  directorStartBlockedReason = '',
  onOpenSettings,
  currentTaskId,
  currentTaskTitle,
  currentTaskStatus,
  onBackToMain,
  fileEditEvents,
  executionLogs,
  llmStreamEvents,
  processStreamEvents,
  currentPhase,
  taskProgressMap,
  taskTraceMap,
  websocketLive,
  websocketReconnecting,
  websocketAttemptCount,
  internalBenchEnabled = false,
  llmRuntimeState,
  agentsRequired = false,
  agentsDraftReady = false,
  agentsDraftFailed = false,
  qualityGate,
  controlPlaneProjection,
  notifyError,
}: DirectorPageProps) {
  const localStartBlockedReason = resolveDirectorStartBlockedReason({
    directorRunning,
    agentsRequired,
    agentsDraftReady,
    agentsDraftFailed,
    llmRuntimeState,
  });
  const startBlockedReason = !directorRunning && directorStartBlockedReason.trim()
    ? directorStartBlockedReason.trim()
    : localStartBlockedReason;

  return (
    <ErrorBoundaryClass onError={(error) => notifyError(error.message || '发生未知错误')}>
      <BenchStatusStrip
        enabled={internalBenchEnabled}
        websocketLive={websocketLive}
        websocketReconnecting={websocketReconnecting}
        websocketAttemptCount={websocketAttemptCount}
      />
      <DirectorWorkspace
        workspace={workspace}
        onBackToMain={onBackToMain}
        tasks={tasks}
        workers={workers}
        directorRunning={directorRunning}
        isStarting={isStarting}
        isStopping={isStopping}
        startBlockedReason={startBlockedReason}
        onToggleDirector={() => onToggleDirector()}
        onOpenSettings={onOpenSettings}
        currentTaskId={currentTaskId ?? null}
        currentTaskTitle={currentTaskTitle ?? null}
        currentTaskStatus={currentTaskStatus ?? null}
        fileEditEvents={fileEditEvents as Parameters<typeof DirectorWorkspace>[0]['fileEditEvents']}
        executionLogs={executionLogs}
        llmStreamEvents={llmStreamEvents}
        processStreamEvents={processStreamEvents}
        currentPhase={currentPhase}
        taskProgressMap={taskProgressMap as Parameters<typeof DirectorWorkspace>[0]['taskProgressMap']}
        taskTraceMap={taskTraceMap}
      />
      <LlmRuntimeOverlay
        activeView="director"
        websocketLive={websocketLive}
        websocketReconnecting={websocketReconnecting}
        websocketAttemptCount={websocketAttemptCount}
        pmRunning={pmRunning}
        directorRunning={directorRunning}
        llmState={llmRuntimeState.state}
        llmBlockedRoles={llmRuntimeState.blockedRoles}
        llmRequiredRoles={llmRuntimeState.requiredRoles}
        llmLastUpdated={llmRuntimeState.lastUpdated}
        currentPhase={currentPhase ?? ''}
        qualityGate={qualityGate as Parameters<typeof LlmRuntimeOverlay>[0]['qualityGate']}
        executionLogs={executionLogs ?? []}
        llmStreamEvents={llmStreamEvents ?? []}
        processStreamEvents={processStreamEvents ?? []}
        fileEditEvents={fileEditEvents as Parameters<typeof LlmRuntimeOverlay>[0]['fileEditEvents']}
        controlPlaneProjection={controlPlaneProjection}
      />
      <Toaster position="bottom-right" />
    </ErrorBoundaryClass>
  );
}
