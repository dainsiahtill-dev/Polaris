import { EngineeringModeBoundary } from '@/app/components/factory/EngineeringRunWorkspace';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
/**
 * Director 工作区页面组件
 *
 * 展示 Director 任务执行界面
 */

import type { DirectorWorkspace } from '@/app/components/director';
import type { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';
import type { RuntimeWorkerState } from '@/app/hooks/useRuntimeStore';

export interface DirectorPageProps {
  engineering?: EngineeringRunBinding;
  engineeringEnabled?: boolean;
  onEngineeringModeChange?: (enabled: boolean) => void;
  onOpenEngineeringContext?: () => void;
  /** 工作区路径 */
  workspace: string;
  /** 任务列表 */
  tasks?: PmTask[];
  /** Worker 列表 */
  workers?: RuntimeWorkerState[];
  /** Director 是否运行中 */
  directorRunning?: boolean;
  /** PM 是否运行中 */
  pmRunning?: boolean;
  /** 是否正在启动 */
  isStarting?: boolean;
  /** 是否正在停止 */
  isStopping?: boolean;
  /** Director 切换回调 */
  onToggleDirector?: () => void | boolean | Promise<void | boolean>;
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
  notifyError?: (message: string) => void;
}

/** All writable role actions use the shared engineering owner. */
export function DirectorPage(props: DirectorPageProps) {
  return <EngineeringModeBoundary binding={props.engineering} workspace={props.workspace} role="director" onBackToMain={props.onBackToMain} onOpenContext={props.onOpenEngineeringContext} />;
}
