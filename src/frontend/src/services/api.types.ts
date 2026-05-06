/**
 * API Service Type Definitions
 *
 * 统一API层类型定义，消除any类型使用
 */

// ============================================================================
// Base API Response Types
// ============================================================================

export interface ApiErrorDetail {
  detail?: string;
  error?: string;
  message?: string;
  code?: string;
}

export interface ApiResult<T> {
  ok: boolean;
  data?: T;
  error?: string;
}

export interface ApiListResponse<T> {
  items: T[];
  count: number;
  total?: number;
}

// ============================================================================
// Process Status Types
// ============================================================================

export interface ProcessStatus {
  running: boolean;
  pid: number | null;
  started_at: number | null;
  mode?: string;
  log_path?: string;
  source?: 'handle' | 'status_file' | 'none' | 'v2_service' | string;
  status?: Record<string, unknown> | null;
}

export interface DirectorStatusPayload {
  state?: string;
  running?: boolean;
  pid?: number;
  started_at?: number;
  mode?: string;
  log_path?: string;
  source?: string;
  status?: Record<string, unknown>;
}

// ============================================================================
// Task Types
// ============================================================================

export interface TaskResponse {
  id: string;
  command: string;
  state: string;
  timeout: number;
  result?: {
    success: boolean;
    exit_code: number;
    stdout: string;
    stderr: string;
    duration_ms: number;
  };
}

export interface TodoItemResponse {
  id: string;
  content: string;
  status: string;
  priority: string;
  tags: string[];
}

export interface TodoSummaryResponse {
  summary: Record<string, unknown>;
  next_action: TodoItemResponse | null;
}

// ============================================================================
// Token Budget Types
// ============================================================================

export interface TokenStatusResponse {
  used_tokens: number;
  budget_limit?: number;
  remaining_tokens?: number;
  percent_used: number;
  is_exceeded: boolean;
}

export interface TokenRecordResponse {
  ok: boolean;
  recorded: number;
  total_used: number;
  remaining?: number;
}

// ============================================================================
// Security Types
// ============================================================================

export interface SecurityCheckResponse {
  is_safe: boolean;
  reason?: string;
  suggested_alternative?: string;
}

// ============================================================================
// Transcript Types
// ============================================================================

export interface TranscriptMessage {
  id?: string;
  type?: string;
  role?: string;
  content?: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
}

export interface TranscriptSessionResponse {
  active: boolean;
  session_id?: string;
  message_count?: number;
}

// ============================================================================
// Factory Run Types
// ============================================================================

export interface FactoryRunStatus {
  run_id: string;
  phase: string;
  status: string;
  current_stage?: string | null;
  last_successful_stage?: string | null;
  progress: number;
  roles: Record<string, {
    role: string;
    status: string;
    detail?: string;
    current_task?: string;
    progress: number;
  }>;
  gates: Array<{
    gate_name: string;
    status: string;
    score?: number;
    passed: boolean;
    message: string;
  }>;
  failure?: {
    failure_type: string;
    code: string;
    detail: string;
    phase: string;
    recoverable: boolean;
    suggested_action?: string;
  };
  created_at: string;
  started_at?: string;
  updated_at?: string;
  completed_at?: string;
  summary_md?: string;
  summary_json?: Record<string, unknown> | null;
  artifacts?: FactoryRunArtifact[];
  artifacts_error?: string | null;
}

export interface FactoryRunArtifact {
  name: string;
  path: string;
  size?: number;
}

export interface FactoryRunArtifactsResponse {
  run_id: string;
  artifacts: FactoryRunArtifact[];
  summary_md?: string | null;
  summary_json?: Record<string, unknown> | null;
}

