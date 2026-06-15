/**
 * ContextOS 实时视图 - 数据派生层
 *
 * 该模块把 App 已有的真实运行时数据（usageStats / dialogueEvents / executionLogs /
 * snapshot / llmRuntimeState / phase）派生成 ContextOS 仪表盘所需的展示模型。
 *
 * 设计原则（诚实优先）：
 *  - 能从真实 props 计算的指标 → 直接计算（real）。
 *  - 图中暗示但无真实数据源的指标 → 标注「估算」(estimated)，绝不伪造精度。
 */

import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState } from '@/app/hooks/useLlmRuntimeGate';
import type { SnapshotPayload } from '@/app/types/appContracts';

/** 名义上下文窗口大小（用于「上下文窗口占用」估算条；真实窗口随模型而变）。 */
export const NOMINAL_CONTEXT_WINDOW = 128_000;

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

export interface RoleCard {
  id: string;
  title: string;
  /** 古风官职名 */
  courtTitle: string;
  tokens: number;
  state: PipelineState;
  detail: string;
}

export interface DecisionRow {
  id: string;
  time: string;
  actor: string;
  kind: string;
  summary: string;
  tone: 'info' | 'success' | 'warning' | 'error';
}

export interface ContextOSModel {
  running: boolean;
  /** 无运行、无 token、无事件 → 数据空闲（用于「空闲」水印，避免陈旧数据被误读为实时） */
  dataIdle: boolean;
  /** error 级日志 + 失败对话事件计数 */
  errorCount: number;
  /** 从最近日志解析出的单次调用时延（毫秒），无则 null */
  lastLatencyMs: number | null;
  /** PM 迭代轮次（来自 snapshot.pm_state.pm_iteration / run_id），无则 null */
  iteration: number | null;
  /** 任务看板任务数（来自 snapshot.tasks） */
  taskCount: number;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  calls: number;
  avgPerCall: number;
  /** 估算的单次上下文窗口占用比例 0..1 */
  windowOccupancy: number;
  windowOccupancyTokens: number;
  pipeline: PipelineStage[];
  components: ComponentHealth[];
  budget: BudgetSlice[];
  byModeSlices: BudgetSlice[];
  roles: RoleCard[];
  decisions: DecisionRow[];
  policies: string[];
}

const ROLE_KEY_ALIASES: Record<string, string[]> = {
  pm: ['pm', 'project_manager', 'planning', 'plan'],
  architect: ['architect', 'design', 'architecture'],
  director: ['director', 'execution', 'implementation', 'code', 'worker'],
  qa: ['qa', 'review', 'reviewer', 'quality', 'test'],
};

/** 角色 → Decision Log 中 actor/speaker 的匹配别名（用于角色页签交叉过滤）。 */
export const ROLE_DECISION_ALIASES: Record<string, string[]> = {
  pm: ['pm'],
  architect: ['architect'],
  director: ['director'],
  qa: ['qa', 'reviewer'],
};

/** 判断某条决策记录是否属于给定角色（roleId 为 null 时表示「全部」）。 */
export function decisionMatchesRole(actor: string, roleId: string | null): boolean {
  if (!roleId) return true;
  const aliases = ROLE_DECISION_ALIASES[roleId];
  if (!aliases) return true;
  const lowered = actor.toLowerCase();
  return aliases.some((alias) => lowered.includes(alias));
}

function safeNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function formatTokens(value: number): string {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  }
  return String(Math.round(value));
}

function formatClock(raw: string | undefined): string {
  if (!raw) return '--:--:--';
  const epoch = Date.parse(raw);
  if (!Number.isFinite(epoch)) {
    // 已是 HH:MM:SS 形式则原样返回
    return raw.length > 12 ? raw.slice(11, 19) : raw;
  }
  return new Date(epoch).toLocaleTimeString('zh-CN', { hour12: false });
}

/** 从最近的执行日志中解析单次调用时延（形如 "1234 ms" / "1234ms"）。 */
function parseLatencyMs(logs: LogEntry[]): number | null {
  const start = Math.max(0, logs.length - 24);
  for (let i = logs.length - 1; i >= start; i--) {
    const text = `${logs[i]?.message ?? ''} ${logs[i]?.details ?? ''}`;
    const match = text.match(/(\d{2,7})\s*ms\b/i);
    if (match) {
      const value = Number.parseInt(match[1], 10);
      if (Number.isFinite(value)) return value;
    }
  }
  return null;
}

function phaseToActiveStage(phase: string, running: boolean): string | null {
  const token = (phase || '').trim().toLowerCase();
  if (!running) return null;
  if (token.includes('plan')) return 'projection';
  if (token.includes('dispatch')) return 'budget';
  if (token.includes('implement') || token.includes('exec') || token.includes('code')) return 'llm';
  if (token.includes('review') || token.includes('qa') || token.includes('test')) return 'telemetry';
  return 'working_mem';
}

