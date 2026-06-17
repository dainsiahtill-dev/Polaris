import { apiDelete, apiGet, apiPost } from './apiClient';
import type { ApiResult } from './api.types';
import type {
  ChiefEngineerBlueprintDetailV1,
  ChiefEngineerBlueprintListV1,
} from '@/types/roleContracts';

export type ChiefEngineerBlueprintListResponse = ChiefEngineerBlueprintListV1;
export type ChiefEngineerBlueprintDetailResponse = ChiefEngineerBlueprintDetailV1;

export interface ChiefEngineerDiagnosticsWorkspaceStatus {
  ok: boolean;
  status: string;
  workspace: string;
  exists: boolean;
  error: string | null;
}

export interface ChiefEngineerDiagnosticsLLMStatus {
  ok: boolean;
  state: string;
  role: 'chief_engineer';
  blocked_roles: string[];
  unsupported_roles: string[];
  required_ready_roles: string[];
  provider_id: string | null;
  model: string | null;
  error: string | null;
  details: Record<string, unknown>;
}

export interface ChiefEngineerDiagnosticsBlueprintStatus {
  ok: boolean;
  status: string;
  source: string;
  plan_status: string;
  plan_path: string | null;
  plan_error: string | null;
  total: number;
  loadable: number;
  invalid_payloads: number;
  planned_tasks: number;
  covered_tasks: number;
  missing_task_ids: string[];
  director_handoff_ready: boolean;
  latest_updated_at: string | null;
  error: string | null;
}

export interface ChiefEngineerDiagnosticsResponse {
  ok: boolean;
  can_handoff?: boolean;
  role: 'chief_engineer';
  generated_at: string;
  workspace: ChiefEngineerDiagnosticsWorkspaceStatus;
  llm: ChiefEngineerDiagnosticsLLMStatus;
  blueprints: ChiefEngineerDiagnosticsBlueprintStatus;
  can_generate?: boolean;
  issues: string[];
  generate_blockers?: string[];
  handoff_blockers?: string[];
}

export interface GenerateChiefEngineerBlueprintPayload {
  task_id: string;
  objective: string;
  run_id?: string | null;
  constraints?: Record<string, unknown>;
  context?: Record<string, unknown>;
}

export interface BulkGenerateChiefEngineerBlueprintPayload {
  tasks: GenerateChiefEngineerBlueprintPayload[];
  stop_on_error?: boolean;
}

export interface ChiefEngineerTaskBlueprintResultResponse {
  ok: boolean;
  task_id: string;
  workspace: string;
  status: string;
  blueprint_id: string | null;
  blueprint_path: string | null;
  source: string;
  summary: string;
  recommendations: string[];
  risks: string[];
  blueprint: Record<string, unknown>;
}

export interface ChiefEngineerBulkBlueprintError {
  task_id: string;
  code: string;
  message: string;
}

export interface ChiefEngineerBulkGenerateBlueprintResponse {
  ok: boolean;
  workspace: string;
  total: number;
  generated: number;
  failed: number;
  results: ChiefEngineerTaskBlueprintResultResponse[];
  errors: ChiefEngineerBulkBlueprintError[];
}

export interface ChiefEngineerBlueprintDeleteResponse {
  ok: boolean;
  blueprint_id: string;
  deleted: boolean;
  source: string;
}

function workspaceQuerySuffix(workspace = ''): string {
  return workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
}

