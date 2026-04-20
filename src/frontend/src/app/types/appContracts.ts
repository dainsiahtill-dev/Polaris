import type { WorkspaceStatus } from '@/app/components/DocsInitDialog';
import type { MemoItem } from '@/app/components/MemoPanel';

export interface BackendSettings {
  workspace: string;
  pm_backend: string;
  pm_model?: string;
  director_model?: string;
  model: string;
  prompt_profile: string;
  architect_spec_model?: string;
  architect_spec_provider?: string;
  architect_spec_base_url?: string;
  architect_spec_api_key?: string;
  architect_spec_api_path?: string;
  architect_spec_timeout?: number;
  docs_init_model?: string;
  docs_init_provider?: string;
  docs_init_base_url?: string;
  docs_init_api_key?: string;
  docs_init_api_path?: string;
  docs_init_timeout?: number;
  interval: number;
  timeout: number;
  refresh_interval: number;
  auto_refresh: boolean;
  show_memory: boolean;
  io_fsync_mode?: string;
  memory_refs_mode?: string;
  ramdisk_root?: string;
  json_log_path?: string;
  pm_show_output?: boolean;
  pm_runs_director?: boolean;
  pm_director_show_output?: boolean;
  pm_director_timeout?: number;
  pm_director_iterations?: number;
  pm_director_match_mode?: string;
  pm_max_failures?: number;
  pm_max_blocked?: number;
  pm_max_same?: number;
  director_iterations?: number;
  director_execution_mode?: 'serial' | 'parallel' | string;
  director_max_parallel_tasks?: number;
  director_ready_timeout_seconds?: number;
  director_claim_timeout_seconds?: number;
  director_phase_timeout_seconds?: number;
  director_complete_timeout_seconds?: number;
  director_task_timeout_seconds?: number;
  director_forever?: boolean;
  director_show_output?: boolean;
  slm_enabled?: boolean;
  qa_enabled?: boolean;
  debug_tracing?: boolean;
}

export interface BackendStatus {
  running: boolean;
  pid: number | null;
  started_at: number | null;
  mode?: string;
  log_path?: string;
  source?: 'handle' | 'status_file' | 'none' | string;
  status?: Record<string, unknown> | null;
}

export interface MemoListResponse {
  items: MemoItem[];
  count: number;
}

export interface LanceDbStatus {
  ok: boolean;
  error?: string | null;
  python?: string | null;
  version?: string | null;
}

export type LlmStatus = import('../components/llm/types').LLMStatus;

export interface AnthroState {
  last_reflection_step: number;
  recent_error_count: number;
  total_memories: number;
  total_reflections: number;
}

export interface AgentsReviewInfo {
  needs_review: boolean;
  has_agents: boolean;
  draft_path?: string | null;
  feedback_path?: string | null;
  draft_mtime?: string | null;
  feedback_mtime?: string | null;
  draft_failed?: boolean | null;
}

export interface RuntimeIssue {
  code: string;
  title: string;
  detail: string;
}

export interface EngineRoleStatus {
  status?: string;
  running?: boolean;
  task_id?: string;
  task_title?: string;
  detail?: string;
  updated_at?: string;
  meta?: Record<string, unknown>;
}

export interface EngineStatus {
  schema_version?: number;
  running?: boolean;
  phase?: string;
  run_id?: string;
  pm_iteration?: number;
  config?: Record<string, unknown>;
  roles?: Record<string, EngineRoleStatus>;
  summary?: Record<string, unknown>;
  updated_at?: string;
  error?: string;
  path?: string;
}

export interface ResidentIdentityPayload {
  resident_id?: string;
  name?: string;
  mission?: string;
  owner?: string;
  active_workspace?: string;
  operating_mode?: string;
  values?: string[];
  memory_lineage?: string[];
  capability_profile?: Record<string, number>;
  created_at?: string;
  updated_at?: string;
}

export interface ResidentAgendaPayload {
  current_focus?: string[];
  pending_goal_ids?: string[];
  approved_goal_ids?: string[];
  materialized_goal_ids?: string[];
  risk_register?: string[];
  next_actions?: string[];
  active_experiment_ids?: string[];
  active_improvement_ids?: string[];
  last_tick_at?: string;
  tick_count?: number;
  updated_at?: string;
}

export interface ResidentRuntimePayload {
  active?: boolean;
  mode?: string;
  last_tick_at?: string;
  tick_count?: number;
  last_error?: string;
  last_summary?: Record<string, unknown>;
  updated_at?: string;
}

export interface ResidentStatusPayload {
  workspace?: string;
  identity?: ResidentIdentityPayload;
  runtime?: ResidentRuntimePayload;
  agenda?: ResidentAgendaPayload;
  counts?: Record<string, number>;
}

export interface ResidentDecisionOptionPayload {
  option_id?: string;
  label?: string;
  rationale?: string;
  strategy_tags?: string[];
  estimated_score?: number;
}

// Phase 1.2: Goal Execution Projection
export interface GoalExecutionTaskProgress {
  task_id?: string;
  subject?: string;
  status?: 'pending' | 'in_progress' | 'completed' | 'failed' | 'blocked';
  progress_percent?: number;
  started_at?: string;
  completed_at?: string;
}

export interface GoalExecutionView {
  goal_id: string;
  stage: 'planning' | 'coding' | 'testing' | 'review' | 'completed' | 'unknown';
  percent: number;
  current_task?: string;
  eta_minutes?: number;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  started_at?: string;
  updated_at: string;
  task_progress: GoalExecutionTaskProgress[];
}