function sumModeTokens(byMode: Record<string, { total_tokens: number; calls: number }>, aliases: string[]): number {
  let total = 0;
  for (const [key, value] of Object.entries(byMode)) {
    const lowered = key.toLowerCase();
    if (aliases.some((alias) => lowered.includes(alias))) {
      total += safeNumber(value?.total_tokens);
    }
  }
  return total;
}

/** 把时间戳解析成可比较的 epoch（毫秒）；不可解析 → 0（排到末尾，保留插入序）。 */
function parseEpoch(raw: string | undefined): number {
  if (!raw) return 0;
  const epoch = Date.parse(raw);
  return Number.isFinite(epoch) ? epoch : 0;
}

function deriveDecisions(dialogueEvents: DialogueEvent[], executionLogs: LogEntry[]): DecisionRow[] {
  const rows: Array<{ row: DecisionRow; epoch: number }> = [];

  dialogueEvents.slice(-14).forEach((event, index) => {
    const speaker = String(event.speaker || 'System');
    const kind = String(event.type || event.refs?.phase || 'message');
    const tone: DecisionRow['tone'] = /error|fail/i.test(kind)
      ? 'error'
      : /warn|block/i.test(kind)
        ? 'warning'
        : /done|complete|pass|success/i.test(kind)
          ? 'success'
          : 'info';
    rows.push({
      epoch: parseEpoch(event.timestamp),
      row: {
        id: event.eventId || `dlg-${event.seq ?? index}`,
        time: formatClock(event.timestamp),
        actor: speaker,
        kind,
        summary: String(event.content || '').replace(/\s+/g, ' ').trim().slice(0, 120),
        tone,
      },
    });
  });

  executionLogs.slice(-6).forEach((log, index) => {
    const tone: DecisionRow['tone'] = log.level === 'error'
      ? 'error'
      : log.level === 'warning'
        ? 'warning'
        : log.level === 'success'
          ? 'success'
          : 'info';
    rows.push({
      epoch: parseEpoch(log.timestamp),
      row: {
        id: log.id || `log-${index}`,
        time: formatClock(log.timestamp),
        actor: String(log.source || 'runtime'),
        kind: String(log.level || 'info'),
        summary: String(log.message || '').replace(/\s+/g, ' ').trim().slice(0, 120),
        tone,
      },
    });
  });

  // 真·按时间倒序合并（Array.sort 稳定：等时/不可解析项保留插入序），最多 10 条。
  return rows
    .sort((a, b) => b.epoch - a.epoch)
    .slice(0, 10)
    .map((entry) => entry.row);
}

