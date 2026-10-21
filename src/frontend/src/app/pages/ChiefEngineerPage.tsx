import { EngineeringModeBoundary } from '@/app/components/factory/EngineeringRunWorkspace';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
/**
 * Chief Engineer 工作区页面组件
 *
 * 展示 Chief Engineer 蓝图与执行前置条件控制界面。
 */

import type { ChiefEngineerWorkspace } from '@/app/components/chief-engineer';
import type { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import type { EngineStatus } from '@/app/types/appContracts';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntimeStore';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';

export interface ChiefEngineerPageProps {
  engineering?: EngineeringRunBinding;
  engineeringEnabled?: boolean;
  onEngineeringModeChange?: (enabled: boolean) => void;
  onOpenEngineeringContext?: () => void;
  /** 工作区路径 */
  workspace: string;
  /** 引擎状态快照 */
  engineStatus?: EngineStatus | null;
  /** PM/Director 任务列表 */
  tasks?: PmTask[];
  /** Worker 列表 */
  workers?: RuntimeWorkerState[];
  /** PM 状态 */
  pmState?: Record<string, unknown> | null;
  /** PM 是否运行中 */
  pmRunning?: boolean;
  /** Director 是否运行中 */
  directorRunning?: boolean;
  /** Director 是否正在启动 */
  isStartingDirector?: boolean;
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
  onEnterDirectorWorkspace?: () => void;
  /** 打开系统配置 */
  onOpenSettings?: () => void;
  /** Director 切换回调 */
  onToggleDirector?: () => void | boolean | Promise<void | boolean>;
  /** 外部统一计算的 Director 启动阻断原因 */
  directorStartBlockedReason?: string;
  /** WebSocket 连接状态 */
  websocketLive?: boolean;
  /** WebSocket 重连状态 */
  websocketReconnecting?: boolean;
  /** WebSocket 重连次数 */
  websocketAttemptCount?: number;
  /** 内部测试模式下才允许展示 Factory Bench 状态。 */
  internalBenchEnabled?: boolean;
  /** LLM 运行时状态 */
  llmRuntimeState?: {
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
  notifyError?: (message: string) => void;
}

/** All writable role actions use the shared engineering owner. */
export function ChiefEngineerPage(props: ChiefEngineerPageProps) {
  return <EngineeringModeBoundary binding={props.engineering} workspace={props.workspace} role="chief_engineer" onBackToMain={props.onBackToMain} onOpenContext={props.onOpenEngineeringContext} />;
}
