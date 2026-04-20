/**
 * Resident 工作区页面组件
 *
 * 展示 AGI Resident 界面
 */

import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { ResidentWorkspace } from '@/app/components/resident';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { Toaster } from '@/app/components/ui/sonner';
import type { LogEntry } from '@/types/log';

export interface ResidentSnapshot {
  [key: string]: unknown;
}

export interface ResidentPageProps {
  /** 工作区路径 */
  workspace: string;
  /** 返回主界面回调 */
  onBackToMain: () => void;
  /** Resident 快照数据 */
  residentSnapshot: ResidentSnapshot | null;
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
  /** 执行日志 */
  executionLogs?: LogEntry[];
  /** LLM 流事件 */
  llmStreamEvents?: LogEntry[];
  /** 进程流事件 */
  processStreamEvents?: LogEntry[];
  /** 错误通知回调 */
  notifyError: (message: string) => void;
}

/**
 * Resident 工作区页面
 */
export function ResidentPage({
  workspace,
  onBackToMain,
  residentSnapshot,
  websocketLive,
  websocketReconnecting,
  websocketAttemptCount,
  pmRunning,
  directorRunning,
  llmRuntimeState,
  currentPhase,
  qualityGate,
  executionLogs,
  llmStreamEvents,
  processStreamEvents,
  notifyError,
}: ResidentPageProps) {
  return (
    <ErrorBoundaryClass onError={(error) => notifyError(error.message || '发生未知错误')}>
      <ResidentWorkspace
        workspace={workspace}
        onBackToMain={onBackToMain}
        residentSnapshot={residentSnapshot}
      />
      <LlmRuntimeOverlay
        activeView="agi"
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
