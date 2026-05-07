export const ROLE_TASK_STATUS_VALUES = [
  'PENDING',
  'CLAIMED',
  'RUNNING',
  'BLOCKED',
  'FAILED',
  'COMPLETED',
  'CANCELLED',
] as const;

export const ROLE_TASK_PRIORITY_VALUES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const;

export type RoleTaskStatusV1 = (typeof ROLE_TASK_STATUS_VALUES)[number];
export type RoleTaskPriorityV1 = (typeof ROLE_TASK_PRIORITY_VALUES)[number];

export interface RoleTaskContractV1 {
  id: string;
  subject: string;
  description: string;
  status: string;
  priority: string;
  claimed_by: string | null;
  result: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
  goal: string;
  acceptance: string[];
  target_files: string[];
  dependencies: string[];
  current_file: string | null;
  error: string | null;
  worker: string | null;
  pm_task_id: string | null;
  blueprint_id: string | null;
  blueprint_path: string | null;
  runtime_blueprint_path: string | null;
}

export interface ChiefEngineerBlueprintSummaryV1 {
  blueprint_id: string;
  title: string;
  summary: string;
  status: string | null;
  source: string;
  target_files: string[];
  updated_at: string | null;
  raw: Record<string, unknown>;
}

export interface ChiefEngineerBlueprintListV1 {
  blueprints: ChiefEngineerBlueprintSummaryV1[];
  total: number;
}

export interface ChiefEngineerBlueprintDetailV1 {
  blueprint_id: string;
  source: string;
  blueprint: Record<string, unknown>;
}
