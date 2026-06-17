/**
 * PM 工作区页面组件
 *
 * 展示 PM 任务管理界面
 */

import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { PMWorkspace } from '@/app/components/pm';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { Toaster } from '@/app/components/ui/sonner';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';

export interface PMPageProps {
  /** 工作区路径 */
  workspace: string;
  /** PM 任务列表 */
  tasks: PmTask[];
  /** PM 状态 */
  pmState: Record<string, unknown> | null;
  /** PM 是否运行中 */
  pmRunning: boolean;
  /** Director 是否运行中 */
  directorRunning?: boolean;
  /** PM 终端状态 */
  pmTerminalStatus?: Parameters<typeof PMWorkspace>[0]['pmTerminalStatus'];
  /** PM 启动阻断原因 */
  pmStartBlockedReason?: string;
  /** PM 运行时问题 */
  runtimeIssue?: Parameters<typeof PMWorkspace>[0]['runtimeIssue'];
  /** 是否正在启动 */
  isStarting: boolean;
  /** 是否正在停止 */
  isStopping?: boolean;
  /** PM 切换回调 */
  onTogglePm: () => void | boolean | Promise<void | boolean>;
  /** 单次运行回调 */
  onRunPmOnce: () => void | boolean | Promise<void | boolean>;
  /** 返回主界面回调 */
  onBackToMain: () => void;
  /** 打开系统配置 */
  onOpenSettings?: () => void;
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
  /** 任务追踪事件 */
  taskTraceMap?: Parameters<typeof PMWorkspace>[0]['taskTraceMap'];
  /** 错误通知回调 */
  notifyError: (message: string) => void;
}

/**
 * PM 工作区页面
 */
export function PMPage({
  workspace,
  tasks,
  pmState,
  pmRunning,
  directorRunning = false,
  pmTerminalStatus = null,
  pmStartBlockedReason = '',
  runtimeIssue = null,
  isStarting,
  isStopping = false,
  onTogglePm,
  onRunPmOnce,
  onBackToMain,
  onOpenSettings,
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
  taskTraceMap,
  notifyError,
}: PMPageProps) {
  return (
    <ErrorBoundaryClass onError={(error) => notifyError(error.message || '发生未知错误')}>
      <BenchStatusStrip />
      <PMWorkspace
        tasks={tasks}
        pmState={pmState}
        pmRunning={pmRunning}
        pmTerminalStatus={pmTerminalStatus}
        pmStartBlockedReason={pmStartBlockedReason}
        runtimeIssue={runtimeIssue}
        isStarting={isStarting}
        isStopping={isStopping}
        onBackToMain={onBackToMain}
        onTogglePm={onTogglePm}
        onRunPmOnce={onRunPmOnce}
        onOpenSettings={onOpenSettings}
        workspace={workspace}
        executionLogs={executionLogs}
        llmStreamEvents={llmStreamEvents}
        processStreamEvents={processStreamEvents}
        currentPhase={currentPhase}
        qualityGate={qualityGate as Parameters<typeof PMWorkspace>[0]['qualityGate']}
        taskTraceMap={taskTraceMap}
        llmRuntimeState={llmRuntimeState}
      />
      <LlmRuntimeOverlay
        activeView="pm"
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
