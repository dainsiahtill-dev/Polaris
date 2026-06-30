/**
 * Chief Engineer 工作区页面组件
 *
 * 展示 Chief Engineer 蓝图与执行前置条件控制界面。
 */

import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { ChiefEngineerWorkspace } from '@/app/components/chief-engineer';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { BenchStatusStrip } from '@/app/components/factory/BenchStatusStrip';
import { Toaster } from '@/app/components/ui/sonner';
import { getRoleLlmBlockedReason } from '@/app/hooks/useLlmRuntimeGate';
import type { EngineStatus } from '@/app/types/appContracts';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntimeStore';
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
  /** Director 是否正在停止 */
  isStoppingDirector?: boolean;
  /** 是否需要 AGENTS.md 审核 */
  agentsRequired?: boolean;
  /** AGENTS.md 草稿是否就绪 */
  agentsDraftReady?: boolean;
  /** AGENTS.md 草稿是否生成失败 */
  agentsDraftFailed?: boolean;
  /** 返回主界面回调 */
  onBackToMain: () => void;
  /** 进入 Director 工作区回调 */
  onEnterDirectorWorkspace: () => void;
  /** 打开系统配置 */
  onOpenSettings?: () => void;
  /** Director 切换回调 */
  onToggleDirector: () => void | boolean | Promise<void | boolean>;
  /** 外部统一计算的 Director 启动阻断原因 */
  directorStartBlockedReason?: string;
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
  /** 平台 Run Ledger 投影 */
  controlPlaneProjection?: Parameters<typeof LlmRuntimeOverlay>[0]['controlPlaneProjection'];
  /** 错误通知回调 */
  notifyError: (message: string) => void;
}

function resolveChiefEngineerDirectorStartBlockedReason({
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
  llmRuntimeState: ChiefEngineerPageProps['llmRuntimeState'];
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
  isStoppingDirector = false,
  agentsRequired = false,
  agentsDraftReady = false,
  agentsDraftFailed = false,
  onBackToMain,
  onEnterDirectorWorkspace,
  onOpenSettings,
  onToggleDirector,
  directorStartBlockedReason: externalDirectorStartBlockedReason = '',
  websocketLive,
  websocketReconnecting,
  websocketAttemptCount,
  internalBenchEnabled = false,
  llmRuntimeState,
  currentPhase,
  qualityGate,
  executionLogs,
  llmStreamEvents,
  processStreamEvents,
  fileEditEvents,
  controlPlaneProjection,
  notifyError,
}: ChiefEngineerPageProps) {
  const localDirectorStartBlockedReason = resolveChiefEngineerDirectorStartBlockedReason({
    directorRunning,
    agentsRequired,
    agentsDraftReady,
    agentsDraftFailed,
    llmRuntimeState,
  });
  const directorStartBlockedReason = !directorRunning && externalDirectorStartBlockedReason.trim()
    ? externalDirectorStartBlockedReason.trim()
    : localDirectorStartBlockedReason;

  return (
    <ErrorBoundaryClass onError={(error) => notifyError(error.message || '发生未知错误')}>
      <BenchStatusStrip
        enabled={internalBenchEnabled}
        websocketLive={websocketLive}
        websocketReconnecting={websocketReconnecting}
        websocketAttemptCount={websocketAttemptCount}
      />
      <ChiefEngineerWorkspace
        workspace={workspace}
        engineStatus={engineStatus}
        tasks={tasks}
        workers={workers ?? []}
        pmState={pmState}
        directorRunning={directorRunning}
        isStartingDirector={isStartingDirector}
        isStoppingDirector={isStoppingDirector}
        directorStartBlockedReason={directorStartBlockedReason}
        onBackToMain={onBackToMain}
        onEnterDirectorWorkspace={onEnterDirectorWorkspace}
        onOpenSettings={onOpenSettings}
        onToggleDirector={onToggleDirector}
        executionLogs={executionLogs}
        llmStreamEvents={llmStreamEvents}
        processStreamEvents={processStreamEvents}
        currentPhase={currentPhase}
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
        controlPlaneProjection={controlPlaneProjection}
      />
      <Toaster position="bottom-right" />
    </ErrorBoundaryClass>
  );
}
