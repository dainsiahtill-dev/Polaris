/**
 * useRuntimeLogs - 日志流管理 Hook
 *
 * 职责:
 * - 管理 LLM 流日志
 * - 管理 Process 流日志
 * - 管理 Execution 日志
 * - 提供日志追加和批量更新
 */

import { useCallback } from 'react';
import { useRuntimeStore } from './useRuntimeStore';
import type { LogEntry } from '@/types/log';
import * as Parsing from './runtimeParsing';

/**
 * useRuntimeLogs - 管理所有日志流
 */
export function useRuntimeLogs() {
  const llmStreamEvents = useRuntimeStore((s) => s.llmStreamEvents);
  const processStreamEvents = useRuntimeStore((s) => s.processStreamEvents);
  const executionLogs = useRuntimeStore((s) => s.executionLogs);

  const appendLlmStreamEvent = useRuntimeStore((s) => s.appendLlmStreamEvent);
  const setLlmStreamEvents = useRuntimeStore((s) => s.setLlmStreamEvents);
  const appendProcessStreamEvent = useRuntimeStore((s) => s.appendProcessStreamEvent);
  const setProcessStreamEvents = useRuntimeStore((s) => s.setProcessStreamEvents);
  const appendExecutionLog = useRuntimeStore((s) => s.appendExecutionLog);
  const setExecutionLogs = useRuntimeStore((s) => s.setExecutionLogs);

  /**
   * 追加单个 LLM 流日志
   */
  const appendLlmLog = useCallback(
    (log: LogEntry) => {
      appendLlmStreamEvent(log);
    },
    [appendLlmStreamEvent]
  );

  /**
   * 批量更新 LLM 流日志
   */
  const updateLlmLogs = useCallback(
    (logs: LogEntry[]) => {
      if (logs.length === 0) return;
      // 使用 appendLlmStreamEntries 逻辑合并
      setLlmStreamEvents([...llmStreamEvents, ...logs].slice(-180));
    },
    [llmStreamEvents, setLlmStreamEvents]
  );

  /**
   * 追加单个 Process 流日志
   */
  const appendProcessLog = useCallback(
    (log: LogEntry) => {
      appendProcessStreamEvent(log);
    },
    [appendProcessStreamEvent]
  );

  /**
   * 批量更新 Process 流日志
   */
  const updateProcessLogs = useCallback(
    (logs: LogEntry[]) => {
      if (logs.length === 0) return;
      const merged = Parsing.appendLogEntries(processStreamEvents, logs, 240);
      setProcessStreamEvents(merged);
    },
    [processStreamEvents, setProcessStreamEvents]
  );

  /**
   * 追加单个 Execution 日志
   */
  const appendExecutionLogEntry = useCallback(
    (log: LogEntry) => {
      appendExecutionLog(log);
    },
    [appendExecutionLog]
  );

  /**
   * 批量更新 Execution 日志
   */
  const updateExecutionLogs = useCallback(
    (logs: LogEntry[]) => {
      if (logs.length === 0) return;
      const merged = Parsing.appendLogEntries(executionLogs, logs, 100);
      setExecutionLogs(merged);
    },
    [executionLogs, setExecutionLogs]
  );

  return {
    // State
    llmStreamEvents,
    processStreamEvents,
    executionLogs,

    // Actions
    appendLlmLog,
    updateLlmLogs,
    appendProcessLog,
    updateProcessLogs,
    appendExecutionLogEntry,
    updateExecutionLogs,
  };
}

/**
 * useTaskProgress - 任务进度管理
 */
export function useTaskProgress() {
  const taskProgressMap = useRuntimeStore((s) => s.taskProgressMap);
  const updateTaskProgress = useRuntimeStore((s) => s.updateTaskProgress);
  const setTasks = useRuntimeStore((s) => s.setTasks);

  const tasks = useRuntimeStore((s) => s.tasks);

  const updateTask = useCallback(
    (taskId: string, progress: Parameters<typeof updateTaskProgress>[1]) => {
      updateTaskProgress(taskId, progress);
    },
    [updateTaskProgress]
  );

  const getTaskProgress = useCallback(
    (taskId: string) => taskProgressMap.get(taskId),
    [taskProgressMap]
  );

  return {
    tasks,
    setTasks,
    taskProgressMap,
    updateTask,
    getTaskProgress,
  };
}

/**
 * useTaskTrace - 任务追踪管理
 */
export function useTaskTrace() {
  const taskTraceMap = useRuntimeStore((s) => s.taskTraceMap);
  const appendTaskTrace = useRuntimeStore((s) => s.appendTaskTrace);

  const getTaskTraces = useCallback(
    (taskId: string) => taskTraceMap.get(taskId) || [],
    [taskTraceMap]
  );

  return {
    taskTraceMap,
    appendTaskTrace,
    getTaskTraces,
  };
}

/**
 * useSequentialTrace - 顺序追踪管理
 */
export function useSequentialTrace() {
  const sequentialTraceMap = useRuntimeStore((s) => s.sequentialTraceMap);
  const appendSequentialTrace = useRuntimeStore((s) => s.appendSequentialTrace);

  const getSequentialTraces = useCallback(
    (runId: string) => sequentialTraceMap.get(runId) || [],
    [sequentialTraceMap]
  );

  return {
    sequentialTraceMap,
    appendSequentialTrace,
    getSequentialTraces,
  };
}