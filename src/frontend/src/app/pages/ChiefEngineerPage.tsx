/**
 * Chief Engineer 工作区页面组件
 *
 * 展示 Chief Engineer 蓝图与执行前置条件控制界面。
 */

import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { ChiefEngineerWorkspace } from '@/app/components/chief-engineer';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { Toaster } from '@/app/components/ui/sonner';
import type { EngineStatus } from '@/app/types/appContracts';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntime';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';

export interface ChiefEngineerPageProps {
  /** 工作区路径 */
  workspace: string;
  /** 引擎状态快照 */
  engineStatus: EngineStatus | null;
  /** PM/Director 任务列表 */
  tasks: PmTask[];
  /** Worker 列表 */
  workers?: RuntimeWorkerState[];
  /** PM 状态 */
  pmState: Record<string, unknown> | null;
  /** PM 是否运行中 */
  pmRunning?: boolean;
  /** Director 是否运行中 */
  directorRunning: boolean;
  /** Director 是否正在启动 */
  isStartingDirector: boolean;
  /** 返回主界面回调 */
  onBackToMain: () => void;
  /** 进入 Director 工作区回调 */
  onEnterDirectorWorkspace: () => void;
  /** 打开系统配置 */
  onOpenSettings?: () => void;
  /** Director 切换回调 */
  onToggleDirector: () => void | boolean | Promise<void | boolean>;
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
  /** 当前阶段 */
  currentPhase?: string;
  /** 质量门 */
  qualityGate?: unknown;
  /** 执行日志 */
  executionLogs?: LogEntry[];
  /** LLM 流事件 */
  llmStreamEvents?: LogEntry[];
  /** 进程流事件 */
  processStreamEvents?: LogEntry[];
  /** 文件编辑事件 */
  fileEditEvents?: Parameters<typeof LlmRuntimeOverlay>[0]['fileEditEvents'];
  /** 错误通知回调 */
  notifyError: (message: string) => void;
}

/**
 * Chief Engineer 工作区页面
 */
export function ChiefEngineerPage({
  workspace,
  engineStatus,
  tasks,
  workers,
  pmState,
  pmRunning = false,
  directorRunning,
  isStartingDirector,
  onBackToMain,
  onEnterDirectorWorkspace,
  onOpenSettings,
  onToggleDirector,
  websocketLive,
  websocketReconnecting,
  websocketAttemptCount,
  llmRuntimeState,
  currentPhase,
  qualityGate,
  executionLogs,
  llmStreamEvents,
  processStreamEvents,
  fileEditEvents,
  notifyError,
}: ChiefEngineerPageProps) {
  return (
    <ErrorBoundaryClass onError={(error) => notifyError(error.message || '发生未知错误')}>
      <ChiefEngineerWorkspace
        workspace={workspace}
        engineStatus={engineStatus}
        tasks={tasks}
        workers={workers ?? []}
        pmState={pmState}
        directorRunning={directorRunning}
        isStartingDirector={isStartingDirector}
        onBackToMain={onBackToMain}
        onEnterDirectorWorkspace={onEnterDirectorWorkspace}
        onOpenSettings={onOpenSettings}
        onToggleDirector={onToggleDirector}
      />
      <LlmRuntimeOverlay
        activeView="chief_engineer"
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
        fileEditEvents={fileEditEvents}
      />
      <Toaster position="bottom-right" />
    </ErrorBoundaryClass>
  );
}