export interface FactoryAuditEvent {
  event_id?: string;
  run_id?: string;
  type: string;
  stage?: string;
  timestamp: string;
  message?: string;
  reason?: string | null;
  result?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface FactoryStartOptions {
  workspace: string;
  start_from?: 'auto' | 'architect' | 'pm' | 'director';
  directive?: string;
  run_director?: boolean;
  director_iterations?: number;
  loop?: boolean;
}

export interface FactoryRunListResponse {
  runs: FactoryRunStatus[];
}

// ============================================================================
// Court System Types
// ============================================================================

export type CourtScenePhase =
  | 'court_audience'
  | 'draft'
  | 'decompose'
  | 'blueprint'
  | 'build'
  | 'review'
  | 'finalize';

export type ActorStatus =
  | 'offline'
  | 'idle'
  | 'thinking'
  | 'executing'
  | 'dispatching'
  | 'reviewing'
  | 'approving'
  | 'blocked'
  | 'success'
  | 'failed';

export type RiskLevel = 'none' | 'low' | 'medium' | 'high' | 'critical';

export interface CourtEvidenceRef {
  path: string;
  channel?: string;
  runId?: string;
  taskId?: string;
  eventId?: string;
}

export interface CourtActorState {
  role_id: string;
  role_name: string;
  status: ActorStatus;
  current_action: string;
  task_id?: string;
  risk_level: RiskLevel;
  evidence_refs: CourtEvidenceRef[];
  metadata: Record<string, unknown>;
  updated_at: number;
}

export interface CourtTopologyNode {
  role_id: string;
  role_name: string;
  parent_id?: string;
  position: [number, number, number];
  department: string;
  level: number;
  is_interactive: boolean;
}

export interface CourtSceneConfig {
  scene_id: string;
  scene_name: string;
  phase: CourtScenePhase;
  description: string;
  camera_position: [number, number, number];
  focus_roles: string[];
  transitions: string[];
}

export interface CourtState {
  phase: CourtScenePhase;
  current_scene: string;
  actors: Record<string, CourtActorState>;
  topology?: CourtTopologyNode[];
  recent_events: CourtActionEvent[];
  updated_at: number;
}

export interface CourtActionEvent {
  action_type: string;
  from_role: string;
  to_role?: string;
  payload: Record<string, unknown>;
  ts: number;
  evidence_refs: CourtEvidenceRef[];
}

export interface CourtTopologyResponse {
  nodes: CourtTopologyNode[];
  count: number;
  total: number;
  scenes: Record<string, CourtSceneConfig>;
}

export interface CourtMappingResponse {
  tech_to_court: Record<string, string>;
  court_roles: string[];
  version: string;
}

// ============================================================================
// Role Chat Types
// ============================================================================

export type DialogueRole = 'pm' | 'architect' | 'director' | 'qa';

export interface ChatStatus {
  ready: boolean;
  error?: string;
  role?: string;
  role_config?: {
    provider_id: string;
    model: string;
    profile?: string;
  };
  provider_type?: string;
  debug?: Record<string, unknown>;
}

export interface ChatMessageRequest {
  message: string;
  context?: Record<string, unknown>;
}

export interface ChatStreamEvent {
  type: 'thinking_chunk' | 'content_chunk' | 'complete' | 'error';
  data?: {
    content?: string;
    response?: string;
    message?: string;
  };
}

// ============================================================================
// LLM Config Types
// ============================================================================

export interface LLMConfigResponse {
  schema_version: number;
  providers: Record<string, ProviderConfig>;
  roles: Record<string, RoleConfig>;
  policies?: {
    required_ready_roles?: string[];
    test_required_suites?: string[];
    role_requirements?: Record<string, RoleRequirement>;
  };
}

export interface ProviderConfig {
  type?: string;
  name?: string;
  command?: string;
  args?: string[];
  codex_exec?: Record<string, unknown>;
  env?: Record<string, string>;
  base_url?: string;
  api_key?: string;
  api_key_ref?: string;
  list_args?: string[];
  tui_args?: string[];
  output_path?: string;
  timeout?: number;
  retries?: number;
  max_retries?: number;
  api_path?: string;
  models_path?: string;
  headers?: Record<string, string>;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  max_context_tokens?: number;
  max_output_tokens?: number;
  model?: string;
  default_model?: string;
  thinking_mode?: boolean;
  streaming?: boolean;
  sdk_params?: Record<string, unknown>;
  request_overrides?: {
    headers?: Record<string, string>;
    queryParams?: Record<string, string>;
    bodyFields?: Record<string, unknown>;
  };
  cli_mode?: 'tui' | 'headless';
  thinking_extraction?: {
    enabled: boolean;
    patterns: string[];
    confidence_threshold: number;
  };
  model_specific?: {
    temperature?: number;
    topP?: number;
    maxTokens?: number;
    frequencyPenalty?: number;
    presencePenalty?: number;
  };
}

export interface RoleConfig {
  provider_id?: string;
  model?: string;
  profile?: string;
}

export interface RoleRequirement {
  requires_thinking?: boolean;
  min_confidence?: number;
  error_message?: string;
}

export interface LLMStatusResponse {
  state: string;
  required_ready_roles: string[];
  blocked_roles: string[];
  unsupported_roles: string[];
  roles: Record<string, {
    provider_id?: string;
    model?: string;
    profile?: string;
    ready?: boolean;
    grade?: string;
    last_run_id?: string | null;
    timestamp?: string | null;
    suites?: Record<string, unknown> | null;
    runtime_supported?: boolean;
  }>;
  providers?: Record<string, {
    ready?: boolean | null;
    grade?: string;
    last_run_id?: string | null;
    timestamp?: string | null;
    suites?: Record<string, unknown> | null;
    model?: string | null;
    role?: string | null;
  }>;
  last_updated: string;
}

// ============================================================================
// File Types
// ============================================================================

export interface FilePayload {
  content: string;
  mtime: string;
}

export interface FileReadOptions {
  tailLines?: number;
}

// ============================================================================
// Memo Types
// ============================================================================

export interface MemoItem {
  path: string;
  name: string;
  mtime?: string;
}

export interface MemoListResponse {
  items: MemoItem[];
  count: number;
}

// ============================================================================
// Settings Types
// ============================================================================

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
  pm_blocked_strategy?: 'skip' | 'manual' | 'degrade_retry' | 'auto';
  pm_blocked_degrade_max_retries?: number;
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

// ============================================================================
// LanceDB Types
// ============================================================================

export interface LanceDbStatus {
  ok: boolean;
  error?: string | null;
  python?: string | null;
  version?: string | null;
}

// ============================================================================
// Snapshot Types
// ============================================================================

export interface WorkspaceStatus {
  initialized: boolean;
  docs_present: boolean;
  agents_present: boolean;
  has_git: boolean;
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

// ============================================================================
// Ollama Types
// ============================================================================

export interface OllamaStopResponse {
  stopped?: string[];
  failed?: Array<{ model: string }>;
}

// ============================================================================
// Health Types
// ============================================================================

export interface HealthCheckResponse {
  timestamp?: string;
  status?: string;
}

// ============================================================================
// Agents Types
// ============================================================================

export interface AgentsFeedbackResponse {
  mtime?: string;
  cleared?: boolean;
}

// ============================================================================
// Director Task Types
// ============================================================================

export interface DirectorQueuedTask {
  id?: string;
  subject?: string;
  description?: string;
  status?: string;
  metadata?: Record<string, unknown>;
}

export interface DirectorTaskPayload {
  subject: string;
  description: string;
  priority: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  timeout_seconds: number;
  metadata: {
    pm_task_id: string;
    pm_task_title: string;
    pm_task_status: string;
    acceptance: string[];
  };
}

// ============================================================================
// Usage Stats Types
// ============================================================================

export interface UsageStats {
  totals: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  calls: number;
  estimated_calls: number;
  by_mode?: Record<string, { total_tokens: number; calls: number }>;
}

export interface LLMObservationEntry {
  kind: string;
  refs?: {
    mode?: string;
  };
  output?: {
    usage?: {
      prompt_tokens?: number;
      completion_tokens?: number;
      total_tokens?: number;
    };
  };
}
