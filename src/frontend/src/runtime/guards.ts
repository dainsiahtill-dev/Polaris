/**
 * Runtime Guards - 运行时状态守卫
 * 
 * 纯函数，用于检查运行时状态的各种条件
 */

import type {
  BackendStatus,
  EngineStatus,
  LanceDbStatus,
  SnapshotPayload,
  AnthroState,
} from '@/app/types/appContracts';
import type { QualityGateData } from '@/app/components/pm';
import type { PmTask } from '@/types/task';
import { TaskStatus } from '@/types/task';

// ============================================================
// 基础类型守卫
// ============================================================

export interface RuntimeRawState {
  live: boolean;
  error: string | null;
  reconnecting: boolean;
  attemptCount: number;
  pmStatus: BackendStatus | null;
  directorStatus: BackendStatus | null;
  engineStatus: EngineStatus | null;
  lancedbStatus: LanceDbStatus | null;
  snapshot: SnapshotPayload | null;
  anthroState: AnthroState | null;
}

export interface RuntimeDetailedState extends RuntimeRawState {
  qualityGate: QualityGateData | null;
  currentPhase: string;
  tasks: PmTask[];
  runId: string | null;
}

// ============================================================
// 连接状态守卫
// ============================================================

export function guardIsConnected(state: RuntimeRawState): boolean {
  return state.live === true;
}

export function guardIsReconnecting(state: RuntimeRawState): boolean {
  return state.reconnecting === true;
}

export function hasConnectionError(state: RuntimeRawState): boolean {
  return state.error !== null && state.error !== '';
}

// ============================================================
// PM 状态守卫
// ============================================================

export function isPmRunning(state: RuntimeRawState): boolean {
  if (!state.pmStatus) return false;
  return state.pmStatus.running === true;
}

export function isPmIdle(state: RuntimeRawState): boolean {
  return !isPmRunning(state);
}

export function getPmStateToken(state: RuntimeRawState): string {
  const status = state.pmStatus;
  if (!status) return '';
  
  const root = status as unknown as Record<string, unknown>;
  const nested = typeof root.status === 'object' ? root.status as Record<string, unknown> : null;
  const deepNested = nested && typeof nested.status === 'object' ? nested.status as Record<string, unknown> : null;
  
  const token = 
    String(deepNested?.state || nested?.state || root?.state || '').toLowerCase().trim() ||
    String(deepNested?.status || nested?.status || root?.status || '').toLowerCase().trim();
  
  return token;
}

// ============================================================
// Director 状态守卫
// ============================================================

export function isDirectorRunning(state: RuntimeRawState): boolean {
  if (!state.directorStatus) return false;
  
  const root = state.directorStatus as unknown as Record<string, unknown>;
  const nested = root && typeof root.status === 'object' ? root.status as Record<string, unknown> : null;
  const deepNested = nested && typeof nested.status === 'object' ? nested.status as Record<string, unknown> : null;
  
  const explicitRunning = state.directorStatus.running === true;
  const tokenState = String(deepNested?.state || nested?.state || root?.state || '').toUpperCase();
  
  return explicitRunning || tokenState === 'RUNNING';
}

export function isDirectorIdle(state: RuntimeRawState): boolean {
  return !isDirectorRunning(state);
}

export function getDirectorStateToken(state: RuntimeRawState): string {
  const status = state.directorStatus;
  if (!status) return '';
  
  const root = status as unknown as Record<string, unknown>;
  const nested = root && typeof root.status === 'object' ? root.status as Record<string, unknown> : null;
  const deepNested = nested && typeof nested.status === 'object' ? nested.status as Record<string, unknown> : null;
  
  return String(deepNested?.state || nested?.state || root?.state || '').toLowerCase().trim();
}

export function isDirectorFailed(state: RuntimeRawState): boolean {
  const token = getDirectorStateToken(state);
  return token === 'failed' || token === 'error' || token === 'deadlock';
}

// ============================================================
// LanceDB 状态守卫
// ============================================================

export function isLancedbBlocked(state: RuntimeRawState): boolean {
  const status = state.lancedbStatus;
  if (!status) return false;
  
  const root = status as unknown as Record<string, unknown>;
  return root?.blocked === true || root?.error !== undefined;
}

export function isLancedbReady(state: RuntimeRawState): boolean {
  const status = state.lancedbStatus;
  if (!status) return false;
  
  const root = status as unknown as Record<string, unknown>;
  return root?.ready === true || root?.healthy === true;
}

// ============================================================
// Anthro/文档状态守卫
// ============================================================

export function isDocsMissing(state: RuntimeRawState): boolean {
  const anthro = state.anthroState;
  if (!anthro) return true;
  
  const root = anthro as unknown as Record<string, unknown>;
  const docsReady = root?.docs_ready;
  
  if (typeof docsReady === 'boolean') {
    return !docsReady;
  }
  
  const docsStatus = String(root?.docs_status || '').toLowerCase();
  return docsStatus !== 'ready' && docsStatus !== 'complete' && docsStatus !== 'completed';
}

export function hasAnthroState(state: RuntimeRawState): boolean {
  return state.anthroState !== null;
}

// ============================================================
// 任务状态守卫
// ============================================================

export function hasTasks(state: RuntimeDetailedState): boolean {
  return state.tasks.length > 0;
}

export function hasCompletedTasks(state: RuntimeDetailedState): boolean {
  return state.tasks.some(task => 
    task.done === true || 
    task.completed === true || 
    task.status === TaskStatus.COMPLETED ||
    task.status === TaskStatus.SUCCESS
  );
}

