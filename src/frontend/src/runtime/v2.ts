/**
 * Runtime V2 Types - 前端运行时类型定义
 * 
 * 对应后端的 runtime_v2.py 协议类型
 */

import { z } from 'zod';
import { devLogger } from '@/app/utils/devLogger';

// ============================================================
// 枚举定义
// ============================================================

export const RoleTypeSchema = z.enum(['PM', 'ChiefEngineer', 'Director', 'QA']);
export type RoleType = z.infer<typeof RoleTypeSchema>;

export const RoleStateSchema = z.enum([
  'idle',
  'analyzing',
  'planning',
  'executing',
  'verification',
  'completed',
  'failed',
  'blocked',
]);
export type RoleState = z.infer<typeof RoleStateSchema>;

export const WorkerStateSchema = z.enum([
  'idle',
  'claimed',
  'in_progress',
  'completed',
  'failed',
]);
export type WorkerState = z.infer<typeof WorkerStateSchema>;

export const TaskStateSchema = z.enum([
  'pending',
  'ready',
  'claimed',
  'in_progress',
  'completed',
  'failed',
  'blocked',
  'cancelled',
]);
export type TaskState = z.infer<typeof TaskStateSchema>;

export const EventSeveritySchema = z.enum(['debug', 'info', 'warning', 'error']);
export type EventSeverity = z.infer<typeof EventSeveritySchema>;

// 阶段枚举
export const PhaseSchema = z.enum([
  'pending',
  'intake',
  'docs_check',
  'architect',
  'planning',
  'implementation',
  'verification',
  'qa_gate',
  'handover',
  'completed',
  'failed',
  'blocked',
  'cancelled',
]);
export type Phase = z.infer<typeof PhaseSchema>;

// ============================================================
// 类型定义
// ============================================================

export const RuntimeRoleStateSchema = z.object({
  role: RoleTypeSchema,
  state: RoleStateSchema,
  task_id: z.string().nullable(),
  task_title: z.string().nullable(),
  detail: z.string().nullable(),
  updated_at: z.string(),
});
export type RuntimeRoleState = z.infer<typeof RuntimeRoleStateSchema>;

export const RuntimeWorkerStateSchema = z.object({
  id: z.string(),
  state: WorkerStateSchema,
  task_id: z.string().nullable(),
  updated_at: z.string(),
});
export type RuntimeWorkerState = z.infer<typeof RuntimeWorkerStateSchema>;

export const RuntimeTaskNodeSchema = z.object({
  id: z.string(),
  title: z.string(),
  level: z.number().min(1).max(10),
  parent_id: z.string().nullable(),
  state: TaskStateSchema,
  blocked_by: z.array(z.string()),
  progress: z.number().min(0).max(100),
});
export type RuntimeTaskNode = z.infer<typeof RuntimeTaskNodeSchema>;

export const RuntimeSummarySchema = z.object({
  total: z.number(),
  completed: z.number(),
  failed: z.number(),
  blocked: z.number(),
});
export type RuntimeSummary = z.infer<typeof RuntimeSummarySchema>;

export const RuntimeSnapshotV2Schema = z.object({
  type: z.literal('runtime_snapshot_v2'),
  schema_version: z.literal(2),
  run_id: z.string(),
  ts: z.string(),
  phase: PhaseSchema,
  roles: z.record(z.string(), RuntimeRoleStateSchema),
  workers: z.array(RuntimeWorkerStateSchema),
  tasks: z.array(RuntimeTaskNodeSchema),
  summary: RuntimeSummarySchema,
  error: z.string().nullable(),
});
export type RuntimeSnapshotV2 = z.infer<typeof RuntimeSnapshotV2Schema>;

export const RuntimeEventV2Schema = z.object({
  type: z.literal('runtime_event_v2'),
  schema_version: z.literal(2),
  event_id: z.string(),
  seq: z.number().min(0),
  run_id: z.string(),
  ts: z.string(),
  phase: PhaseSchema,
  role: RoleTypeSchema.nullable(),
  node_level: z.number().min(1).max(10).nullable(),
  state: z.string().nullable(),
  task_id: z.string().nullable(),
  worker_id: z.string().nullable(),
  severity: EventSeveritySchema,
  message: z.string(),
  detail: z.string().nullable(),
  metrics: z.record(z.string(), z.unknown()),
});
export type RuntimeEventV2 = z.infer<typeof RuntimeEventV2Schema>;

// ============================================================
// 状态映射辅助函数
// ============================================================

export function getPhaseLabel(phase: Phase): string {
  const labels: Record<Phase, string> = {
    pending: '待处理',
    intake: '需求接入',
    docs_check: '文档检查',
    architect: '架构设计',
    planning: '任务规划',
    implementation: '代码实现',
    verification: '验证测试',
    qa_gate: '质量门禁',
    handover: '交付',
    completed: '已完成',
    failed: '失败',
    blocked: '已阻塞',
    cancelled: '已取消',
  };
  return labels[phase] || phase;
}

export function getPhaseColor(phase: Phase): string {
  const colors: Record<Phase, string> = {
    pending: 'slate',
    intake: 'cyan',
    docs_check: 'cyan',
    architect: 'cyan',
    planning: 'cyan',
    implementation: 'cyan',
    verification: 'cyan',
    qa_gate: 'amber',
    handover: 'emerald',
    completed: 'emerald',
    failed: 'red',
    blocked: 'amber',
    cancelled: 'slate',
  };
  return colors[phase] || 'slate';
}

export function getRoleStateColor(state: RoleState): string {
  const colors: Record<RoleState, string> = {
    idle: 'slate',
    analyzing: 'cyan',
    planning: 'cyan',
    executing: 'cyan',
    verification: 'amber',
    completed: 'emerald',
    failed: 'red',
    blocked: 'amber',
  };
  return colors[state] || 'slate';
}

export function getWorkerStateColor(state: WorkerState): string {
  const colors: Record<WorkerState, string> = {
    idle: 'slate',
    claimed: 'amber',
    in_progress: 'cyan',
    completed: 'emerald',
    failed: 'red',
  };
  return colors[state] || 'slate';
}

export function getTaskStateColor(state: TaskState): string {
  const colors: Record<TaskState, string> = {
    pending: 'slate',
    ready: 'cyan',
    claimed: 'amber',
    in_progress: 'cyan',
    completed: 'emerald',
    failed: 'red',
    blocked: 'amber',
    cancelled: 'slate',
  };
  return colors[state] || 'slate';
}

// ============================================================
// 验证函数
// ============================================================

export function validateSnapshotV2(data: unknown): RuntimeSnapshotV2 | null {
  const result = RuntimeSnapshotV2Schema.safeParse(data);
  if (result.success) {
    return result.data;
  }
  devLogger.warn('[Runtime] Invalid snapshot V2:', result.error);
  return null;
}

export function validateEventV2(data: unknown): RuntimeEventV2 | null {
  const result = RuntimeEventV2Schema.safeParse(data);
  if (result.success) {
    return result.data;
  }
  devLogger.warn('[Runtime] Invalid event V2:', result.error);
  return null;
}
