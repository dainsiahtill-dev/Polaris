/**
 * Task Trace Type Definitions
 *
 * 任务追踪数据层类型定义，用于 Runtime Hook 数据层扩展
 */

/** 任务追踪事件 */
export interface TaskTraceEvent {
  event_id: string;
  run_id: string;
  role: 'pm' | 'director' | 'qa' | 'architect' | 'chief_engineer';
  task_id: string;
  seq: number;
  phase: string;
  step_kind: 'phase' | 'llm' | 'tool' | 'validation' | 'retry' | 'system';
  step_title: string;
  step_detail: string;
  status: 'started' | 'running' | 'completed' | 'failed' | 'skipped';
  attempt: number;
  visibility: 'summary' | 'debug';
  ts: string;
  refs: {
    related_task_ids?: string[];
    current_file?: string;
    error_code?: string;
    tool_name?: string;
  };
}

/** 任务追踪映射 (Map 类型，用于 Hook 状态) */
export type TaskTraceMap = Map<string, TaskTraceEvent[]>;

/** 任务追踪映射 (对象类型，用于 API 序列化) */
export interface TaskTraceMapObject {
  [taskId: string]: TaskTraceEvent[];
}

export type TaskTraceStatus = TaskTraceEvent['status'];
export type TaskTraceKind = TaskTraceEvent['step_kind'];
