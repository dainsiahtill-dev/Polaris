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

// ============================================================================
// V2 P1 Management Routes Types
// ============================================================================

export interface PmTaskItem {
  id: string;
  subject: string;
  status: string;
  priority?: string;
  created_at?: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
}

export interface PmTaskListResponse {
  items: PmTaskItem[];
  total: number;
}

export interface PmTaskDetailResponse extends PmTaskItem {
  description?: string;
  acceptance?: string[];
  assignee?: string;
  due_date?: string | null;
  tags?: string[];
  parent_id?: string | null;
  subtasks?: PmTaskItem[];
}

export interface PmCreateTaskRequest {
  subject: string;
  description?: string;
  priority?: string;
  status?: string;
  acceptance?: string[];
  assignee?: string;
  due_date?: string | null;
  tags?: string[];
  parent_id?: string | null;
}

export interface PmRequirementItem {
  id: string;
  title: string;
  status: string;
  priority?: string;
  created_at?: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
}

export interface PmRequirementListResponse {
  items: PmRequirementItem[];
  total: number;
}

export interface PmRequirementDetailResponse extends PmRequirementItem {
  description?: string;
  acceptance_criteria?: string[];
  source?: string;
  tags?: string[];
  related_task_ids?: string[];
}

export interface DocsInitDialogueRequest {
  message: string;
  context?: Record<string, unknown>;
}

export interface DocsInitDialogueResponse {
  ok: boolean;
  response: string;
  suggestions?: string[];
}

export interface DocsInitSuggestRequest {
  topic?: string;
  context?: Record<string, unknown>;
}

export interface DocsInitSuggestResponse {
  ok: boolean;
  suggestions: Array<{
    title: string;
    description: string;
    category?: string;
  }>;
}

export interface DocsInitPreviewRequest {
  selections?: string[];
  context?: Record<string, unknown>;
}

export interface DocsInitPreviewResponse {
  ok: boolean;
  preview: string;
  files?: Array<{
    path: string;
    content: string;
  }>;
}

export interface DocsInitApplyRequest {
  selections?: string[];
  context?: Record<string, unknown>;
}

export interface DocsInitApplyResponse {
  ok: boolean;
  applied: string[];
  message?: string;
}

export interface LLMConfigMigrateRequest {
  target_version?: number;
  backup?: boolean;
}

export interface LLMConfigMigrateResponse {
  ok: boolean;
  migrated: boolean;
  previous_version?: number;
  current_version?: number;
  message?: string;
}

export interface LLMProviderItem {
  id: string;
  name: string;
  type?: string;
  ready?: boolean;
  models?: string[];
  config?: Record<string, unknown>;
}

export interface LLMProviderListResponse {
  items: LLMProviderItem[];
  total: number;
}

export interface SettingsV2Response {
  settings: Record<string, unknown>;
  version?: number;
  updated_at?: string;
}

export interface SettingsV2UpdateRequest {
  settings: Record<string, unknown>;
}

export interface AgentsApplyRequest {
  draft_path?: string;
  auto_generate?: boolean;
  context?: Record<string, unknown>;
}

export interface AgentsApplyResponse {
  ok: boolean;
  applied: boolean;
  agents_path?: string;
  message?: string;
}

export interface AgentsFeedbackRequest {
  text: string;
  category?: string;
  context?: Record<string, unknown>;
}

export interface AgentsFeedbackResponse {
  ok: boolean;
  mtime?: string;
  cleared?: boolean;
  message?: string;
}

export interface RoleCacheStatsResponse {
  ok: boolean;
  hits?: number;
  misses?: number;
  size?: number;
  entries?: number;
  last_cleared?: string | null;
  by_role?: Record<string, {
    hits?: number;
    misses?: number;
    entries?: number;
  }>;
}

export interface RoleCacheClearResponse {
  ok: boolean;
  cleared: boolean;
  previous_size?: number;
  message?: string;
}

export interface RoleChatRolesResponse {
  ok: boolean;
  roles: Array<{
    id: string;
    name: string;
    description?: string;
    enabled?: boolean;
  }>;
}

