import type { WorkspaceStatus } from "@/app/components/DocsInitDialog";
import type { MemoItem } from "@/app/components/MemoPanel";

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
  close_to_tray?: boolean;
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
  director_execution_mode?: "serial" | "parallel" | string;
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
  verifier_policy?: VerifierPolicySettings;
}

export interface VerifierPolicySettings {
  browser_enabled?: boolean;
  visual_enabled?: boolean;
  multimodal_llm_enabled?: boolean;
  user_scripts_enabled?: boolean;
  domain_verifiers_enabled?: boolean;
  enabled_evidence_modalities?: string[];
  required_evidence_modalities?: string[];
}

export interface BackendStatus {
  running: boolean;
  pid: number | null;
  started_at: number | null;
  mode?: string;
  log_path?: string;
  source?: "handle" | "status_file" | "none" | string;
  status?: Record<string, unknown> | string | null;
  execution_id?: string | null;
  terminal?: boolean;
  ok?: boolean | null;
  exit_code?: number | null;
  error?: string | null;
  contract_path?: string | null;
  contract_exists?: boolean;
  contract_size?: number;
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

export type LlmStatus = import("../components/llm/types").LLMStatus;

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
  stale?: boolean;
  orphaned?: boolean;
  recovery_code?: string;
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
  resident_agi_participation?: ResidentAgiParticipationPayload;
  created_at?: string;
  updated_at?: string;
}

export interface ResidentAgiParticipationPayload {
  enabled?: boolean;
  role_turn_enabled?: boolean;
  manual_role_turn_requested?: boolean;
  automatic_participation_enabled?: boolean;
  configured_enabled?: boolean;
  configured_scopes?: string[];
  scopes?: string[];
  required_role_turn_scopes?: string[];
  configured_participation?: Record<string, boolean>;
  automatic_participation?: Record<string, boolean>;
  participation?: Record<string, boolean>;
  custom_scopes_allowed?: boolean;
  updated_at?: string;
}

