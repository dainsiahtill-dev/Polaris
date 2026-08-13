/**
 * ContextOS data model types (extracted losslessly from contextOSData.ts).
 */

import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState, LlmRuntimeRoleBinding, LlmRuntimeRoleDetail } from '@/app/hooks/useLlmRuntimeGate';
import type { SnapshotPayload } from '@/app/types/appContracts';
import type {
  ContextOSEvent,
  ContextOSTelemetry,
  WorkerAggregate,
} from '../contextOSTelemetry';

export type PipelineState = 'active' | 'idle' | 'blocked';

export interface PipelineStage {
  id: string;
  label: string;
  /** 对应的真实后端组件名 */
  component: string;
  /** 该节点展示的一句话职责 */
  hint: string;
  state: PipelineState;
  /** 节点上展示的实时小指标（可空） */
  metric: string;
}

export interface ComponentHealth {
  id: string;
  name: string;
  component: string;
  state: PipelineState;
  metric: string;
  /** 0..1 强度条（真实占比；无意义时为 null 不渲染） */
  intensity: number | null;
}

export interface BudgetSlice {
  key: string;
  label: string;
  tokens: number;
  ratio: number;
  /** tailwind 颜色类（背景） */
  colorClass: string;
}

/** 真实事件类型分布的一段（来自观测事件的 category 归类）。 */
export interface EventTypeSlice {
  key: string;
  label: string;
  count: number;
  ratio: number;
  colorClass: string;
}

export interface RoleBindingBudget {
  id: string;
  roleId: string;
  label: string;
  providerId: string | null;
  providerName: string | null;
  providerType: string | null;
  model: string | null;
  profile: string | null;
  contextWindowTokens: number | null;
  maxOutputTokens: number | null;
  contextWindowLabel: string;
  contextWindowDetail: string;
  contextWindowSource: 'binding' | 'unknown';
  calls: number;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  toolTokens: number;
  reasoningTokens: number;
  audioTokens: number;
  serverToolUseCount: number;
  realProviderTokens: number;
  estimatedTokens: number;
  contextTokensLatest: number | null;
  windowOccupancyTokens: number | null;
  windowOccupancyLabel: string;
  windowOccupancyDetail: string;
  latencyMs: number | null;
  lastEventAt: number | null;
  matchedEvents: number;
  usageSource: 'matched' | 'role_aggregate' | 'none';
  usageKind: 'provider' | 'stream_final' | 'request_estimate' | 'char_estimate' | 'mixed' | 'none';
  usageProvenance: 'provider' | 'estimated' | 'mixed' | 'none';
  bindingId: string | null;
  taskId: string | null;
  pmTaskId: string | null;
  chiefBlueprintId: string | null;
  skipped: boolean;
  skipReason: string | null;
}

export interface RoleInternalContext {
  roleId: string;
  title: string;
  courtTitle: string;
  state: PipelineState;
  events: ContextOSEvent[];
  eventCount: number;
  projectionCount: number;
  receiptCount: number;
  contextItemsCount: number | null;
  workingMemoryItems: number | null;
  workingMemoryEstimated: boolean;
  contextTokensLatest: number | null;
  contextWindowTokens: number | null;
  contextWindowLabel: string;
  contextWindowDetail: string;
  /** 上下文窗口数据来源：binding=LLM角色绑定，unknown=无绑定。 */
  contextWindowSource: 'binding' | 'unknown';
  /** 绑定的 provider 名称（如 "Kimi Coding"），无绑定时 null。 */
  contextWindowProvider: string | null;
  /** 绑定的 model 名称（如 "kimi-for-coding"），无绑定时 null。 */
  contextWindowModel: string | null;
  /** 当前窗口占用的角色级分子。优先最终 provider request token，其次 context token，再退回平均 prompt。 */
  windowOccupancyTokens: number | null;
  windowOccupancyLabel: string;
  windowOccupancyDetail: string;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  toolTokens: number;
  reasoningTokens: number;
  audioTokens: number;
  serverToolUseCount: number;
  calls: number;
  lastEventAt: number | null;
  currentTaskId: string | null;
  currentTaskTitle: string | null;
  /** Reference to the most recent stored full context (click to fetch detail). */
  latestContextSnapshotRef: string | null;
  /** Most recent call ID for this role. */
  latestCallId: string | null;
  /** Most recent turn ID for this role. */
  latestTurnId: string | null;
  /** Per provider/model context budget rows for this role. */
  bindingBudgets: RoleBindingBudget[];
  detail: string;
}

export interface RoleCard {
  id: string;
  title: string;
  /** 古风官职名 */
  courtTitle: string;
  tokens: number;
  state: PipelineState;
  detail: string;
  /**
   * 该角色的 token 是否来自真实带 usage 的观测通道。
   * 只有该角色自己的实时事件携带 usage 时才归并 token；无 usage 通道时
   * detail 以事件数/无观测呈现，不冒充 token 归因。
   */
  tokensReal: boolean;
  /** 是否为只读辅助角色 */
  readOnly?: boolean;
  lastEventAt: number | null;
  projectionCount: number;
  contextItemsCount: number | null;
  contextWindowTokens: number | null;
  contextWindowLabel: string;
  contextWindowDetail: string;
  /** 上下文窗口数据来源：binding=LLM角色绑定，unknown=无绑定。 */
  contextWindowSource: 'binding' | 'unknown';
  /** 绑定的 provider 名称，无绑定时 null。 */
  contextWindowProvider: string | null;
  /** 绑定的 model 名称，无绑定时 null。 */
  contextWindowModel: string | null;
  receiptCount: number;
  internalContext: RoleInternalContext;
}

