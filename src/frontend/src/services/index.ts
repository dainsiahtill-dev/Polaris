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
  createDirectorTask,
} from './pmService';

export type {
  PmStatus,
  DirectorStatus,
  DirectorTask,
  CreateDirectorTaskPayload,
} from './pmService';

// ============================================================================
// Factory Service
// ============================================================================

export {
  startFactoryRun,
  stopFactoryRun,
  getFactoryRun,
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