// ============================================================================
// V2 P0 Missing Routes Types
// ============================================================================

export type UnifiedRole = 'pm' | 'architect' | 'chief_engineer' | 'director' | 'qa' | 'scout';

export interface RoleChatRequest {
  message: string;
  context?: Record<string, unknown>;
}

export interface RoleChatResponse {
  ok: boolean;
  response: string;
  thinking?: string;
  role: string;
  model?: string;
  provider?: string;
}

export interface RoleChatStatusResponse {
  ready: boolean;
  configured?: boolean;
  error?: string;
  role?: string;
  role_config?: {
    provider_id: string;
    model: string;
    profile?: string;
  };
  provider_type?: string;
  llm_test_ready?: boolean;
  debug?: Record<string, unknown>;
}

export interface SessionMessageRequest {
  role: string;
  content: string;
  thinking?: string;
  meta?: Record<string, unknown>;
}

export interface SessionMessageResponse {
  ok: boolean;
  session?: Record<string, unknown>;
}

export interface SessionMemoryItem {
  id?: string;
  kind?: string;
  entity?: string;
  content?: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
}

export interface SessionMemoryResponse {
  ok: boolean;
  session_id: string;
  query?: string;
  kind?: string;
  entity?: string;
  total: number;
  items: SessionMemoryItem[];
}

export interface StreamChatRequest {
  role?: string;
  message: string;
  provider_id?: string;
  model?: string;
  context?: Record<string, unknown>;
  options?: Record<string, unknown>;
}

export interface StreamChatResponse {
  status: string;
  message: string;
}

export interface ConversationV2 {
  id: string;
  title?: string;
  role: string;
  workspace?: string;
  context_config?: Record<string, unknown>;
  message_count: number;
  created_at: string;
  updated_at: string;
  messages?: ConversationMessageV2[];
}

export interface ConversationMessageV2 {
  id: string;
  conversation_id: string;
  sequence: number;
  role: string;
  content: string;
  thinking?: string;
  meta?: Record<string, unknown>;
  created_at: string;
}

export interface ConversationListResponseV2 {
  conversations: ConversationV2[];
  total: number;
}

export interface CreateConversationRequestV2 {
  title?: string;
  role: string;
  workspace?: string;
  context_config?: Record<string, unknown>;
  initial_message?: {
    role: string;
    content: string;
    thinking?: string;
    meta?: Record<string, unknown>;
  };
}

export interface AddConversationMessageRequestV2 {
  role: string;
  content: string;
  thinking?: string;
  meta?: Record<string, unknown>;
}

// ============================================================================
// V2 P2 Diagnostic Routes Types
// ============================================================================

export interface HealthV2Response {
  status?: string;
  timestamp?: string;
  version?: string;
}

export interface ReadyV2Response {
  ready: boolean;
  checks?: Record<string, boolean>;
  timestamp?: string;
}

export interface LiveV2Response {
  alive: boolean;
  timestamp?: string;
}

export interface StateSnapshotV2Response {
  snapshot: Record<string, unknown>;
  timestamp?: string;
}

export interface ShutdownV2Response {
  ok: boolean;
  message?: string;
}

export interface LogsQueryV2Response {
  logs: Array<{
    timestamp?: string;
    level?: string;
    channel?: string;
    message?: string;
    metadata?: Record<string, unknown>;
  }>;
  total?: number;
}

export interface LogUserActionV2Request {
  action: string;
  category?: string;
  metadata?: Record<string, unknown>;
}

export interface LogUserActionV2Response {
  ok: boolean;
  logged?: boolean;
}

export interface LogChannelsV2Response {
  channels: string[];
}

export interface LanceDbStatusV2Response {
  ok: boolean;
  error?: string | null;
  python?: string | null;
  version?: string | null;
}

export interface MemoListV2Response {
  items: Array<{ path: string; name: string; mtime?: string }>;
  count: number;
}

export interface OllamaModelsV2Request {
  host?: string;
}

