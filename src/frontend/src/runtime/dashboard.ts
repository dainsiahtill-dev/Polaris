/**
 * Runtime Dashboard - 主控制台状态选择器
 * 
 * 为主界面提供统一的状态选择入口
 */

import { useMemo } from 'react';
import { useRuntime } from '@/app/hooks/useRuntime';
import type {
  BackendStatus,
  EngineStatus,
  LanceDbStatus,
  SnapshotPayload,
  AnthroState,
} from '@/app/types/appContracts';
import type { QualityGateData } from '@/app/components/pm';
import { PmTask, TaskStatus } from '@/types/task';
import type { LogEntry } from '@/types/log';
import type { FileEditEvent, RuntimeWorkerState } from '@/app/hooks/useRuntime';
import * as Guards from './guards';

// ============================================================
// Dashboard 状态接口
// ============================================================

export interface DashboardState {
  // 连接状态
  connected: boolean;
  reconnecting: boolean;
  error: string | null;
  
  // PM 状态
  pmRunning: boolean;
  pmStatus: BackendStatus | null;
  
  // Director 状态
  directorRunning: boolean;
  directorStatus: BackendStatus | null;
  
  // 引擎状态
  engineStatus: EngineStatus | null;
  
  // LanceDB 状态
  lancedbStatus: LanceDbStatus | null;
  lancedbReady: boolean;
  
  // 文档状态
  docsReady: boolean;
  anthroState: AnthroState | null;
  
  // 任务状态
  tasks: PmTask[];
  taskCount: number;
  completedTaskCount: number;
  failedTaskCount: number;
  
  // 质量门禁
  qualityGate: QualityGateData | null;
  qualityGatePassed: boolean;
  qualityGateScore: number;
  
  // 阶段
  currentPhase: string;
  phaseLabel: string;
  
  // 日志
  executionLogs: LogEntry[];
  llmStreamEvents: LogEntry[];
  processStreamEvents: LogEntry[];
  
  // Worker 状态
  workers: RuntimeWorkerState[];
  
  // Run ID
  runId: string | null;
  
  // 综合评估
  guards: Guards.RuntimeGuardResult;
}

// ============================================================
// 选择器 Hooks
// ============================================================

/**
 * 主控制台完整状态选择器
 */
export function useDashboard(): DashboardState {
  const runtime = useRuntime();
  
  return useMemo(() => {
    const rawState: Guards.RuntimeRawState = {
      live: runtime.live,
      error: runtime.error,
      reconnecting: runtime.reconnecting,
      attemptCount: runtime.attemptCount,
      pmStatus: runtime.pmStatus,
      directorStatus: runtime.directorStatus,
      engineStatus: runtime.engineStatus,
      lancedbStatus: runtime.lancedbStatus,
      snapshot: runtime.snapshot,
      anthroState: runtime.anthroState,
    };
    
    const detailedState: Guards.RuntimeDetailedState = {
      ...rawState,
      qualityGate: runtime.qualityGate,
      currentPhase: runtime.currentPhase,
      tasks: runtime.tasks,
      runId: runtime.runId,
    };
    
    const pmRunning = Guards.isPmRunning(rawState);
    const directorRunning = Guards.isDirectorRunning(rawState);
    const lancedbReady = Guards.isLancedbReady(rawState);
    const docsReady = !Guards.isDocsMissing(rawState);
    const qualityGate = runtime.qualityGate;
    const qualityGatePassed = Guards.isQualityGatePassed(detailedState);
    const qualityGateScore = Guards.getQualityGateScore(detailedState);
    const guards = Guards.evaluateRuntimeGuards(detailedState);
    
    // 阶段标签映射
    const phaseLabel = getPhaseLabel(runtime.currentPhase);
    
    return {
      connected: runtime.live,
      reconnecting: runtime.reconnecting,
      error: runtime.error,
      pmRunning,
      pmStatus: runtime.pmStatus,
      directorRunning,
      directorStatus: runtime.directorStatus,
      engineStatus: runtime.engineStatus,
      lancedbStatus: runtime.lancedbStatus,
      lancedbReady,
      docsReady,
      anthroState: runtime.anthroState,
      tasks: runtime.tasks,
      taskCount: runtime.tasks.length,
      completedTaskCount: Guards.getCompletedTaskCount(detailedState),
      failedTaskCount: Guards.getFailedTaskCount(detailedState),
      qualityGate,
      qualityGatePassed,
      qualityGateScore,
      currentPhase: runtime.currentPhase,
      phaseLabel,
      executionLogs: runtime.executionLogs,
      llmStreamEvents: runtime.llmStreamEvents,
      processStreamEvents: runtime.processStreamEvents,
      workers: runtime.workers,
      runId: runtime.runId,
      guards,
    };
  }, [
    runtime.live,
    runtime.error,
    runtime.reconnecting,
    runtime.attemptCount,
    runtime.pmStatus,
    runtime.directorStatus,
    runtime.engineStatus,
    runtime.lancedbStatus,
    runtime.snapshot,
    runtime.anthroState,
    runtime.qualityGate,
    runtime.currentPhase,
    runtime.tasks,
    runtime.runId,
    runtime.executionLogs,
    runtime.llmStreamEvents,
    runtime.processStreamEvents,
    runtime.workers,
  ]);
}

