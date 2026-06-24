/**
 * Factory 工作区页面组件
 *
 * 展示工厂模式工作区，集成 PM 和 Director
 */

import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { FactoryWorkspace } from '@/app/components/factory/FactoryWorkspace';
import { BenchPanel } from '@/app/components/factory/BenchPanel';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { Toaster } from '@/app/components/ui/sonner';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';
import type { FactoryRunStatus, FactoryAuditEvent, FactoryRunArtifact } from '@/hooks/useFactory';
import type { UseFactoryBenchResult } from '@/hooks/useFactoryBench';

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
  /** Factory run artifacts */
  artifacts?: FactoryRunArtifact[];
  /** Markdown summary */
  summaryMd?: string | null;
  /** JSON summary */
  summaryJson?: Record<string, unknown> | null;
  /** Artifact loading error */
  artifactsError?: string | null;
  /** Artifact loading state */
  isArtifactsLoading?: boolean;
  /** 启动回调 */
  onStart?: () => void;
  /** 取消回调 */
  onCancel?: () => void;
  /** 暂停回调 */
  onPause?: () => void;
  /** 恢复回调 */
  onResume?: () => void;
  /** 从 checkpoint 重试回调 */
  onRetryCheckpoint?: () => void;
  /** 是否加载中 */
  isLoading: boolean;
  /** Factory Bench 实时会话状态 */
  bench?: UseFactoryBenchResult;
  /** 内部测试模式下才允许展示 Factory Bench 面板/状态。 */
  internalBenchEnabled?: boolean;
  /** Factory Bench 观测到项目 workspace 后同步到全局 workspace。 */
  onBenchWorkspaceChange?: (workspace: string) => void;
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
    state: 'READY' | 'BLOCKED' | 'DEGRADED' | 'UNKNOWN';
    blockedRoles: string[];
    requiredRoles: string[];
    lastUpdated: string | null;
  };
  /** 当前阶段 */
  currentPhase?: string;
  /** 质量门 */
  qualityGate?: unknown;
  /** 平台 Run Ledger 投影 */
  controlPlaneProjection?: Parameters<typeof LlmRuntimeOverlay>[0]['controlPlaneProjection'];
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
  artifacts,
  summaryMd,
  summaryJson,
  artifactsError,
  isArtifactsLoading,
  onStart,
  onCancel,
  onPause,
  onResume,
  onRetryCheckpoint,
  isLoading,
  bench,
  internalBenchEnabled = false,
  onBenchWorkspaceChange,
  websocketLive,
  websocketReconnecting,
  websocketAttemptCount,
  pmRunning,
  directorRunning,
  llmRuntimeState,
  currentPhase,
  qualityGate,
  controlPlaneProjection,
  notifyError,
}: FactoryPageProps) {
  return (
    <ErrorBoundaryClass onError={(error) => notifyError(error.message || '发生未知错误')}>
      {internalBenchEnabled ? (
        <BenchStatusStrip
          enabled={internalBenchEnabled}
          bench={bench}
          websocketLive={websocketLive}
          websocketReconnecting={websocketReconnecting}
          websocketAttemptCount={websocketAttemptCount}
        />
      ) : null}
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
        artifacts={artifacts}
        summaryMd={summaryMd}
        summaryJson={summaryJson}
        artifactsError={artifactsError}
        isArtifactsLoading={isArtifactsLoading}
        onStart={onStart}
        onCancel={onCancel}
        onPause={onPause}
        onResume={onResume}
        onRetryCheckpoint={onRetryCheckpoint}
        isLoading={isLoading}
        bench={bench}
        internalBenchEnabled={internalBenchEnabled}
        controlPlaneProjection={controlPlaneProjection}
      />
      {internalBenchEnabled ? (
        <BenchPanel
          enabled={internalBenchEnabled}
          className="border-t border-white/10"
          onWorkspaceChange={onBenchWorkspaceChange}
        />
      ) : null}
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
        controlPlaneProjection={controlPlaneProjection}
      />
      <Toaster position="bottom-right" />
    </ErrorBoundaryClass>
  );
}
