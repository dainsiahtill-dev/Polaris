/**
 * Director 工作区页面组件
 *
 * 展示 Director 任务执行界面
 */

import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { DirectorWorkspace } from '@/app/components/director';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { Toaster } from '@/app/components/ui/sonner';
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
  /** 是否正在启动 */
  isStarting: boolean;
  /** 是否正在停止 */
  isStopping: boolean;
  /** Director 切换回调 */
  onToggleDirector: () => void;
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
  /** WebSocket 连接状态 */
  websocketLive: boolean;
  /** WebSocket 重连状态 */
  websocketReconnecting: boolean;
  /** WebSocket 重连次数 */
  websocketAttemptCount: number;
  /** LLM 运行时状态 */
  llmRuntimeState: {
    state: 'READY' | 'BLOCKED' | 'UNKNOWN';
    blockedRoles: string[];
    requiredRoles: string[];
    lastUpdated: string | null;
  };
  /** 是否需要代理 */
  agentsRequired?: boolean;
  /** 草稿是否就绪 */
  agentsDraftReady?: boolean;
  /** 质量门 */
  qualityGate?: unknown;
  /** 错误通知回调 */
  notifyError: (message: string) => void;
}

/**
 * Director 工作区页面
 */
export function DirectorPage({
  workspace,
  tasks,
  workers,
  directorRunning,
  isStarting,
  isStopping: _isStopping,
  onToggleDirector,
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
  websocketLive,
  websocketReconnecting,
  websocketAttemptCount,
  llmRuntimeState,
  agentsRequired = false,
  agentsDraftReady = false,
  qualityGate,
  notifyError,
}: DirectorPageProps) {
  return (
    <ErrorBoundaryClass onError={(error) => notifyError(error.message || '发生未知错误')}>
      <DirectorWorkspace
        workspace={workspace}
        onBackToMain={onBackToMain}
        tasks={tasks}
        workers={workers}
        directorRunning={directorRunning}
        isStarting={isStarting}
        onToggleDirector={() => onToggleDirector()}
        currentTaskId={currentTaskId ?? null}
        currentTaskTitle={currentTaskTitle ?? null}
        currentTaskStatus={currentTaskStatus ?? null}
        fileEditEvents={fileEditEvents as Parameters<typeof DirectorWorkspace>[0]['fileEditEvents']}
        executionLogs={executionLogs}
        llmStreamEvents={llmStreamEvents}
        processStreamEvents={processStreamEvents}
        currentPhase={currentPhase}
        taskProgressMap={taskProgressMap as Parameters<typeof DirectorWorkspace>[0]['taskProgressMap']}
      />
      <LlmRuntimeOverlay
        activeView="director"
        websocketLive={websocketLive}
        websocketReconnecting={websocketReconnecting}
        websocketAttemptCount={websocketAttemptCount}
        pmRunning={false}
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
      />
      <Toaster position="bottom-right" />
    </ErrorBoundaryClass>
  );
}
