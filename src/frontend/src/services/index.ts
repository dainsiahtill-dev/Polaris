/**
 * Services Index
 *
 * 统一导出所有服务层API
 */

// ============================================================================
// Core Types
// ============================================================================

export type { ApiResult, ApiListResponse } from './api.types';

export type {
  // Process Status
  ProcessStatus,
  DirectorStatusPayload,
  // Tasks
  TaskResponse,
  TodoItemResponse,
  TodoSummaryResponse,
  // Token Budget
  TokenStatusResponse,
  TokenRecordResponse,
  // Security
  SecurityCheckResponse,
  // Transcript
  TranscriptMessage,
  TranscriptSessionResponse,
  // Factory
  FactoryRunArtifact,
  FactoryRunArtifactsResponse,
  FactoryRunStatus,
  FactoryStartOptions,
  FactoryAuditEvent,
  // Court
  CourtState,
  CourtTopologyResponse,
  CourtActorState,
  CourtSceneConfig,
  CourtScenePhase,
  ActorStatus,
  RiskLevel,
  CourtMappingResponse,
  // LLM
  LLMConfigResponse,
  LLMStatusResponse,
  ProviderConfig,
  RoleConfig,
  ChatStatus,
  ChatMessageRequest,
  // File
  FilePayload,
  // Memo
  MemoItem,
  MemoListResponse,
  // Settings
  BackendSettings,
  // Snapshot
  SnapshotPayload,
  AgentsReviewInfo,
  RuntimeIssue,
  WorkspaceStatus,
  ResidentStatusPayload,
  ResidentStatusDetailsPayload,
  ResidentGoalPayload,
  ResidentGoalStagePayload,
  ResidentGoalRunPayload,
  ResidentDecisionPayload,
  ResidentSkillPayload,
  ResidentExperimentPayload,
  ResidentImprovementPayload,
  ResidentCapabilityGraphPayload,
  // LanceDB
  LanceDbStatus,
  // Health
  HealthCheckResponse,
} from './api.types';

// ============================================================================
// API Client
// ============================================================================

export {
  ApiError,
  extractErrorDetail,
  formatErrorMessage,
  apiGet,
  apiPost,
  apiPostEmpty,
  apiPut,
  apiDelete,
  buildQueryString,
  handleEmptyResponse,
  handleJsonResponse,
} from './apiClient';

// ============================================================================
// PM Service
// ============================================================================

export {
  // Status
  getPmStatus,
  getPmStartupDiagnostics,
  getDirectorStatus,
  getAllStatuses,
  // Process Control
  startPm,
  stopPm,
  runPmOnce,
  startDirector,
  stopDirector,
  // Director Tasks
  listDirectorTasks,
  getDirectorTask,
  listDirectorWorkers,
  getDirectorWorker,
  listDirectorTaskFallbackRows,
  resolveDirectorTaskSources,
  listPmTasks,
  getPmTask,
  listPmTaskAssignments,
  listPmRequirements,
  getPmRequirement,
  createDirectorTask,
  cancelDirectorTask,
  cancelDirectorRun,
  runPm,
  runDirector,
  getPmRun,
  cancelPmRun,
  getDirectorRun,
  getDirectorCapabilities,
  getRoleKernelCacheStats,
  clearRoleKernelCache,
  getRoleKernelTokenBudgetStats,
  getRoleKernelLLMEvents,
  getDirectorTaskKernelLLMEvents,
} from './pmService';

export type {
  PmStatus,
  PmStartupDiagnosticsResponse,
  PmDiagnosticsLanceDBStatus,
  PmDiagnosticsLLMStatus,
  PmDiagnosticsWorkspaceStatus,
  DirectorStatus,
  DirectorTask,
  DirectorWorker,
  CreateDirectorTaskPayload,
  CancelDirectorTaskResponse,
  RunPmPayload,
  RunDirectorPayload,
  RunDirectorResponse,
  PmOrchestrationRunResponse,
  DirectorOrchestrationRunResponse,
  DirectorCapabilitiesResponse,
  DirectorFallbackTaskRow,
  DirectorTaskSource,
  PmTaskListParams,
  PmTaskListResponse,
  PmTaskAssignmentEntry,
  PmTaskAssignmentsResponse,
  PmRequirementEntry,
  PmRequirementListParams,
  PmRequirementListResponse,
  PmTaskSearchResult,
  PmTaskSearchResponse,
  RoleKernelDiagnosticsRole,
  RoleKernelCacheStats,
  RoleKernelCacheClearResponse,
  RoleKernelTokenBudgetStats,
  RoleKernelLLMEvent,
  RoleKernelLLMEventsResponse,
  RoleKernelLLMEventsQuery,
} from './pmService';