export function hasFailedTasks(state: RuntimeDetailedState): boolean {
  return state.tasks.some(task => 
    task.status === TaskStatus.FAILED
  );
}

export function getTaskCount(state: RuntimeDetailedState): number {
  return state.tasks.length;
}

export function getCompletedTaskCount(state: RuntimeDetailedState): number {
  return state.tasks.filter(task => 
    task.done === true || 
    task.completed === true ||
    task.status === TaskStatus.COMPLETED ||
    task.status === TaskStatus.SUCCESS
  ).length;
}

export function getFailedTaskCount(state: RuntimeDetailedState): number {
  return state.tasks.filter(task => 
    task.status === TaskStatus.FAILED
  ).length;
}

// ============================================================
// 质量门禁守卫
// ============================================================

export function hasQualityGate(state: RuntimeDetailedState): boolean {
  return state.qualityGate !== null;
}

export function isQualityGatePassed(state: RuntimeDetailedState): boolean {
  const qg = state.qualityGate;
  if (!qg) return false;
  return qg.passed === true;
}

export function isQualityGateFailed(state: RuntimeDetailedState): boolean {
  const qg = state.qualityGate;
  if (!qg) return false;
  return qg.passed === false;
}

export function getQualityGateScore(state: RuntimeDetailedState): number {
  const qg = state.qualityGate;
  return qg?.score ?? 0;
}

export function hasCriticalIssues(state: RuntimeDetailedState): boolean {
  const qg = state.qualityGate;
  if (!qg) return false;
  return qg.issues?.some(issue => issue.type === 'critical') ?? false;
}

// ============================================================
// 阶段守卫
// ============================================================

export function isPhasePlanning(phase: string): boolean {
  const normalized = phase.toLowerCase().trim();
  return normalized === 'planning' || normalized === 'pm_planning';
}

export function isPhaseExecuting(phase: string): boolean {
  const normalized = phase.toLowerCase().trim();
  return normalized === 'executing' || 
         normalized === 'implementation' ||
         normalized === 'tool_running' ||
         normalized === 'llm_calling' ||
         normalized.startsWith('director_');
}

export function isPhaseVerification(phase: string): boolean {
  const normalized = phase.toLowerCase().trim();
  return normalized === 'verification' || 
         normalized === 'qa_gate' ||
         normalized.startsWith('qa_');
}

export function isPhaseCompleted(phase: string): boolean {
  const normalized = phase.toLowerCase().trim();
  return normalized === 'completed' || 
         normalized === 'done' || 
         normalized === 'success' ||
         normalized === 'handover';
}

export function isPhaseError(phase: string): boolean {
  const normalized = phase.toLowerCase().trim();
  return normalized === 'error' || 
         normalized === 'failed' ||
         normalized === 'blocked';
}

// ============================================================
// LLM 阻塞守卫
// ============================================================

export function isLlmBlocked(state: RuntimeRawState): boolean {
  const engine = state.engineStatus;
  if (!engine) return false;
  
  const root = engine as unknown as Record<string, unknown>;
  const blocked = root?.llm_blocked || root?.blocked || root?.llm_blocked;
  return blocked === true;
}

export function hasLlmProvider(state: RuntimeRawState): boolean {
  const engine = state.engineStatus;
  if (!engine) return false;
  
  const root = engine as unknown as Record<string, unknown>;
  const provider = root?.provider || root?.llm_provider;
  return provider !== undefined && provider !== null && String(provider).trim() !== '';
}

// ============================================================
// 综合守卫
// ============================================================

export interface RuntimeGuardResult {
  ready: boolean;
  blockers: string[];
  warnings: string[];
}

export function evaluateRuntimeGuards(state: RuntimeDetailedState): RuntimeGuardResult {
  const blockers: string[] = [];
  const warnings: string[] = [];
  
  if (!guardIsConnected(state)) {
    blockers.push('WebSocket 未连接');
  } else if (hasConnectionError(state)) {
    blockers.push(`连接错误: ${state.error}`);
  }
  
  if (isLancedbBlocked(state)) {
    blockers.push('LanceDB 被阻塞');
  }
  
  if (isDocsMissing(state)) {
    blockers.push('项目文档缺失');
  }
  
  if (isLlmBlocked(state)) {
    warnings.push('LLM 调用被阻塞');
  }
  
  if (hasCriticalIssues(state)) {
    blockers.push('质量门禁存在严重问题');
  }
  
  if (isDirectorFailed(state)) {
    warnings.push('Director 执行失败');
  }
  
  const ready = blockers.length === 0;
  
  return { ready, blockers, warnings };
}

// ============================================================
// Agent 需求守卫
// ============================================================

export interface AgentsRequiredResult {
  pm: boolean;
  director: boolean;
  qa: boolean;
}

export function getRequiredAgents(state: RuntimeDetailedState, currentPhase: string): AgentsRequiredResult {
  const phase = currentPhase.toLowerCase().trim();
  
  return {
    pm: isPhasePlanning(phase) || isDocsMissing(state),
    director: isPhaseExecuting(phase) || hasTasks(state),
    qa: isPhaseVerification(phase) || isPhaseCompleted(phase),
  };
}

export function isAgentRequired(agent: 'pm' | 'director' | 'qa', state: RuntimeDetailedState, currentPhase: string): boolean {
  const required = getRequiredAgents(state, currentPhase);
  return required[agent];
}