function appendWorkspaceQuery(path: string, workspace = ''): string {
  if (!workspace) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}workspace=${encodeURIComponent(workspace)}`;
}

export async function getChiefEngineerDiagnostics(
  workspace = '',
): Promise<ApiResult<ChiefEngineerDiagnosticsResponse>> {
  return apiGet<ChiefEngineerDiagnosticsResponse>(
    `/v2/chief-engineer/diagnostics${workspaceQuerySuffix(workspace)}`,
    'Failed to load Chief Engineer diagnostics',
  );
}

export async function generateChiefEngineerBlueprint(
  payload: GenerateChiefEngineerBlueprintPayload,
  workspace = '',
): Promise<ApiResult<ChiefEngineerTaskBlueprintResultResponse>> {
  return apiPost<ChiefEngineerTaskBlueprintResultResponse>(
    `/v2/chief-engineer/blueprints${workspaceQuerySuffix(workspace)}`,
    payload,
    'Failed to generate Chief Engineer blueprint',
  );
}

export async function bulkGenerateChiefEngineerBlueprints(
  payload: BulkGenerateChiefEngineerBlueprintPayload,
  workspace = '',
): Promise<ApiResult<ChiefEngineerBulkGenerateBlueprintResponse>> {
  return apiPost<ChiefEngineerBulkGenerateBlueprintResponse>(
    `/v2/chief-engineer/blueprints/bulk${workspaceQuerySuffix(workspace)}`,
    payload,
    'Failed to bulk generate Chief Engineer blueprints',
  );
}

export async function getChiefEngineerBlueprintStatus(
  taskId: string,
  runId?: string | null,
  workspace = '',
): Promise<ApiResult<ChiefEngineerTaskBlueprintResultResponse>> {
  const query = new URLSearchParams({ task_id: taskId });
  if (runId) {
    query.set('run_id', runId);
  }
  if (workspace) {
    query.set('workspace', workspace);
  }
  return apiGet<ChiefEngineerTaskBlueprintResultResponse>(
    `/v2/chief-engineer/blueprints/status?${query.toString()}`,
    'Failed to load Chief Engineer blueprint status',
  );
}

export async function listChiefEngineerBlueprints(
  workspace = '',
): Promise<ApiResult<ChiefEngineerBlueprintListResponse>> {
  return apiGet<ChiefEngineerBlueprintListResponse>(
    `/v2/chief-engineer/blueprints${workspaceQuerySuffix(workspace)}`,
    'Failed to list Chief Engineer blueprints',
  );
}

export async function getChiefEngineerBlueprint(
  blueprintId: string,
  workspace = '',
): Promise<ApiResult<ChiefEngineerBlueprintDetailResponse>> {
  return apiGet<ChiefEngineerBlueprintDetailResponse>(
    appendWorkspaceQuery(`/v2/chief-engineer/blueprints/${encodeURIComponent(blueprintId)}`, workspace),
    'Failed to load Chief Engineer blueprint',
  );
}

export async function deleteChiefEngineerBlueprint(
  blueprintId: string,
  workspace = '',
): Promise<ApiResult<ChiefEngineerBlueprintDeleteResponse>> {
  return apiDelete<ChiefEngineerBlueprintDeleteResponse>(
    appendWorkspaceQuery(`/v2/chief-engineer/blueprints/${encodeURIComponent(blueprintId)}`, workspace),
    'Failed to delete Chief Engineer blueprint',
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Tier-1 governance surface: Risk Register + Tech-Debt Ledger
// Mirrors the backend routes in delivery/http/v2/chief_engineer.py.
// ═══════════════════════════════════════════════════════════════════════

export type RiskSeverity = 'low' | 'medium' | 'high' | 'critical' | 'blocker';
export type RiskStatus = 'open' | 'mitigating' | 'accepted' | 'resolved' | 'reverted';
export type TechDebtSeverity = 'trivial' | 'minor' | 'major' | 'severe' | 'fatal';
export type TechDebtStatus = 'registered' | 'acknowledged' | 'scheduled' | 'paid' | 'wontfix';

export interface RiskRecord {
  risk_id: string;
  task_id: string;
  title: string;
  severity: RiskSeverity;
  owner: string;
  mitigation: string;
  status: RiskStatus;
  detected_at: string;
  links: string[];
  supersedes: string | null;
  history: Record<string, string>[];
}

export interface TechDebtRecord {
  debt_id: string;
  title: string;
  description: string;
  severity: TechDebtSeverity;
  surface: string;
  owner: string;
  evidence: string[];
  status: TechDebtStatus;
  registered_at: string;
  history: Record<string, string>[];
}

export interface RegisterRiskPayload {
  task_id: string;
  title: string;
  severity: RiskSeverity;
  owner: string;
  mitigation?: string;
  links?: string[];
  supersedes?: string | null;
}

export interface RegisterTechDebtPayload {
  title: string;
  description?: string;
  severity: TechDebtSeverity;
  surface: string;
  owner: string;
  evidence?: string[];
}

export interface RiskRegisterResponse {
  ok: boolean;
  workspace: string;
  risk: RiskRecord;
}

export interface RiskListResponse {
  ok: boolean;
  workspace: string;
  total: number;
  risks: RiskRecord[];
  summary: Record<string, unknown>;
}

export interface TechDebtRegisterResponse {
  ok: boolean;
  workspace: string;
  tech_debt: TechDebtRecord;
}

export interface TechDebtListResponse {
  ok: boolean;
  workspace: string;
  total: number;
  tech_debt: TechDebtRecord[];
  summary: Record<string, unknown>;
}

export interface RiskFilters {
  taskId?: string;
  severity?: RiskSeverity;
  status?: RiskStatus;
}

export interface TechDebtFilters {
  severity?: TechDebtSeverity;
  surface?: string;
  status?: TechDebtStatus;
}

export async function registerChiefEngineerRisk(
  payload: RegisterRiskPayload,
  workspace = '',
): Promise<ApiResult<RiskRegisterResponse>> {
  return apiPost<RiskRegisterResponse>(
    `/v2/chief-engineer/risks${workspaceQuerySuffix(workspace)}`,
    payload,
    'Failed to register Chief Engineer risk',
  );
}

export async function listChiefEngineerRisks(
  filters: RiskFilters = {},
  workspace = '',
): Promise<ApiResult<RiskListResponse>> {
  const query = new URLSearchParams();
  if (workspace) query.set('workspace', workspace);
  if (filters.taskId) query.set('task_id', filters.taskId);
  if (filters.severity) query.set('severity', filters.severity);
  if (filters.status) query.set('status', filters.status);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return apiGet<RiskListResponse>(
    `/v2/chief-engineer/risks${suffix}`,
    'Failed to list Chief Engineer risks',
  );
}

export async function updateChiefEngineerRiskStatus(
  riskId: string,
  status: RiskStatus,
  note = '',
  workspace = '',
): Promise<ApiResult<RiskRegisterResponse>> {
  return apiPost<RiskRegisterResponse>(
    appendWorkspaceQuery(`/v2/chief-engineer/risks/${encodeURIComponent(riskId)}/status`, workspace),
    { status, note },
    'Failed to update Chief Engineer risk status',
  );
}

export async function registerChiefEngineerTechDebt(
  payload: RegisterTechDebtPayload,
  workspace = '',
): Promise<ApiResult<TechDebtRegisterResponse>> {
  return apiPost<TechDebtRegisterResponse>(
    `/v2/chief-engineer/tech-debt${workspaceQuerySuffix(workspace)}`,
    payload,
    'Failed to register Chief Engineer tech debt',
  );
}

export async function listChiefEngineerTechDebt(
  filters: TechDebtFilters = {},
  workspace = '',
): Promise<ApiResult<TechDebtListResponse>> {
  const query = new URLSearchParams();
  if (workspace) query.set('workspace', workspace);
  if (filters.severity) query.set('severity', filters.severity);
  if (filters.surface) query.set('surface', filters.surface);
  if (filters.status) query.set('status', filters.status);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return apiGet<TechDebtListResponse>(
    `/v2/chief-engineer/tech-debt${suffix}`,
    'Failed to list Chief Engineer tech debt',
  );
}

export async function updateChiefEngineerTechDebtStatus(
  debtId: string,
  status: TechDebtStatus,
  note = '',
  workspace = '',
): Promise<ApiResult<TechDebtRegisterResponse>> {
  return apiPost<TechDebtRegisterResponse>(
    appendWorkspaceQuery(`/v2/chief-engineer/tech-debt/${encodeURIComponent(debtId)}/status`, workspace),
    { status, note },
    'Failed to update Chief Engineer tech debt status',
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Tier-2 governance surface: Architecture Decision Log
// ═══════════════════════════════════════════════════════════════════════

export type ADRStatus = 'proposed' | 'accepted' | 'superseded' | 'deprecated' | 'rejected';

export interface ADRRecord {
  adr_id: string;
  title: string;
  status: ADRStatus;
  context: string;
  decision: string;
  consequences: string;
  owner: string;
  decided_at: string;
  alternatives: string[];
  related_task_ids: string[];
  supersedes: string | null;
  history: Record<string, string>[];
}

export interface RegisterADRPayload {
  title: string;
  decision: string;
  owner: string;
  context?: string;
  consequences?: string;
  alternatives?: string[];
  related_task_ids?: string[];
  supersedes?: string | null;
}

export interface ADRRegisterResponse {
  ok: boolean;
  workspace: string;
  adr: ADRRecord;
}

export interface ADRListResponse {
  ok: boolean;
  workspace: string;
  total: number;
  adrs: ADRRecord[];
  summary: Record<string, unknown>;
}

export interface ADRFilters {
  status?: ADRStatus;
  taskId?: string;
}

export async function registerChiefEngineerADR(
  payload: RegisterADRPayload,
  workspace = '',
): Promise<ApiResult<ADRRegisterResponse>> {
  return apiPost<ADRRegisterResponse>(
    `/v2/chief-engineer/adrs${workspaceQuerySuffix(workspace)}`,
    payload,
    'Failed to record Chief Engineer ADR',
  );
}

export async function listChiefEngineerADRs(
  filters: ADRFilters = {},
  workspace = '',
): Promise<ApiResult<ADRListResponse>> {
  const query = new URLSearchParams();
  if (workspace) query.set('workspace', workspace);
  if (filters.status) query.set('status', filters.status);
  if (filters.taskId) query.set('task_id', filters.taskId);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return apiGet<ADRListResponse>(
    `/v2/chief-engineer/adrs${suffix}`,
    'Failed to list Chief Engineer ADRs',
  );
}

export async function updateChiefEngineerADRStatus(
  adrId: string,
  status: ADRStatus,
  note = '',
  workspace = '',
): Promise<ApiResult<ADRRegisterResponse>> {
  return apiPost<ADRRegisterResponse>(
    appendWorkspaceQuery(`/v2/chief-engineer/adrs/${encodeURIComponent(adrId)}/status`, workspace),
    { status, note },
    'Failed to update Chief Engineer ADR status',
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Tier-2 gate enforcement: Director-handoff decision
// ═══════════════════════════════════════════════════════════════════════

export interface HandoffDecision {
  allowed: boolean;
  blueprint_id: string;
  task_id: string;
  blocker_count: number;
  warning_count: number;
  open_blocker_risk_count: number;
  blockers: string[];
  reason: string;
  evaluated_at: string;
}

export interface HandoffDecisionResponse {
  ok: boolean;
  workspace: string;
  decision: HandoffDecision;
}

export async function getChiefEngineerHandoffDecision(
  blueprintId: string,
  workspace = '',
): Promise<ApiResult<HandoffDecisionResponse>> {
  const query = new URLSearchParams({ blueprint_id: blueprintId });
  if (workspace) query.set('workspace', workspace);
  return apiGet<HandoffDecisionResponse>(
    `/v2/chief-engineer/handoff-decision?${query.toString()}`,
    'Failed to load Chief Engineer handoff decision',
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Tier-2 stack/library policy: Tech Radar
// ═══════════════════════════════════════════════════════════════════════

export type TechRadarRing = 'adopt' | 'trial' | 'hold' | 'deprecated';

export interface TechRadarEntry {
  entry_id: string;
  library: string;
  ring: TechRadarRing;
  rationale: string;
  owner: string;
  decided_at: string;
  supersedes: string | null;
  history: Record<string, string>[];
}

export interface RegisterTechRadarPayload {
  library: string;
  ring: TechRadarRing;
  owner: string;
  rationale?: string;
  supersedes?: string | null;
}

export interface TechRadarEntryResponse {
  ok: boolean;
  workspace: string;
  entry: TechRadarEntry;
}

export interface TechRadarListResponse {
  ok: boolean;
  workspace: string;
  total: number;
  entries: TechRadarEntry[];
  summary: Record<string, unknown>;
}

export interface StackPolicyViolation {
  library: string;
  ring: TechRadarRing;
  rationale: string;
}

export interface StackPolicyCheckResponse {
  ok: boolean;
  workspace: string;
  allowed: boolean;
  violations: StackPolicyViolation[];
}

export async function registerChiefEngineerTechRadar(
  payload: RegisterTechRadarPayload,
  workspace = '',
): Promise<ApiResult<TechRadarEntryResponse>> {
  return apiPost<TechRadarEntryResponse>(
    `/v2/chief-engineer/tech-radar${workspaceQuerySuffix(workspace)}`,
    payload,
    'Failed to register Chief Engineer tech radar entry',
  );
}

export async function listChiefEngineerTechRadar(
  ring?: TechRadarRing,
  workspace = '',
): Promise<ApiResult<TechRadarListResponse>> {
  const query = new URLSearchParams();
  if (workspace) query.set('workspace', workspace);
  if (ring) query.set('ring', ring);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return apiGet<TechRadarListResponse>(
    `/v2/chief-engineer/tech-radar${suffix}`,
    'Failed to list Chief Engineer tech radar',
  );
}

export async function updateChiefEngineerTechRadarRing(
  entryId: string,
  ring: TechRadarRing,
  note = '',
  workspace = '',
): Promise<ApiResult<TechRadarEntryResponse>> {
  return apiPost<TechRadarEntryResponse>(
    appendWorkspaceQuery(`/v2/chief-engineer/tech-radar/${encodeURIComponent(entryId)}/ring`, workspace),
    { ring, note },
    'Failed to update Chief Engineer tech radar ring',
  );
}

export async function checkChiefEngineerStackPolicy(
  libraries: string[],
  workspace = '',
): Promise<ApiResult<StackPolicyCheckResponse>> {
  return apiPost<StackPolicyCheckResponse>(
    `/v2/chief-engineer/stack-policy/check${workspaceQuerySuffix(workspace)}`,
    { libraries },
    'Failed to check Chief Engineer stack policy',
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Tier-2 incident learning: Post-Mortem / Incident Review
// ═══════════════════════════════════════════════════════════════════════

export type IncidentSeverity = 'sev1' | 'sev2' | 'sev3' | 'sev4';
export type PostMortemStatus = 'draft' | 'reviewing' | 'published' | 'actions_open' | 'closed';

export interface PostMortemRecord {
  incident_id: string;
  title: string;
  severity: IncidentSeverity;
  summary: string;
  root_cause: string;
  impact: string;
  status: PostMortemStatus;
  occurred_at: string;
  owner: string;
  recorded_at: string;
  timeline: string[];
  action_items: string[];
  related_risk_ids: string[];
  history: Record<string, string>[];
}

export interface RegisterPostMortemPayload {
  title: string;
  severity: IncidentSeverity;
  occurred_at: string;
  owner: string;
  summary?: string;
  root_cause?: string;
  impact?: string;
  timeline?: string[];
  action_items?: string[];
  related_risk_ids?: string[];
}

export interface PostMortemRecordResponse {
  ok: boolean;
  workspace: string;
  post_mortem: PostMortemRecord;
}

export interface PostMortemListResponse {
  ok: boolean;
  workspace: string;
  total: number;
  post_mortems: PostMortemRecord[];
  summary: Record<string, unknown>;
}

export interface PostMortemFilters {
  severity?: IncidentSeverity;
  status?: PostMortemStatus;
}

export async function registerChiefEngineerPostMortem(
  payload: RegisterPostMortemPayload,
  workspace = '',
): Promise<ApiResult<PostMortemRecordResponse>> {
  return apiPost<PostMortemRecordResponse>(
    `/v2/chief-engineer/post-mortems${workspaceQuerySuffix(workspace)}`,
    payload,
    'Failed to record Chief Engineer post-mortem',
  );
}

export async function listChiefEngineerPostMortems(
  filters: PostMortemFilters = {},
  workspace = '',
): Promise<ApiResult<PostMortemListResponse>> {
  const query = new URLSearchParams();
  if (workspace) query.set('workspace', workspace);
  if (filters.severity) query.set('severity', filters.severity);
  if (filters.status) query.set('status', filters.status);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return apiGet<PostMortemListResponse>(
    `/v2/chief-engineer/post-mortems${suffix}`,
    'Failed to list Chief Engineer post-mortems',
  );
}

export async function updateChiefEngineerPostMortemStatus(
  incidentId: string,
  status: PostMortemStatus,
  note = '',
  workspace = '',
): Promise<ApiResult<PostMortemRecordResponse>> {
  return apiPost<PostMortemRecordResponse>(
    appendWorkspaceQuery(`/v2/chief-engineer/post-mortems/${encodeURIComponent(incidentId)}/status`, workspace),
    { status, note },
    'Failed to update Chief Engineer post-mortem status',
  );
}