export function buildContextOSModel(input: {
  usageStats: UsageStats | null;
  dialogueEvents: DialogueEvent[];
  executionLogs: LogEntry[];
  snapshot: SnapshotPayload | null;
  llmRuntimeState: LlmRuntimeGateState;
  currentPhase: string;
  pmRunning: boolean;
  directorRunning: boolean;
}): ContextOSModel {
  const {
    usageStats,
    dialogueEvents,
    executionLogs,
    snapshot,
    llmRuntimeState,
    currentPhase,
    pmRunning,
    directorRunning,
  } = input;

  const running = pmRunning || directorRunning;

  // 真实快照派生：任务看板规模 + PM 迭代轮次。
  const snapshotTasks = Array.isArray(snapshot?.tasks)
    ? snapshot.tasks.filter((task) => Boolean(task && typeof task === 'object'))
    : [];
  const taskCount = snapshotTasks.length;
  const pmState = snapshot?.pm_state && typeof snapshot.pm_state === 'object'
    ? (snapshot.pm_state as Record<string, unknown>)
    : null;
  const rawIteration = pmState?.['pm_iteration'];
  const iteration = typeof rawIteration === 'number' && Number.isFinite(rawIteration)
    ? rawIteration
    : typeof rawIteration === 'string' && Number.isFinite(Number(rawIteration))
      ? Number(rawIteration)
      : null;
  const totals = usageStats?.totals ?? { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
  const totalTokens = safeNumber(totals.total_tokens);
  const promptTokens = safeNumber(totals.prompt_tokens);
  const completionTokens = safeNumber(totals.completion_tokens);
  const calls = safeNumber(usageStats?.calls);
  const byMode = usageStats?.by_mode ?? {};
  const avgPerCall = calls > 0 ? Math.round(totalTokens / calls) : 0;

  // 估算单次上下文窗口占用：以「平均单次提示 token」近似当前窗口压力。
  const avgPromptPerCall = calls > 0 ? promptTokens / calls : promptTokens;
  const windowOccupancyTokens = Math.round(avgPromptPerCall);
  const windowOccupancy = Math.max(0, Math.min(1, windowOccupancyTokens / NOMINAL_CONTEXT_WINDOW));

  const blockedRoles = new Set(llmRuntimeState.blockedRoles.map((role) => role.toLowerCase()));
  const llmBlocked = llmRuntimeState.state === 'BLOCKED';

  const activeStageId = phaseToActiveStage(currentPhase, running);
  const stateFor = (id: string): PipelineState => {
    if (llmBlocked && (id === 'llm' || id === 'prompt')) return 'blocked';
    if (id === 'role_signal' && blockedRoles.size > 0) return 'blocked';
    if (activeStageId === id) return 'active';
    return 'idle';
  };

  const eventCount = dialogueEvents.length;
  const logCount = executionLogs.length;
  const windowItems = Math.min(eventCount, 32);

  const errorCount =
    executionLogs.filter((log) => log.level === 'error').length +
    dialogueEvents.filter((event) => /error|fail/i.test(String(event.type || ''))).length;
  const lastLatencyMs = parseLatencyMs(executionLogs);
  const dataIdle = !running && totalTokens === 0 && eventCount === 0 && logCount === 0;

  const pipeline: PipelineStage[] = [
    { id: 'request', label: '用户请求', component: 'UserTurn', hint: '进入的指令 / 反馈', state: running ? 'active' : 'idle', metric: taskCount > 0 ? `${eventCount} 轮 · ${taskCount} 任务` : `${eventCount} 轮` },
    { id: 'turflog', label: 'TurfLog', component: 'TurnLog', hint: '事件真值流', state: stateFor('turflog'), metric: `${eventCount + logCount} 事件` },
    { id: 'working_mem', label: 'WorkingMem', component: 'WorkingMemoryWindow', hint: '活动上下文窗口', state: stateFor('working_mem'), metric: `${windowItems} 项` },
    { id: 'projection', label: 'ProjectionEngine', component: 'ProjectionEngine', hint: '自适应排序投影', state: stateFor('projection'), metric: `${calls} 次` },
    { id: 'role_signal', label: 'RoleSignalPlane', component: 'RoleSignalPlane', hint: '角色信号注入', state: stateFor('role_signal'), metric: `${4 - blockedRoles.size}/4 角色` },
    { id: 'budget', label: 'BudgetPlanner', component: 'PhaseAwareBudgetPlanner', hint: '相位感知预算', state: stateFor('budget'), metric: `${formatTokens(avgPerCall)} tok/次` },
    { id: 'prompt', label: 'PromptAssembler', component: 'project() → messages', hint: '提示装配与门禁', state: stateFor('prompt'), metric: `${formatTokens(promptTokens)} 提示` },
    { id: 'llm', label: 'LLM Invoke', component: 'AIExecutor', hint: '模型调用', state: stateFor('llm'), metric: lastLatencyMs !== null ? `${formatTokens(completionTokens)} · ${lastLatencyMs}ms` : `${formatTokens(completionTokens)} 输出` },
  ];

  const componentIntensityBase = Math.max(promptTokens + completionTokens, 1);
  const components: ComponentHealth[] = [
    {
      id: 'turflog', name: 'TurfLog', component: '真值事件流',
      state: running ? 'active' : 'idle',
      metric: `${eventCount} 轮 · ${logCount} 日志`,
      intensity: eventCount > 0 ? Math.min(1, eventCount / 48) : null,
    },
    {
      id: 'working_mem', name: 'WorkingMem', component: '活动窗口',
      state: running ? 'active' : 'idle',
      metric: `${windowItems} 项在窗`,
      intensity: windowItems > 0 ? Math.min(1, windowItems / 32) : null,
    },
    {
      id: 'projection', name: 'ProjectionEngine', component: '排序投影',
      state: running ? 'active' : 'idle',
      metric: `投影 ${calls} 次`,
      intensity: null,
    },
    {
      id: 'role_signal', name: 'RoleSignalPlane', component: '角色信号',
      state: blockedRoles.size > 0 ? 'blocked' : running ? 'active' : 'idle',
      metric: blockedRoles.size > 0 ? `${blockedRoles.size} 角色受阻` : `${4 - blockedRoles.size} 角色就绪`,
      intensity: null,
    },
    {
      id: 'budget', name: 'BudgetPlanner', component: '相位预算',
      state: running ? 'active' : 'idle',
      metric: `${formatTokens(avgPerCall)} tok / 次`,
      intensity: avgPerCall > 0 ? Math.min(1, avgPerCall / NOMINAL_CONTEXT_WINDOW * 4) : null,
    },
    {
      id: 'prompt', name: 'PromptAssembler', component: '提示装配',
      state: llmBlocked ? 'blocked' : running ? 'active' : 'idle',
      metric: `${formatTokens(promptTokens)} 提示 tok`,
      intensity: promptTokens > 0 ? Math.min(1, promptTokens / componentIntensityBase) : null,
    },
    {
      id: 'telemetry', name: 'Receipt · Telemetry', component: '回执遥测',
      state: errorCount > 0 ? 'blocked' : llmBlocked ? 'blocked' : llmRuntimeState.state === 'READY' ? 'active' : 'idle',
      metric: errorCount > 0 ? `${errorCount} 错误 · ${calls} 调用` : `${formatTokens(completionTokens)} 输出 tok`,
      intensity: completionTokens > 0 ? Math.min(1, completionTokens / componentIntensityBase) : null,
    },
  ];

  // 预算构成：真实的 提示/输出 二分（不伪造 System/Memory/Tool 细分）。
  const budget: BudgetSlice[] = [
    { key: 'prompt', label: '提示 (Prompt)', tokens: promptTokens, ratio: totalTokens > 0 ? promptTokens / totalTokens : 0, colorClass: 'bg-accent-secondary' },
    { key: 'completion', label: '输出 (Completion)', tokens: completionTokens, ratio: totalTokens > 0 ? completionTokens / totalTokens : 0, colorClass: 'bg-gold' },
  ];

  // 按模式分布（真实 by_mode），取 token 最高的前 6 个。
  const modePalette = ['bg-accent-secondary', 'bg-gold', 'bg-accent', 'bg-status-info', 'bg-status-success', 'bg-status-warning'];
  const byModeSlices: BudgetSlice[] = Object.entries(byMode)
    .map(([key, value]) => ({ key, tokens: safeNumber(value?.total_tokens) }))
    .filter((entry) => entry.tokens > 0)
    .sort((a, b) => b.tokens - a.tokens)
    .slice(0, 6)
    .map((entry, index) => ({
      key: entry.key,
      label: entry.key,
      tokens: entry.tokens,
      ratio: totalTokens > 0 ? entry.tokens / totalTokens : 0,
      colorClass: modePalette[index % modePalette.length],
    }));

  const speakerTokens = (speakers: string[]): number => {
    const lowered = speakers.map((s) => s.toLowerCase());
    return dialogueEvents.filter((event) => lowered.includes(String(event.speaker || '').toLowerCase())).length;
  };

  const roleState = (roleKey: string): PipelineState => {
    if (blockedRoles.has(roleKey)) return 'blocked';
    if (roleKey === 'pm' && pmRunning) return 'active';
    if (roleKey === 'director' && directorRunning) return 'active';
    return 'idle';
  };

  const roleTokens = (roleKey: string): number => {
    const fromMode = sumModeTokens(byMode, ROLE_KEY_ALIASES[roleKey] ?? [roleKey]);
    if (fromMode > 0) return fromMode;
    // 退化：用该角色发言数 * 估算（标注于 detail）
    return 0;
  };

  const roles: RoleCard[] = [
    { id: 'pm', key: 'pm', courtTitle: '尚书令', title: 'PM' },
    { id: 'architect', key: 'architect', courtTitle: '中书令', title: 'Architect' },
    { id: 'director', key: 'director', courtTitle: '工部侍郎', title: 'Director' },
    { id: 'qa', key: 'qa', courtTitle: '门下侍中', title: 'QA' },
  ].map((role) => {
    const tokens = roleTokens(role.key);
    const speeches = speakerTokens(role.key === 'qa' ? ['QA', 'Reviewer'] : [role.title]);
    return {
      id: role.id,
      title: role.title,
      courtTitle: role.courtTitle,
      tokens,
      state: roleState(role.key),
      detail: tokens > 0 ? `${formatTokens(tokens)} tok` : speeches > 0 ? `${speeches} 次发言` : '待命',
    };
  });

  const policies: string[] = [
    '自适应排序',
    '回执卸载',
    '相位感知预算',
    running ? '运行中' : '空闲',
    llmRuntimeState.state === 'READY' ? 'LLM 就绪' : llmBlocked ? 'LLM 受阻' : 'LLM 未知',
  ];

  return {
    running,
    dataIdle,
    errorCount,
    lastLatencyMs,
    iteration,
    taskCount,
    totalTokens,
    promptTokens,
    completionTokens,
    calls,
    avgPerCall,
    windowOccupancy,
    windowOccupancyTokens,
    pipeline,
    components,
    budget,
    byModeSlices,
    roles,
    decisions: deriveDecisions(dialogueEvents, executionLogs),
    policies,
  };
}

export const contextOSFormat = {
  tokens: formatTokens,
  clock: formatClock,
};