export interface OllamaModelsV2Response {
  models: Array<{ name: string; size?: number; parameter_size?: string; digest?: string }>;
}

export interface OllamaStopV2Response {
  stopped?: string[];
  failed?: Array<{ model: string }>;
}

export interface MemoryStateV2Response {
  state: Record<string, unknown>;
  count?: number;
  timestamp?: string;
}

export interface DeleteMemoryV2Response {
  ok: boolean;
  deleted?: boolean;
  memory_id?: string;
}

export interface RoleLlmEventsV2Response {
  events: Array<{
    event_id?: string;
    role?: string;
    timestamp?: string;
    type?: string;
    model?: string;
    provider?: string;
    tokens?: number;
    duration_ms?: number;
    metadata?: Record<string, unknown>;
  }>;
  total?: number;
}

export interface AllLlmEventsV2Response {
  events: Array<{
    event_id?: string;
    role?: string;
    timestamp?: string;
    type?: string;
    model?: string;
    provider?: string;
    tokens?: number;
    duration_ms?: number;
    metadata?: Record<string, unknown>;
  }>;
  total?: number;
}

export interface FactoryRunEventsV2Response {
  events: Array<{
    event_id?: string;
    run_id?: string;
    timestamp?: string;
    type?: string;
    stage?: string;
    message?: string;
    metadata?: Record<string, unknown>;
  }>;
  total?: number;
}

export interface FactoryRunAuditBundleV2Response {
  run_id: string;
  bundle: Record<string, unknown>;
  timestamp?: string;
}

export interface RuntimeMigrationStatusV2Response {
  status: string;
  current_version?: number;
  target_version?: number;
  pending_migrations?: string[];
  timestamp?: string;
}

// ============================================================================
// V2 P3 Advanced Routes Types
// ============================================================================

// --- Court ---
export interface CourtTopologyResponse {
  nodes: CourtTopologyNode[];
  count: number;
  total: number;
  scenes: Record<string, CourtSceneConfig>;
}

export interface CourtStateResponse {
  phase: CourtScenePhase;
  current_scene: string;
  actors: Record<string, CourtActorState>;
  topology?: CourtTopologyNode[];
  recent_events: CourtActionEvent[];
  updated_at: number;
}

export interface CourtActorResponse {
  actor: CourtActorState;
}

export interface CourtSceneResponse {
  scene: CourtSceneConfig;
}

// --- Vision ---
export interface VisionStatusResponse {
  ready: boolean;
  provider?: string;
  model?: string;
  error?: string;
}

export interface VisionAnalyzeRequest {
  image_url?: string;
  image_base64?: string;
  prompt?: string;
  context?: Record<string, unknown>;
}

export interface VisionAnalyzeResponse {
  ok: boolean;
  result: string;
  model?: string;
  provider?: string;
}

// --- Scheduler ---
export interface SchedulerStatusResponse {
  running: boolean;
  schedule?: string;
  next_run?: string;
  last_run?: string;
  error?: string;
}

export interface SchedulerStartRequest {
  schedule?: string;
  immediate?: boolean;
}

export interface SchedulerStartResponse {
  ok: boolean;
  running: boolean;
  message?: string;
}

export interface SchedulerStopResponse {
  ok: boolean;
  running: boolean;
  message?: string;
}

// --- Code Map ---
export interface CodeMapResponse {
  ok: boolean;
  map?: Record<string, unknown>;
  symbols?: Array<{
    name: string;
    kind: string;
    path: string;
    line?: number;
  }>;
  files?: string[];
}

export interface CodeIndexRequest {
  paths?: string[];
  force?: boolean;
}

export interface CodeIndexResponse {
  ok: boolean;
  indexed: number;
  message?: string;
}

export interface CodeSearchRequest {
  query: string;
  limit?: number;
  kind?: string;
}

export interface CodeSearchResult {
  name: string;
  kind: string;
  path: string;
  line?: number;
  score?: number;
  snippet?: string;
}

