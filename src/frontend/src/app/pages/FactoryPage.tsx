/**
 * Factory 工作区页面组件
 *
 * 展示工厂模式工作区，集成 PM 和 Director
 */

import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { FactoryWorkspace } from '@/app/components/factory/FactoryWorkspace';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { Toaster } from '@/app/components/ui/sonner';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';
import type { FactoryRunStatus, FactoryAuditEvent } from '@/hooks/useFactory';

export interface FactoryPageProps {
  /** 工作区路径 */
  workspace: string;
  /** 返回主界面回调 */
  onBackToMain: () => void;
  /** 任务列表 */
  tasks: PmTask[];
  /** PM 任务列表 */
  pmTasks?: PmTask[];
  /** Director 任务列表 */
  directorTasks?: PmTask[];
  /** 执行日志 */
  executionLogs?: LogEntry[];
  /** LLM 流事件 */
  llmStreamEvents?: LogEntry[];
  /** 进程流事件 */
  processStreamEvents?: LogEntry[];
  /** 文件编辑事件 */
  fileEditEvents?: unknown[];
  /** 当前运行 */
  currentRun?: FactoryRunStatus | null;
  /** 事件流 */
  events?: FactoryAuditEvent[];
  /** 启动回调 */
  onStart?: () => void;
  /** 取消回调 */
  onCancel?: () => void;
  /** 是否加载中 */
  isLoading: boolean;
  /** WebSocket 连接状态 */
  websocketLive: boolean;
  /** WebSocket 重连状态 */
  websocketReconnecting: boolean;
  /** WebSocket 重连次数 */
  websocketAttemptCount: number;
  /** PM 是否运行中 */
  pmRunning: boolean;
  /** Director 是否运行中 */
  directorRunning: boolean;
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
  /** 错误通知回调 */
  notifyError: (message: string) => void;
}

/**
 * Factory 工作区页面
 */
export function FactoryPage({
  workspace,
  onBackToMain,
  tasks,
  pmTasks,
  directorTasks,
  executionLogs,
  llmStreamEvents,
  processStreamEvents,
  fileEditEvents,
  currentRun,
  events,
  onStart,
  onCancel,
  isLoading,
  websocketLive,
  websocketReconnecting,
  websocketAttemptCount,
  pmRunning,
  directorRunning,
  llmRuntimeState,
  currentPhase,
  qualityGate,
  notifyError,
}: FactoryPageProps) {
  return (
    <ErrorBoundaryClass onError={(error) => notifyError(error.message || '发生未知错误')}>
      <FactoryWorkspace
        workspace={workspace}
        onBackToMain={onBackToMain}
        tasks={tasks}
        pmTasks={pmTasks}
        directorTasks={directorTasks}
        executionLogs={executionLogs}
        llmStreamEvents={llmStreamEvents}
        processStreamEvents={processStreamEvents}
        fileEditEvents={fileEditEvents as Parameters<typeof FactoryWorkspace>[0]['fileEditEvents']}
        currentRun={currentRun}
        events={events}
        onStart={onStart}
        onCancel={onCancel}
        isLoading={isLoading}
      />
      <LlmRuntimeOverlay
        activeView="factory"
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
      />
      <Toaster position="bottom-right" />
    </ErrorBoundaryClass>
  );
}