export interface ResidentAgiParticipationPolicyPayload {
  schema_version?: string;
  role_id?: string;
  source?: string;
  enabled_default?: boolean;
  custom_scopes_allowed?: boolean;
  scope_semantics?: string;
  participation_flags?: string[];
  available_scopes?: Array<
    Record<string, unknown> & {
      scope_id?: string;
      name?: string;
      category?: string;
      risk_level?: string;
      default_enabled?: boolean;
    }
  >;
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

export interface ResidentTickAutonomyBoundaryPayload {
  schema_version?: string;
  tick_role?: string;
  tick_outputs?: string[];
  goal_proposal_semantics?: string;
  agi_judgement_entrypoint?: string;
  agi_judgement_endpoint?: string;
  execution_impacting_decision_policy?: string;
  sidecar_llm_allowed?: boolean;
}

export interface ResidentRuntimePayload {
  active?: boolean;
  mode?: string;
  last_tick_at?: string;
  tick_count?: number;
  last_error?: string;
  last_summary?: Record<string, unknown> & {
    autonomy_boundary?: ResidentTickAutonomyBoundaryPayload;
  };
  updated_at?: string;
}

export interface ResidentStatusPayload {
  workspace?: string;
  identity?: ResidentIdentityPayload;
  runtime?: ResidentRuntimePayload;
  agenda?: ResidentAgendaPayload;
  counts?: Record<string, number>;
  agi_capability_surface?: ResidentAgiCapabilitySurfacePayload;
  agi_participation_policy?: ResidentAgiParticipationPolicyPayload;
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
  status?: "pending" | "in_progress" | "completed" | "failed" | "blocked";
  progress_percent?: number;
  started_at?: string;
  completed_at?: string;
}

export interface GoalExecutionView {
  goal_id: string;
  stage: "planning" | "coding" | "testing" | "review" | "completed" | "unknown";
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

export interface ResidentAgiCapabilityPayload {
  capability_id?: string;
  name?: string;
  category?: string;
  access?: string;
  purpose?: string;
  contract_ref?: string;
  endpoint?: string;
  risk_level?: string;
  guardrails?: string[];
  evidence_refs?: string[];
}

export interface ResidentAgiHardcodedRepairStrategyPayload {
  source_tool?: string;
  language?: string;
  phase?: string;
  concern?: string;
  risk_level?: string;
  registered?: boolean;
}

export interface ResidentAgiHardcodedRepairStrategyCatalogPayload {
  schema_version?: string;
  source?: string;
  access?: string;
  owner_cell?: string;
  execution_boundary?: string;
  chain?: string;
  unknown_source_tool_policy?: string;
  agi_execution_authority?: boolean;
  director_tool_execution_required?: boolean;
  items?: ResidentAgiHardcodedRepairStrategyPayload[];
  summary?: {
    total?: number;
    returned?: number;
    by_language?: Record<string, number>;
    by_phase?: Record<string, number>;
    by_concern?: Record<string, number>;
    by_risk?: Record<string, number>;
  };
}

export interface ResidentAgiDecisionBoundaryPayload {
  boundary_id?: string;
  name?: string;
  authority?: string;
  platform_hard_rule?: string;
  agi_decision_scope?: string;
  evidence_required?: string[];
  escalation?: string;
  contract_refs?: string[];
}

export interface ResidentAgiDecisionCapabilityPayload {
  decision_id?: string;
  name?: string;
  owner?: string;
  decision_scope?: string;
  risk_level?: string;
  required_evidence_interfaces?: string[];
  optional_evidence_interfaces?: string[];
  candidate_actions?: string[];
  hard_constraints?: string[];
  escalation?: string;
  output_contract?: string;
  contract_refs?: string[];
  llm_decision_required?: boolean;
  platform_enforced?: boolean;
}

export interface ResidentAgiDecisionCapabilityRegistryPayload {
  schema_version?: string;
  role_id?: string;
  runtime_foundation?: string;
  platform_owned_decisions?: string[];
  agi_owned_decisions?: string[];
  governed_execution_decisions?: string[];
  evidence_interface_ids?: string[];
  candidate_actions?: string[];
  counts?: {
    decisions?: number;
    platform_owned?: number;
    agi_owned?: number;
    governed_execution?: number;
    evidence_interfaces?: number;
  };
  decision_policy?: Record<string, string>;
}

export interface ResidentAgiEvidenceInterfaceContractPayload {
  schema_version?: string;
  role_id?: string;
  source?: string;
  coverage_complete?: boolean;
  supported_interface_ids?: string[];
  declared_interface_ids?: string[];
  required_interface_ids?: string[];
  optional_interface_ids?: string[];
  missing_interface_ids?: string[];
  missing_required_interface_ids?: string[];
  missing_optional_interface_ids?: string[];
  interfaces?: Array<{
    interface_id?: string;
    status?: string;
    required_by_decisions?: string[];
    optional_by_decisions?: string[];
    access?: string;
    category?: string;
    contract_ref?: string;
    risk_level?: string;
  }>;
  decision_policy?: Record<string, string>;
}

export interface ResidentAgiAuthorityMatrixPayload {
  schema_version?: string;
  runtime_foundation?: string;
  role_id?: string;
  chain?: string;
  chain_required?: boolean;
  platform_enforced?: boolean;
  llm_decision_required?: boolean;
  platform_hard_rules?: string[];
  agi_recommendation_boundaries?: string[];
  governed_execution_boundaries?: string[];
  read_only_capabilities?: string[];
  governed_operation_capabilities?: string[];
  high_risk_capabilities?: string[];
  canonical_contracts?: string[];
  counts?: {
    platform_hard_rules?: number;
    agi_recommendations?: number;
    governed_execution_boundaries?: number;
    read_only_capabilities?: number;
    governed_operation_capabilities?: number;
    high_risk_capabilities?: number;
    canonical_contracts?: number;
  };
  decision_policy?: Record<string, string>;
}

export interface ResidentAgiCapabilitySurfacePayload {
  schema_version?: string;
  decision_boundary_schema?: string;
  authority_matrix_schema?: string;
  role_id?: string;
  runtime_foundation?: string;
  implementation_cell?: string;
  product_role?: string;
  unattended_factory_role?: string;
  categories?: string[];
  items?: ResidentAgiCapabilityPayload[];
  decision_boundaries?: ResidentAgiDecisionBoundaryPayload[];
  decision_capability_schema?: string;
  decision_capabilities?: ResidentAgiDecisionCapabilityPayload[];
  decision_capability_registry?: ResidentAgiDecisionCapabilityRegistryPayload;
  evidence_interface_contract_schema?: string;
  evidence_interface_contract?: ResidentAgiEvidenceInterfaceContractPayload;
  participation_policy?: ResidentAgiParticipationPolicyPayload;
  hardcoded_repair_strategy_catalog?: ResidentAgiHardcodedRepairStrategyCatalogPayload;
  authority_matrix?: ResidentAgiAuthorityMatrixPayload;
  count?: number;
}

export interface ResidentAgiRoleRegistryPayload {
  schema_version?: string;
  source?: string;
  dialogue_roles?: string[];
  adapter_roles?: string[];
  required_roles?: string[];
  missing_required_roles?: string[];
  resident_agi_available?: boolean;
}

export interface ResidentAgiDecisionProfilePayload {
  schema_version?: string;
  role_id?: string;
  runtime_foundation?: string;
  role_turn_allowed?: boolean;
  downstream_precheck?: string;
  recommended_verdict?: string;
  recommended_next_action?: string;
  candidate_actions?: string[];
  required_constraints?: string[];
  required_evidence?: string[];
  evidence_interface_recommendations?: Array<{
    capability_id?: string;
    name?: string;
    category?: string;
    contract_ref?: string;
    access?: string;
    risk_level?: string;
    priority?: number;
    recommended_now?: boolean;
    reason?: string;
    evidence_refs?: string[];
  }>;
  decision_capability_registry?: ResidentAgiDecisionCapabilityRegistryPayload;
  decision_capability_ids?: string[];
  contract_refs?: string[];
  authority_policy?: Record<string, string>;
  platform_permission_counts?: {
    read_only?: number;
    governed_operations?: number;
    high_risk?: number;
  };
  gate_refs?: Record<string, string>;
  llm_decision_required?: boolean;
  llm_override_allowed?: boolean;
  audit_pack_schema?: string;
}

export interface ResidentAgiRuntimeContractGatePayload {
  schema_version?: string;
  status?: string;
  passed?: boolean;
  required?: boolean;
  reason?: string;
  checks?: Array<{
    check_id?: string;
    passed?: boolean;
    detail?: string;
  }>;
  failed_check_ids?: string[];
}

export interface ResidentAgiDirectorRepairContractPayload {
  schema_version?: string;
  owner_cell?: string;
  source?: string;
  catalog_schema?: string;
  profile_summary_schema?: string;
  unknown_source_tool_policy?: string;
  execution_boundary?: string;
  chain?: string;
  agi_advisory?: {
    active?: boolean;
    authoritative?: boolean;
    writes_allowed?: boolean;
  };
  agi_execution_authority?: boolean;
  director_tool_execution_required?: boolean;
  strategy_count?: number;
  summary?: Record<string, unknown>;
}

export interface ResidentAgiAuditPackPayload {
  schema_version?: string;
  workspace?: string;
  role_id?: string;
  runtime_foundation?: string;
  truth_sources?: string[];
  role_registry?: ResidentAgiRoleRegistryPayload;
  runtime_summary?: Record<string, unknown>;
  counts?: Record<string, number>;
  capability_surface?: ResidentAgiCapabilitySurfacePayload;
  autonomy_boundary?: ResidentTickAutonomyBoundaryPayload;
  authority_matrix?: ResidentAgiAuthorityMatrixPayload;
  director_repair_contract?: ResidentAgiDirectorRepairContractPayload;
  boundary_summary?: {
    schema?: string;
    counts_by_authority?: Record<string, number>;
    boundary_ids?: string[];
  };
  hard_rule_gate?: {
    schema_version?: string;
    status?: string;
    checks?: Array<{
      check_id?: string;
      passed?: boolean;
      detail?: string;
    }>;
    failed_check_ids?: string[];
    platform_enforced?: boolean;
    llm_override_allowed?: boolean;
  };
  run_ledger_summary?: {
    schema_version?: string;
    source?: string;
    available?: boolean;
    ok?: boolean;
    status?: string;
    projected?: number;
    total?: number;
    failed?: number;
    missing?: number;
    detail?: string;
    evidence_policy?: Record<string, unknown>;
    evidence_modalities?: Record<string, unknown>;
  };
  evidence_gate?: {
    schema_version?: string;
    status?: string;
    recommended_verdict?: string;
    reason?: string;
    run_ledger_available?: boolean;
    run_ledger_ok?: boolean;
    context_snapshot_ref_count?: number;
    platform_enforced?: boolean;
    llm_decision_required?: boolean;
  };
  recent_decisions?: ResidentDecisionPayload[];
  evidence_refs?: string[];
  execution_constraints?: string[];
  decision_endpoint?: string;
  decision_profile?: ResidentAgiDecisionProfilePayload;
}

export interface ResidentAgiEvidenceInterfacePayload {
  interface_id?: string;
  capability?: ResidentAgiCapabilityPayload;
  name?: string;
  category?: string;
  access?: string;
  contract_ref?: string;
  risk_level?: string;
  endpoint?: string;
  available?: boolean;
  callable?: boolean;
  status?: string;
  source?: string;
  summary?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  gaps?: string[];
  recommended_next_action?: string;
}

export interface ResidentAgiEvidenceInterfacesPayload {
  schema_version?: string;
  workspace?: string;
  decision_type?: string;
  run_id?: string;
  task_id?: string;
  selected_decision_capability?: ResidentAgiDecisionCapabilityPayload;
  required_evidence_interfaces?: string[];
  optional_evidence_interfaces?: string[];
  requested_interface_ids?: string[];
  interfaces?: ResidentAgiEvidenceInterfacePayload[];
  summary?: {
    total?: number;
    available?: number;
    metadata_only?: number;
    needs_public_facade?: number;
    governed_execute_only?: number;
    unavailable?: number;
    empty?: number;
    unknown_interface?: number;
    missing_required_interface_ids?: string[];
  };
  audit_pack_ref?: {
    schema_version?: string;
    evidence_gate_status?: string;
    hard_rule_gate_status?: string;
  };
}

export interface ResidentAgiDecisionTurnRequest {
  workspace?: string;
  decision_type?: string;
  objective: string;
  run_id?: string;
  task_id?: string;
  goal_id?: string;
  evidence?: Record<string, unknown>;
  constraints?: string[];
  candidate_actions?: string[];
  context_refs?: string[];
  evidence_refs?: string[];
  confidence?: number;
  include_audit_pack?: boolean;
  audit_pack_decision_limit?: number;
}

export interface ResidentAgiDecisionHandoffPayload {
  schema_version?: string;
  source_role?: string;
  decision_type?: string;
  decision_capability_id?: string;
  handoff_status?: string;
  target_roles?: string[];
  allowed_actions?: string[];
  blocked_actions?: string[];
  downstream_allowed?: boolean;
  reason?: string;
  evidence_refs?: string[];
  context_refs?: string[];
  gate_statuses?: Record<string, unknown>;
  required_chain?: string;
  advisory_only?: boolean;
  agi_execution_authority?: boolean;
}

export interface ResidentAgiDecisionTurnResponse {
  ok: boolean;
  workspace?: string;
  decision?: Record<string, unknown>;
  recorded_decision?: ResidentDecisionPayload;
  role_result?: Record<string, unknown>;
  audit_pack?: ResidentAgiAuditPackPayload | null;
  resident_agi_participation?: ResidentAgiParticipationPayload;
  decision_handoff?: ResidentAgiDecisionHandoffPayload;
  runtime_contract_gate?: ResidentAgiRuntimeContractGatePayload;
  error?: string | null;
}

export interface ResidentAgiHandoffInboxItemPayload {
  schema_version?: string;
  workspace?: string;
  decision_id?: string;
  timestamp?: string;
  run_id?: string;
  task_id?: string;
  goal_id?: string;
  actor?: string;
  stage?: string;
  summary?: string;
  verdict?: string;
  evidence_refs?: string[];
  context_refs?: string[];
  handoff?: ResidentAgiDecisionHandoffPayload;
}

export interface ResidentAgiHandoffInboxPayload {
  schema_version?: string;
  workspace?: string;
  source?: string;
  role_id?: string;
  target_role?: string;
  handoff_status?: string;
  items?: ResidentAgiHandoffInboxItemPayload[];
  count?: number;
  summary?: {
    total?: number;
    by_status?: Record<string, number>;
    by_target_role?: Record<string, number>;
    advisory_only?: boolean;
    agi_execution_authority?: boolean;
    required_chain?: string;
  };
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
  | "status"
  | "dialogue_event"
  | "runtime_event"
  | "llm_stream"
  | "process_stream"
  | "file_edit"
  | "task_progress"
  | "task_trace"
  | "snapshot"
  | "line";
