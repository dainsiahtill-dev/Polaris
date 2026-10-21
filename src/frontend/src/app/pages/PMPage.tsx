import { EngineeringModeBoundary } from '@/app/components/factory/EngineeringRunWorkspace';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
/**
 * PM 工作区页面组件
 *
 * 展示 PM 任务管理界面
 */

import type { PMWorkspace } from '@/app/components/pm';
import type { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import type { PmTask } from '@/types/task';
import type { LogEntry } from '@/types/log';

export interface PMPageProps {
  engineering?: EngineeringRunBinding;
  engineeringEnabled?: boolean;
  onEngineeringModeChange?: (enabled: boolean) => void;
  onOpenEngineeringContext?: () => void;
  /** 工作区路径 */
  workspace: string;
  /** PM 任务列表 */
  tasks?: PmTask[];
  /** PM 状态 */
  pmState?: Record<string, unknown> | null;
  /** PM 是否运行中 */
  pmRunning?: boolean;
  /** Director 是否运行中 */
  directorRunning?: boolean;
  /** PM 终端状态 */
  pmTerminalStatus?: Parameters<typeof PMWorkspace>[0]['pmTerminalStatus'];
  /** PM 启动阻断原因 */
  pmStartBlockedReason?: string;
  /** PM 运行时问题 */
  runtimeIssue?: Parameters<typeof PMWorkspace>[0]['runtimeIssue'];
  /** 是否正在启动 */
  isStarting?: boolean;
  /** 是否正在停止 */
  isStopping?: boolean;
  /** PM 切换回调 */
  onTogglePm?: () => void | boolean | Promise<void | boolean>;
  /** 单次运行回调 */
  onRunPmOnce?: () => void | boolean | Promise<void | boolean>;
  /** 返回主界面回调 */
  onBackToMain: () => void;
  /** 打开系统配置 */
  onOpenSettings?: () => void;
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
  /** 任务追踪事件 */
  taskTraceMap?: Parameters<typeof PMWorkspace>[0]['taskTraceMap'];
  /** 错误通知回调 */
  notifyError?: (message: string) => void;
}

/** All writable role actions use the shared engineering owner. */
export function PMPage(props: PMPageProps) {
  return <EngineeringModeBoundary binding={props.engineering} workspace={props.workspace} role="pm" onBackToMain={props.onBackToMain} onOpenContext={props.onOpenEngineeringContext} />;
}