/**
 * 连接状态选择器
 */
export function useConnectionState() {
  const runtime = useRuntime();
  
  return useMemo(() => ({
    connected: runtime.live,
    reconnecting: runtime.reconnecting,
    error: runtime.error,
    attemptCount: runtime.attemptCount,
  }), [runtime.live, runtime.reconnecting, runtime.error, runtime.attemptCount]);
}

/**
 * PM 状态选择器
 */
export function usePmState() {
  const runtime = useRuntime();
  
  return useMemo(() => ({
    running: Guards.isPmRunning({
      live: runtime.live,
      error: runtime.error,
      reconnecting: runtime.reconnecting,
      attemptCount: runtime.attemptCount,
      pmStatus: runtime.pmStatus,
      directorStatus: runtime.directorStatus,
      engineStatus: runtime.engineStatus,
      lancedbStatus: runtime.lancedbStatus,
      snapshot: runtime.snapshot,
      anthroState: runtime.anthroState,
    }),
    status: runtime.pmStatus,
    stateToken: Guards.getPmStateToken({
      live: runtime.live,
      error: runtime.error,
      reconnecting: runtime.reconnecting,
      attemptCount: runtime.attemptCount,
      pmStatus: runtime.pmStatus,
      directorStatus: runtime.directorStatus,
      engineStatus: runtime.engineStatus,
      lancedbStatus: runtime.lancedbStatus,
      snapshot: runtime.snapshot,
      anthroState: runtime.anthroState,
    }),
  }), [runtime]);
}

/**
 * Director 状态选择器
 */
export function useDirectorState() {
  const runtime = useRuntime();
  
  return useMemo(() => ({
    running: Guards.isDirectorRunning({
      live: runtime.live,
      error: runtime.error,
      reconnecting: runtime.reconnecting,
      attemptCount: runtime.attemptCount,
      pmStatus: runtime.pmStatus,
      directorStatus: runtime.directorStatus,
      engineStatus: runtime.engineStatus,
      lancedbStatus: runtime.lancedbStatus,
      snapshot: runtime.snapshot,
      anthroState: runtime.anthroState,
    }),
    status: runtime.directorStatus,
    failed: Guards.isDirectorFailed({
      live: runtime.live,
      error: runtime.error,
      reconnecting: runtime.reconnecting,
      attemptCount: runtime.attemptCount,
      pmStatus: runtime.pmStatus,
      directorStatus: runtime.directorStatus,
      engineStatus: runtime.engineStatus,
      lancedbStatus: runtime.lancedbStatus,
      snapshot: runtime.snapshot,
      anthroState: runtime.anthroState,
    }),
  }), [runtime]);
}

/**
 * 任务状态选择器
 */
export function useTaskState() {
  const runtime = useRuntime();
  
  return useMemo(() => ({
    tasks: runtime.tasks,
    count: runtime.tasks.length,
    completedCount: runtime.tasks.filter(t => 
      t.done === true || 
      t.completed === true ||
      t.status === TaskStatus.COMPLETED ||
      t.status === TaskStatus.SUCCESS
    ).length,
    failedCount: runtime.tasks.filter(t => 
      t.status === TaskStatus.FAILED
    ).length,
  }), [runtime.tasks]);
}

/**
 * 质量门禁选择器
 */
export function useQualityGate() {
  const runtime = useRuntime();
  
  return useMemo(() => ({
    data: runtime.qualityGate,
    passed: runtime.qualityGate?.passed ?? false,
    score: runtime.qualityGate?.score ?? 0,
    hasCritical: runtime.qualityGate?.issues?.some(i => i.type === 'critical') ?? false,
  }), [runtime.qualityGate]);
}

/**
 * 当前阶段选择器 (Dashboard variant)
 * @deprecated Use useCurrentPhase from './selectors' instead
 */
export function useCurrentPhaseDashboard() {
  const runtime = useRuntime();

  return useMemo(() => ({
    phase: runtime.currentPhase,
    label: getPhaseLabel(runtime.currentPhase),
    isPlanning: Guards.isPhasePlanning(runtime.currentPhase),
    isExecuting: Guards.isPhaseExecuting(runtime.currentPhase),
    isVerification: Guards.isPhaseVerification(runtime.currentPhase),
    isCompleted: Guards.isPhaseCompleted(runtime.currentPhase),
    isError: Guards.isPhaseError(runtime.currentPhase),
  }), [runtime.currentPhase]);
}

/**
 * Worker 状态选择器
 */