export interface DecisionRow {
  id: string;
  time: string;
  actor: string;
  kind: string;
  summary: string;
  tone: 'info' | 'success' | 'warning' | 'error';
  /** 数据来源：真实观测流 / 对话 / 日志。 */
  source: 'telemetry' | 'dialogue' | 'log';
  /** 该事件的真实 token 用量（仅 telemetry 来源且有 usage 时）。 */
  tokens?: number;
  /** 真实时延（ms，仅 telemetry 来源且有 duration 时）。 */
  latencyMs?: number | null;
  /** 是否落盘了上下文快照（output.context_snapshot）。 */
  receipt?: boolean;
}

/**
 * Phase 3+：单 worker 的实时 LLM 追踪卡。
 * 字段完全派生自真实遥测（journal `llm` 通道的 meta.worker_id / meta.workerId）。
 * 无 worker 归属时聚合为空（hasWorkers=false，UI 据实降级）。
 */
export interface WorkerCard {
  workerId: string;
  role: string;
  tokens: number;
  calls: number;
  events: number;
  /** 真实时延（ms），无则 null。 */
  latencyMs: number | null;
  /** 最近活动 epoch（ms），无则 null。 */
  lastEpoch: number | null;
  /** Pipeline 状态（有事件 → active；纯空 → idle）。 */
  state: PipelineState;
  /** 该 worker 命中的最近一次 context snapshot ref（按事件截取）。 */
  latestContextSnapshotRef: string | null;
}

export interface ContextOSModel {
  running: boolean;
  /** 无运行、无 token、无事件 → 数据空闲（用于「空闲」水印，避免陈旧数据被误读为实时） */
  dataIdle: boolean;
  /** error 级日志 + 失败对话事件计数 */
  errorCount: number;
  /** 最近权威观测仍为 error，且尚无更新事件证明恢复。 */
  currentErrorUnrecovered: boolean;
  /** 从最近日志解析出的单次调用时延（毫秒），无则 null */
  lastLatencyMs: number | null;
  /** PM 迭代轮次（来自 snapshot.pm_state.pm_iteration / run_id），无则 null */
  iteration: number | null;
  /** 任务看板任务数（来自 snapshot.tasks） */
  taskCount: number;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
  toolTokens: number;
  reasoningTokens: number;
  audioTokens: number;
  serverToolUseCount: number;
  providerUsageTokens: number;
  usageSourceLabel: string;
  /** token 是否来自实时遥测（journal `llm` 通道）。false = 退回用量统计通道（非实时）或无数据。 */
  tokensRealtime: boolean;
  calls: number;
  avgPerCall: number;
  /** 估算的单次上下文窗口占用比例 0..1 */
  windowOccupancy: number;
  windowOccupancyTokens: number;
  /** 用于全部视角的窗口分母：优先取已绑定角色窗口的最小值，无数据为 null（不冒充 128k）。 */
  contextWindowTokens: number | null;
  contextWindowLabel: string;
  contextWindowDetail: string;
  contextWindowSource: 'binding' | 'unknown';
  /** 是否绑定了真实实时遥测（WebSocket 运行时流有事件）。 */
  telemetryActive: boolean;
  /** 真实 ProjectionEngine 投影次数（telemetry 优先，无则回退 calls 估算）。 */
  projectionCount: number;
  /** 真实落盘的上下文快照数（output.context_snapshot 计数）。 */
  receiptCount: number;
  /** 真实平均/最近时延（ms），无则 null。 */
  realLatencyMs: number | null;
  /** 最近真实事件 epoch（毫秒），无则 null。 */
  lastTelemetryEpoch: number | null;
  /** 由后端字符估算得到 usage 的调用数（output.usage.estimated），用于在 token 总量旁标「含估算」。 */
  estimatedCalls: number;
  /** 最近一次 context.build 装配的真实上下文项数（WorkingMem 在窗项数），无则 null。 */
  contextItemsCount: number | null;
  /** 遥测是否只覆盖尾部窗口（解析行数达读取上限）；用于把「累计」诚实降级为「最近窗口」。 */
  telemetryWindowed: boolean;
  pipeline: PipelineStage[];
  components: ComponentHealth[];
  budget: BudgetSlice[];
  byModeSlices: BudgetSlice[];
  /** 真实事件类型分布（projection/call/tool/state/error），仅遥测激活时非空。 */
  eventTypes: EventTypeSlice[];
  /** 事件类型分布的统计基数（= 最近事件窗口大小）。 */
  eventTypesTotal: number;
  roles: RoleCard[];
  decisions: DecisionRow[];
  policies: string[];
  /** Phase 3+：多 worker LLM 追踪卡（按 worker_id 聚合）。无 worker 归属时为空数组。 */
  workers: WorkerCard[];
  /** 是否识别到任何带 worker_id 的真实事件（用于 UI 判断是否展示多 worker 面板）。 */
  hasWorkers: boolean;
  /** Per-role provider/model context budget rows. */
  bindingBudgets: RoleBindingBudget[];
}

/** 事件类型 → 展示标签与颜色（与解析层 category 一致）。 */