export interface ResidentDecisionPayload {
  decision_id?: string;
  workspace?: string;
  timestamp?: string;
  run_id?: string;
  actor?: string;
  stage?: string;
  goal_id?: string;
  task_id?: string;
  summary?: string;
  context_refs?: string[];
  options?: ResidentDecisionOptionPayload[];
  selected_option_id?: string;
  strategy_tags?: string[];
  expected_outcome?: Record<string, unknown>;
  actual_outcome?: Record<string, unknown>;
  verdict?: string;
  evidence_refs?: string[];
  confidence?: number;
  // Phase 1.1: EvidenceBundle integration
  evidence_bundle_id?: string;
  parent_decision_id?: string;
  affected_files?: string[];
  affected_symbols?: string[];
}

export interface ResidentGoalPayload {
  goal_id?: string;
  goal_type?: string;
  title?: string;
  motivation?: string;
  source?: string;
  expected_value?: number;
  risk_score?: number;
  scope?: string[];
  budget?: Record<string, unknown>;
  evidence_refs?: string[];
  status?: string;
  approval_note?: string;
  fingerprint?: string;
  derived_from?: string[];
  pm_contract_outline?: Record<string, unknown>;
  materialization_artifacts?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface ResidentInsightPayload {
  insight_id?: string;
  insight_type?: string;
  strategy_tag?: string;
  summary?: string;
  recommendation?: string;
  confidence?: number;
  evidence_refs?: string[];
  created_at?: string;
}

export interface ResidentSkillPayload {
  skill_id?: string;
  name?: string;
  trigger?: string;
  preconditions?: string[];
  steps?: string[];
  evidence_refs?: string[];
  failure_modes?: string[];
  confidence?: number;
  version?: number;
  source_decision_ids?: string[];
  created_at?: string;
  updated_at?: string;
}

export interface ResidentExperimentPayload {
  experiment_id?: string;
  source_decision_id?: string;
  baseline_strategy?: string;
  counterfactual_strategy?: string;
  metrics_before?: Record<string, unknown>;
  metrics_after?: Record<string, unknown>;
  confidence?: number;
  recommendation?: string;
  rollback_plan?: string;
  status?: string;
  evidence_refs?: string[];
  created_at?: string;
}

export interface ResidentImprovementPayload {
  improvement_id?: string;
  category?: string;
  title?: string;
  description?: string;
  target_surface?: string;
  evidence_refs?: string[];
  experiment_ids?: string[];
  confidence?: number;
  rollback_plan?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ResidentCapabilityNodePayload {
  capability_id?: string;
  name?: string;
  kind?: string;
  score?: number;
  success_rate?: number;
  attempts?: number;
  evidence_count?: number;
  supporting_skill_ids?: string[];
  supporting_strategy_tags?: string[];
  updated_at?: string;
}

export interface ResidentCapabilityGraphPayload {
  generated_at?: string;
  capabilities?: ResidentCapabilityNodePayload[];
  gaps?: string[];
}

export interface ResidentPmRunPayload {
  directive?: string;
  metadata?: Record<string, unknown>;
  run_id?: string;
  status?: string;
  message?: string | null;
  reason_code?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  stage_results?: Record<string, unknown> | null;
  artifacts?: Array<Record<string, unknown>> | null;
}

export interface ResidentGoalStagePayload {
  goal?: ResidentGoalPayload;
  goal_id?: string;
  goal_status?: string;
  staged_at?: string;
  promoted_to_pm_runtime?: boolean;
  contract?: Record<string, unknown>;
  artifacts?: Record<string, unknown>;
  promotion?: Record<string, unknown>;
  pm_run?: ResidentPmRunPayload;
}

export interface ResidentGoalRunPayload {
  goal?: ResidentGoalPayload;
  staging?: ResidentGoalStagePayload;
  pm_run?: ResidentPmRunPayload;
}

export interface ResidentStatusDetailsPayload extends ResidentStatusPayload {
  decisions?: ResidentDecisionPayload[];
  goals?: ResidentGoalPayload[];
  insights?: ResidentInsightPayload[];
  skills?: ResidentSkillPayload[];
  experiments?: ResidentExperimentPayload[];
  improvements?: ResidentImprovementPayload[];
  capability_graph?: ResidentCapabilityGraphPayload;
  // Phase 1.2: Goal Execution Projection (via WebSocket status)
  goal_executions?: GoalExecutionView[];
}

export interface SnapshotPayload {
  timestamp: string;
  run_id?: string;
  pm_iteration?: number;
  focus?: string;
  notes?: string;
  tasks?: unknown[];
  goals?: string[] | null;
  plan_text?: string | null;
  plan_mtime?: string | null;
  plan_text_normalized?: boolean;
  agents_content?: string | null;
  agents_mtime?: string | null;
  file_status?: string[];
  file_paths?: string[];
  pm_state?: Record<string, unknown>;
  director_state?: Record<string, unknown>;
  agents_review?: AgentsReviewInfo | null;
  runtime_issues?: RuntimeIssue[] | null;
  git?: {
    present?: boolean;
    root?: string;
  };
  resident?: ResidentStatusPayload | null;
  workspace_status?: WorkspaceStatus | null;
  docs_present?: boolean;
}

export interface FilePayload {
  content: string;
  mtime: string;
}

// ============================================================================
// WebSocket Event Types
// ============================================================================

export type WebSocketEventType =
  | 'status'
  | 'dialogue_event'
  | 'runtime_event'
  | 'llm_stream'
  | 'process_stream'
  | 'file_edit'
  | 'task_progress'
  | 'task_trace'
  | 'snapshot'
  | 'line';