export interface CodeSearchResponse {
  ok: boolean;
  results: CodeSearchResult[];
  total: number;
}

// --- MCP ---
export interface McpStatusResponse {
  ready: boolean;
  servers?: Array<{
    name: string;
    status: string;
    tools?: string[];
  }>;
  error?: string;
}

// --- Director Capabilities ---
export interface DirectorCapabilitiesResponse {
  ok: boolean;
  capabilities: string[];
  roles: string[];
  features: Record<string, boolean>;
}

// --- Interview ---
export interface InterviewAskRequest {
  question: string;
  context?: Record<string, unknown>;
  role?: string;
}

export interface InterviewAskResponse {
  ok: boolean;
  answer: string;
  follow_up?: string[];
  role?: string;
  model?: string;
}

export interface InterviewSaveRequest {
  session_id?: string;
  answers: Array<{
    question: string;
    answer: string;
  }>;
}

export interface InterviewSaveResponse {
  ok: boolean;
  session_id: string;
  saved: number;
}

export interface InterviewCancelRequest {
  session_id?: string;
}

export interface InterviewCancelResponse {
  ok: boolean;
  cancelled: boolean;
}

export interface InterviewStreamRequest {
  question: string;
  context?: Record<string, unknown>;
  role?: string;
}

// --- LLM Test ---
export interface LLMTestReportResponse {
  ok: boolean;
  report?: {
    passed: number;
    failed: number;
    skipped: number;
    total: number;
    suites?: string[];
  };
  last_run?: string;
}

export interface LLMTestStartRequest {
  suites?: string[];
  roles?: string[];
}

export interface LLMTestStartResponse {
  ok: boolean;
  test_run_id: string;
  status: string;
}

export interface LLMTestRunStatusResponse {
  ok: boolean;
  test_run_id: string;
  status: string;
  progress: number;
  passed: number;
  failed: number;
  total: number;
}

export interface LLMTestTranscriptResponse {
  ok: boolean;
  test_run_id: string;
  entries: Array<{
    timestamp: string;
    role: string;
    prompt: string;
    response: string;
    passed: boolean;
    error?: string;
  }>;
}

// --- Permissions ---
export interface PermissionCheckRequest {
  action: string;
  resource: string;
  context?: Record<string, unknown>;
}

export interface PermissionCheckResponse {
  allowed: boolean;
  reason?: string;
  policy_id?: string;
}

export interface EffectivePermissionsResponse {
  permissions: string[];
  roles: string[];
  context?: Record<string, unknown>;
}

export interface PermissionRoleItem {
  id: string;
  name: string;
  permissions: string[];
  inherited?: string[];
}

export interface PermissionRolesResponse {
  roles: PermissionRoleItem[];
  total: number;
}

export interface PermissionAssignRequest {
  subject: string;
  role: string;
  resource?: string;
}

export interface PermissionAssignResponse {
  ok: boolean;
  assigned: boolean;
  previous_role?: string;
}

export interface PermissionPolicyItem {
  id: string;
  name: string;
  actions: string[];
  resources: string[];
  effect: 'allow' | 'deny';
}

export interface PermissionPoliciesResponse {
  policies: PermissionPolicyItem[];
  total: number;
}

// --- Files V2 ---
export interface FileReadV2Response {
  ok: boolean;
  content: string;
  path: string;
  mtime?: string;
  size?: number;
}

// --- Runtime V2 ---
export interface RuntimeClearResponse {
  ok: boolean;
  cleared: string[];
}

export interface RuntimeResetTasksResponse {
  ok: boolean;
  reset: number;
}

// --- LLM Runtime Status ---
export interface LLMRuntimeStatusResponse {
  ok: boolean;
  overall: string;
  roles: Record<string, {
    status: string;
    provider?: string;
    model?: string;
    ready?: boolean;
    last_error?: string;
  }>;
}

export interface RoleRuntimeStatusResponse {
  ok: boolean;
  role_id: string;
  status: string;
  provider?: string;
  model?: string;
  ready?: boolean;
  last_error?: string;
  timestamp?: string;
}