export function useWorkerState() {
  const runtime = useRuntime();
  
  return useMemo(() => ({
    workers: runtime.workers,
    busyCount: runtime.workers.filter(w => w.status === 'busy').length,
    idleCount: runtime.workers.filter(w => w.status === 'idle').length,
    failedCount: runtime.workers.filter(w => w.status === 'failed').length,
  }), [runtime.workers]);
}

/**
 * 日志选择器
 */
export function useRuntimeLogs() {
  const runtime = useRuntime();
  
  return useMemo(() => ({
    executionLogs: runtime.executionLogs,
    llmStreamEvents: runtime.llmStreamEvents,
    processStreamEvents: runtime.processStreamEvents,
    dialogueEvents: runtime.dialogueEvents,
  }), [runtime.executionLogs, runtime.llmStreamEvents, runtime.processStreamEvents, runtime.dialogueEvents]);
}

/**
 * 综合守卫评估选择器
 */
export function useRuntimeGuards() {
  const runtime = useRuntime();
  
  return useMemo(() => {
    const detailedState: Guards.RuntimeDetailedState = {
      live: runtime.live,
      error: runtime.error,
      reconnecting: runtime.reconnecting,
      attemptCount: runtime.attemptCount,
      pmStatus: runtime.pmStatus,
      directorStatus: runtime.directorStatus,
      engineStatus: runtime.engineStatus,
      lancedbStatus: runtime.lancedbStatus,
      snapshot: runtime.snapshot,
      anthroState: runtime.anthroState,
      qualityGate: runtime.qualityGate,
      currentPhase: runtime.currentPhase,
      tasks: runtime.tasks,
      runId: runtime.runId,
    };
    
    return Guards.evaluateRuntimeGuards(detailedState);
  }, [runtime]);
}

// ============================================================
// 辅助函数
// ============================================================

function getPhaseLabel(phase: string): string {
  const normalized = phase.toLowerCase().trim();
  
  const labels: Record<string, string> = {
    '': '空闲',
    'idle': '空闲',
    'planning': '规划中',
    'analyzing': '分析中',
    'executing': '执行中',
    'implementation': '实现中',
    'llm_calling': 'LLM 调用中',
    'tool_running': '工具运行中',
    'verification': '验证中',
    'completed': '已完成',
    'done': '已完成',
    'success': '成功',
    'failed': '失败',
    'error': '错误',
    'blocked': '已阻塞',
  };
  
  return labels[normalized] || phase;
}

// ============================================================
// 纯函数选择器（可在组件外使用）
// ============================================================

/**
 * 从 runtime 原始状态提取 Dashboard 状态
 */
export function extractDashboardState(
  runtime: ReturnType<typeof useRuntime>
): DashboardState {
  const rawState: Guards.RuntimeRawState = {
    live: runtime.live,
    error: runtime.error,
    reconnecting: runtime.reconnecting,
    attemptCount: runtime.attemptCount,
    pmStatus: runtime.pmStatus,
    directorStatus: runtime.directorStatus,
    engineStatus: runtime.engineStatus,
    lancedbStatus: runtime.lancedbStatus,
    snapshot: runtime.snapshot,
    anthroState: runtime.anthroState,
  };
  
  const detailedState: Guards.RuntimeDetailedState = {
    ...rawState,
    qualityGate: runtime.qualityGate,
    currentPhase: runtime.currentPhase,
    tasks: runtime.tasks,
    runId: runtime.runId,
  };
  
  return {
    connected: runtime.live,
    reconnecting: runtime.reconnecting,
    error: runtime.error,
    pmRunning: Guards.isPmRunning(rawState),
    pmStatus: runtime.pmStatus,
    directorRunning: Guards.isDirectorRunning(rawState),
    directorStatus: runtime.directorStatus,
    engineStatus: runtime.engineStatus,
    lancedbStatus: runtime.lancedbStatus,
    lancedbReady: Guards.isLancedbReady(rawState),
    docsReady: !Guards.isDocsMissing(rawState),
    anthroState: runtime.anthroState,
    tasks: runtime.tasks,
    taskCount: runtime.tasks.length,
    completedTaskCount: Guards.getCompletedTaskCount(detailedState),
    failedTaskCount: Guards.getFailedTaskCount(detailedState),
    qualityGate: runtime.qualityGate,
    qualityGatePassed: Guards.isQualityGatePassed(detailedState),
    qualityGateScore: Guards.getQualityGateScore(detailedState),
    currentPhase: runtime.currentPhase,
    phaseLabel: getPhaseLabel(runtime.currentPhase),
    executionLogs: runtime.executionLogs,
    llmStreamEvents: runtime.llmStreamEvents,
    processStreamEvents: runtime.processStreamEvents,
    workers: runtime.workers,
    runId: runtime.runId,
    guards: Guards.evaluateRuntimeGuards(detailedState),
  };
}