// ============================================================================
// Chief Engineer Service
// ============================================================================

export {
  getChiefEngineerDiagnostics,
  generateChiefEngineerBlueprint,
  getChiefEngineerBlueprintStatus,
  listChiefEngineerBlueprints,
  getChiefEngineerBlueprint,
} from './chiefEngineerService';

export type {
  ChiefEngineerDiagnosticsResponse,
  ChiefEngineerDiagnosticsWorkspaceStatus,
  ChiefEngineerDiagnosticsBlueprintStatus,
  GenerateChiefEngineerBlueprintPayload,
  ChiefEngineerTaskBlueprintResultResponse,
  ChiefEngineerBlueprintListResponse,
  ChiefEngineerBlueprintDetailResponse,
} from './chiefEngineerService';

// ============================================================================
// Role Session Service
// ============================================================================

export {
  attachRoleSession,
  createRoleSession,
  detachRoleSession,
  exportRoleSessionSnapshot,
  exportRoleSessionToWorkflow,
  getRoleCapabilities,
  getRoleSession,
  listRoleSessionArtifacts,
  listRoleSessionAuditEvents,
  listRoleSessionMessages,
  listRoleSessions,
  readRoleSessionMemoryArtifact,
  readRoleSessionMemoryEpisode,
  readRoleSessionMemoryState,
  resolveRoleCapabilities,
  searchRoleSessionMemory,
} from './roleSessionService';

export type {
  AttachRoleSessionPayload,
  CreateRoleSessionPayload,
  ExportRoleSessionSnapshotPayload,
  ExportRoleSessionToWorkflowPayload,
  ListRoleSessionsParams,
  RoleCapabilitiesResponse,
  RoleSessionArtifactItem,
  RoleSessionAuditEventItem,
  RoleSessionDetailItem,
  RoleSessionListItem,
  RoleSessionMemoryDetailItem,
  RoleSessionMemoryItem,
  RoleSessionMessageItem,
  RoleSessionSnapshotExportFormat,
  RoleSessionWorkflowExportResponse,
} from './roleSessionService';

// ============================================================================
// Factory Service
// ============================================================================

export {
  startFactoryRun,
  stopFactoryRun,
  getFactoryRun,
  getFactoryRunArtifacts,
  listFactoryRuns,
  connectFactoryStream,
} from './factoryService';

export type { FactoryStreamConnection, FactoryStreamHandlers } from './factoryService';

// ============================================================================
// Court Service
// ============================================================================

export {
  getCourtTopology,
  getCourtState,
  getActorDetail,
  getSceneConfig,
  getRoleMapping,
} from './courtService';

// ============================================================================
// LLM Service
// ============================================================================

export {
  getLLMConfig,
  saveLLMConfig,
  getLLMStatus,
  getRoleChatStatus,
  sendRoleChatMessage,
  parseSSEData,
  createStreamReader,
} from './llmService';

export type { ChatStreamEvent } from './llmService';

// ============================================================================
// File Service
// ============================================================================

export {
  normalizeArtifactPath,
  readFile,
  readLogTail,
  readJsonFile,
} from './fileService';

// ============================================================================
// Legacy Services (保持向后兼容)
// ============================================================================

export {
  // Settings
  settingsService,
  // Status
  statusService,
  // Process
  processService,
  // Snapshot
  snapshotService,
  // Resident
  residentService,
  // LanceDB
  lancedbService,
  // LLM (legacy)
  llmService as legacyLlmService,
  // File (legacy)
  fileService as legacyFileService,
  // Memo
  memoService,
  // Runtime
  runtimeService,
  // Ollama
  ollamaService,
  // Health
  healthService,
  // Agents
  agentsService,
  // V2 Services
  v2Services,
} from './api';

export type {
  ApiResult as LegacyApiResult,
} from './api';
