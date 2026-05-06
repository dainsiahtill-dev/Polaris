"""Common HTTP response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RoleChatPingResponse(BaseModel):
    status: str
    message: str
    supported_roles: list[str]


class RoleListResponse(BaseModel):
    roles: list[str]
    count: int


class CacheStatsResponse(BaseModel):
    model_config = {"extra": "allow"}


class CacheClearResponse(BaseModel):
    ok: bool
    message: str


class RoleChatStatusResponse(BaseModel):
    model_config = {"extra": "allow"}
    ready: bool
    configured: bool
    llm_test_ready: bool | None = None
    role: str | None = None
    role_config: dict[str, Any] | None = None
    provider_type: str | None = None
    debug: dict[str, Any] | None = None
    error: str | None = None
    code: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


class RoleLLMEventsResponse(BaseModel):
    model_config = {"extra": "allow"}
    role: str
    run_id: str | None = None
    task_id: str | None = None
    events: list[dict[str, Any]]
    stats: dict[str, Any]


class AllLLMEventsResponse(BaseModel):
    model_config = {"extra": "allow"}
    events: list[dict[str, Any]]
    count: int


class RoleChatResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    response: str | None = None
    thinking: str | None = None
    role: str | None = None
    model: str | None = None
    provider: str | None = None


class PMChatPingResponse(BaseModel):
    status: str
    message: str
    role: str


class PMChatStatusResponse(BaseModel):
    ready: bool
    configured: bool
    llm_test_ready: bool | None = None
    role_config: dict[str, Any] | None = None
    provider_type: str | None = None
    debug: dict[str, Any] | None = None


class StreamHealthResponse(BaseModel):
    status: str
    streaming: str


class PMStatusResponse(BaseModel):
    model_config = {"extra": "allow"}
    initialized: bool
    workspace: str
    project: str | None = None
    version: str | None = None
    stats: dict[str, Any] | None = None
    storage: dict[str, Any] | None = None


class PMHealthResponse(BaseModel):
    model_config = {"extra": "allow"}
    overall: str
    components: dict[str, str]
    metrics: dict[str, float]
    recommendations: list[str]


class PMInitResponse(BaseModel):
    model_config = {"extra": "allow"}
    initialized: bool
    workspace: str
    project_name: str | None = None
    pm_version: str | None = None
    message: str | None = None


class TaskSnapshotsResponse(BaseModel):
    model_config = {"extra": "allow"}
    snapshots: list[dict[str, Any]]
    total: int


class FactorySnapshotsResponse(BaseModel):
    model_config = {"extra": "allow"}
    factory_runs: list[dict[str, Any]]
    total: int


class FactoryRunEventsResponse(BaseModel):
    model_config = {"extra": "allow"}
    events: list[dict[str, Any]]


class FactoryRunArtifactItem(BaseModel):
    model_config = {"extra": "allow"}
    name: str
    path: str
    size: int


class FactoryRunArtifactsResponse(BaseModel):
    model_config = {"extra": "allow"}
    run_id: str
    artifacts: list[FactoryRunArtifactItem]
    summary_md: str | None = None
    summary_json: dict[str, Any] | None = None


class FactoryRunAuditBundleResponse(BaseModel):
    model_config = {"extra": "allow"}
    run_id: str
    status: str | None = None
    phase: str | None = None
    progress: float | None = None
    current_stage: str | None = None
    last_successful_stage: str | None = None
    gates: list[dict[str, Any]] | None = None
    failure: dict[str, Any] | None = None
    events_tail: list[dict[str, Any]] | None = None
    artifacts: list[FactoryRunArtifactItem] | None = None
    summary_md: str | None = None
    summary_json: dict[str, Any] | None = None
    generated_at: str | None = None
    evidence_counts: dict[str, Any] | None = None


class SessionDeleteResponse(BaseModel):
    ok: bool


class ConversationResponse(BaseModel):
    model_config = {"extra": "allow"}
    id: str | None = None
    title: str | None = None
    role: str | None = None
    workspace: str | None = None
    context_config: dict[str, Any] | None = None
    message_count: int | None = None
    created_at: Any | None = None
    updated_at: Any | None = None
    messages: list[dict[str, Any]] | None = None


class ConversationListResponse(BaseModel):
    conversations: list[dict[str, Any]]
    total: int


class ConversationDeleteResponse(BaseModel):
    ok: bool
    deleted_id: str


class MessageResponse(BaseModel):
    model_config = {"extra": "allow"}
    id: str | None = None
    conversation_id: str | None = None
    sequence: int | None = None
    role: str | None = None
    content: str | None = None
    thinking: str | None = None
    meta: dict[str, Any] | None = None
    created_at: Any | None = None


class MessageBatchResponse(BaseModel):
    ok: bool
    added_count: int


class MessageDeleteResponse(BaseModel):
    ok: bool
    deleted_id: str


class MemoryStateResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str
    path: str
    value: Any | None = None


# ---- Reusable CRUD envelope models ----


class DocumentVersionInfo(BaseModel):
    version: str
    created_at: str
    created_by: str
    change_summary: str
    checksum: str


class DocumentDiffResponse(BaseModel):
    path: str
    old_version: str
    new_version: str
    diff_text: str
    changed_sections: list[str]
    added_requirements: list[str]
    removed_requirements: list[str]
    impact_score: float


class DocumentInfo(BaseModel):
    path: str
    current_version: str
    version_count: int
    last_modified: str
    created_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentInfo]
    pagination: dict[str, Any]


class DocumentDetailResponse(BaseModel):
    model_config = {"extra": "allow"}
    path: str
    current_version: str
    version_count: int
    last_modified: str
    created_at: str
    content: str | None = None
    versions: list[DocumentVersionInfo] | None = None
    analysis: dict[str, Any] | None = None


class DocumentVersionsResponse(BaseModel):
    path: str
    versions: list[DocumentVersionInfo]


class DocumentWriteResponse(BaseModel):
    success: bool
    path: str
    version: str | None = None
    checksum: str | None = None


class DocumentDeleteResponse(BaseModel):
    success: bool
    path: str
    deleted: bool


class DocumentSearchResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]
    count: int


# ---- Agent response models ----


class AgentSessionListResponse(BaseModel):
    model_config = {"extra": "allow"}
    sessions: list[dict[str, Any]]
    total: int


class AgentSessionResponse(BaseModel):
    model_config = {"extra": "allow"}
    session_id: str
    workspace: Any | None = None
    created_at: Any | None = None
    updated_at: Any | None = None
    message_count: int | None = None
    role: str | None = None
    context: dict[str, Any] | None = None
    history: list[dict[str, Any]] | None = None
    recent_tools: list[dict[str, Any]] | None = None
    failure_summary: dict[str, Any] | None = None


class AgentMemorySearchResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str | None = None
    query: str | None = None
    kind: str | None = None
    entity: str | None = None
    total: int | None = None
    items: list[dict[str, Any]] | None = None
    error_code: str | None = None
    error: str | None = None


class AgentArtifactResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str | None = None
    artifact: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None


class AgentEpisodeResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str | None = None
    episode: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None


class AgentMemoryStateResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str | None = None
    path: str | None = None
    value: Any | None = None
    error_code: str | None = None
    error: str | None = None


class AgentMessageResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str
    reply: str | None = None
    reasoning_summary: str | None = None
    tool_calls: list[str] | None = None
    error: str | None = None


class AgentTurnResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str
    reply: str | None = None
    reasoning_summary: str | None = None
    phase: str | None = None
    stream_url: str | None = None
    error: str | None = None


class SessionResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session: dict[str, Any]


class SessionListResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    sessions: list[dict[str, Any]]
    total: int


class MessageListResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    messages: list[dict[str, Any]]
    session: dict[str, Any]


class ArtifactListResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    artifacts: list[dict[str, Any]]


class AuditLogResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    audit_events: list[dict[str, Any]]


class HistoryRunListResponse(BaseModel):
    model_config = {"extra": "allow"}
    runs: list[dict[str, Any]]
    total: int


class HistoryManifestResponse(BaseModel):
    model_config = {"extra": "allow"}
    manifest: dict[str, Any]


class HistoryEventsResponse(BaseModel):
    model_config = {"extra": "allow"}
    run_id: str
    events: list[dict[str, Any]]
    count: int


class TaskListResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    tasks: list[dict[str, Any]]


class TaskDetailResponse(BaseModel):
    model_config = {"extra": "allow"}


class TaskHistoryResponse(BaseModel):
    model_config = {"extra": "allow"}


class TaskAssignmentsResponse(BaseModel):
    model_config = {"extra": "allow"}
    task_id: str
    assignments: list[dict[str, Any]]
    count: int


class TaskSearchResponse(BaseModel):
    model_config = {"extra": "allow"}
    query: str
    results: list[dict[str, Any]]
    count: int


class RequirementListResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    requirements: list[dict[str, Any]]


class RequirementDetailResponse(BaseModel):
    model_config = {"extra": "allow"}


# ---- Cognitive Runtime response models ----


class RuntimeReceiptResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    receipt: dict[str, Any] | None = None


class HandoffPackResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    handoff: dict[str, Any] | None = None


class CognitiveRuntimeActionResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    snapshot: dict[str, Any] | None = None
    lease: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    rehydration: dict[str, Any] | None = None
    mapping: dict[str, Any] | None = None
    request: dict[str, Any] | None = None
    entry: dict[str, Any] | None = None


class CognitiveRuntimeValidationResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    validation: dict[str, Any] | None = None


class CognitiveRuntimeDecisionResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    decision: dict[str, Any] | None = None


class MemorySearchResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str
    query: str | None = None
    kind: str | None = None
    entity: str | None = None
    total: int
    items: list[dict[str, Any]]


class ArtifactDetailResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str
    artifact: dict[str, Any]


class EpisodeDetailResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str
    episode: dict[str, Any]


class SessionExportResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    export: dict[str, Any]


class WorkflowExportResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    exported_to: str
    run_id: str
    session_id: str
    artifact_count: int


class RoleCapabilitiesResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    role: str
    capabilities: dict[str, Any]


class VisionStatusResponse(BaseModel):
    model_config = {"extra": "allow"}
    pil_available: bool
    advanced_available: bool
    model_loaded: bool


class VisionAnalyzeResponse(BaseModel):
    model_config = {"extra": "allow"}


class SchedulerStatusResponse(BaseModel):
    model_config = {"extra": "allow"}
    available: bool
    active: bool
    reason: str
    message: str | None = None


class CodeMapResponse(BaseModel):
    model_config = {"extra": "allow"}
    points: list[dict[str, Any]]
    mode: str
    message: str | None = None


class CodeIndexResponse(BaseModel):
    model_config = {"extra": "allow"}
    result: list[dict[str, Any]] | dict[str, Any] | None = None
    ok: bool
    error: str | None = None


class CodeSearchResponse(BaseModel):
    model_config = {"extra": "allow"}
    results: list[dict[str, Any]]
    ok: bool
    error: str | None = None


class MCPStatusResponse(BaseModel):
    model_config = {"extra": "allow"}
    available: bool
    healthy: bool
    server_path: str | None = None
    server_version: str | None = None
    tools: list[str]
    protocol: str
    error: str | None = None
    health_check: dict[str, Any] | None = None


class DirectorCapabilitiesResponse(BaseModel):
    model_config = {"extra": "allow"}
    role: str
    capabilities: list[Any]


class LLMConfigResponse(BaseModel):
    model_config = {"extra": "allow"}


class LLMStatusResponse(BaseModel):
    model_config = {"extra": "allow"}


class LLMRuntimeStatusResponse(BaseModel):
    model_config = {"extra": "allow"}
    roles: dict[str, Any]
    timestamp: str


class LLMRoleRuntimeStatusResponse(BaseModel):
    model_config = {"extra": "allow"}
    running: bool
    last_run: str | None = None
    config: dict[str, Any]
    started_at: str | None = None
    pid: int | None = None
    last_status: str | None = None
    last_error: str | None = None
    role_id: str | None = None


class LLMMigrateConfigResponse(BaseModel):
    model_config = {"extra": "allow"}


# ---- Runtime response models ----


class RuntimeStorageLayoutResponse(BaseModel):
    model_config = {"extra": "allow"}


class RuntimeClearResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    scope: str


class RuntimeMigrationStatusResponse(BaseModel):
    model_config = {"extra": "allow"}
    version: int
    cutover_at: str | None = None
    backup_path: str
    archived_counts: dict[str, int]
    strict_mode: bool


class RuntimeResetTasksResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    pm_running: bool
    pm_external_terminated_pids: list[int]
    director_running: bool
    director_external_pid: int | None = None
    director_external_terminated: bool


# ---- System response models ----


class HealthResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    version: str
    timestamp: str
    lancedb_ok: bool
    lancedb_error: str | None = None
    python: str | None = None
    pm: dict[str, Any]
    director: dict[str, Any]


class PrimaryHealthResponse(BaseModel):
    status: str
    service: str
    version: str


class PrimaryReadyResponse(BaseModel):
    model_config = {"extra": "allow"}
    ready: bool
    checks: dict[str, str]


class PrimaryLiveResponse(BaseModel):
    alive: bool
    timestamp: str


class SettingsResponse(BaseModel):
    model_config = {"extra": "allow"}


class StateSnapshotResponse(BaseModel):
    model_config = {"extra": "allow"}


class ShutdownResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    pm_running: bool
    pm_external_terminated_pids: list[int]
    director_running: bool
    pm_terminated: bool
    director_terminated: bool


class ReadyResponse(BaseModel):
    model_config = {"extra": "allow"}
    ready: bool
    checks: dict[str, str] | None = None


class LiveResponse(BaseModel):
    live: bool


# ---- Test response models ----


class LlmTestReportResponse(BaseModel):
    model_config = {"extra": "allow"}


class LlmTestTranscriptResponse(BaseModel):
    ok: bool
    content: str


# ---- Docs init response models ----


class DocsInitDialogueResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    reply: str
    questions: list[str]
    tiaochen: list[str]
    meta: dict[str, Any]
    handoffs: dict[str, Any]
    fields: dict[str, str]


class DocsInitSuggestResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    fields: dict[str, str]


class DocsInitPreviewFile(BaseModel):
    path: str
    content: str
    exists: bool


class DocsInitPreviewResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    mode: str
    target_root: str
    docs_exists: bool
    project: dict[str, Any] | None = None
    files: list[DocsInitPreviewFile]


class DocsInitApplyResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    files: list[str]


# ---- Provider response models ----


class ProviderInfoItem(BaseModel):
    model_config = {"extra": "allow"}
    name: str
    type: str
    description: str
    version: str
    author: str
    documentation_url: str
    supported_features: list[str]
    cost_class: str
    provider_category: str
    autonomous_file_access: bool
    requires_file_interfaces: bool
    model_listing_method: str


class ProviderListResponse(BaseModel):
    providers: list[ProviderInfoItem]


class ProviderDetailResponse(BaseModel):
    model_config = {"extra": "allow"}
    name: str
    type: str
    description: str
    version: str
    author: str
    documentation_url: str
    supported_features: list[str]
    cost_class: str


class ProviderConfigResponse(BaseModel):
    model_config = {"extra": "allow"}


class ProviderValidationResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
    normalized_config: dict[str, Any] | None = None


class ProviderHealthResponse(BaseModel):
    model_config = {"extra": "allow"}


class ProviderModelsResponse(BaseModel):
    model_config = {"extra": "allow"}


class ProviderHealthAllResponse(BaseModel):
    model_config = {"extra": "allow"}


class CourtTopologyResponse(BaseModel):
    model_config = {"extra": "allow"}
    nodes: list[dict[str, Any]]
    count: int
    total: int
    scenes: dict[str, Any]


class CourtStateResponse(BaseModel):
    model_config = {"extra": "allow"}


class CourtActorResponse(BaseModel):
    model_config = {"extra": "allow"}


class CourtSceneResponse(BaseModel):
    model_config = {"extra": "allow"}


class CourtMappingResponse(BaseModel):
    model_config = {"extra": "allow"}
    tech_to_court: dict[str, Any]
    court_roles: list[str]
    version: str
    description: str


# ---- Interview response models ----


class InterviewAskResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    session_id: str
    output: str
    thinking: str
    answer: str
    evaluation: dict[str, Any]


class InterviewSaveResponse(BaseModel):
    ok: bool
    saved: bool


class InterviewCancelResponse(BaseModel):
    ok: bool
    cancelled: bool


# ---- File response models ----


class FileReadResponse(BaseModel):
    path: str
    rel_path: str
    mtime: str
    content: str


# ---- Logs response models ----


class LogsQueryResponse(BaseModel):
    events: list[dict[str, Any]]
    next_cursor: str | None = None
    total_count: int
    has_more: bool


class LogsUserActionResponse(BaseModel):
    status: str
    action: str


class LogsChannelsResponse(BaseModel):
    channels: list[dict[str, str]]


# ---- LanceDB response models ----


class LanceDBStatusResponse(BaseModel):
    model_config = {"extra": "allow"}


# ---- Memos response models ----


class MemosListResponse(BaseModel):
    model_config = {"extra": "allow"}


# ---- Ollama response models ----


class OllamaModelsResponse(BaseModel):
    model_config = {"extra": "allow"}
    models: list[str] = []


class OllamaStopResponse(BaseModel):
    model_config = {"extra": "allow"}


# ---- Memory response models ----


class MemoryDeleteResponse(BaseModel):
    status: str
    id: str


# ---- Agents response models ----


class AgentsApplyResponse(BaseModel):
    ok: bool
    target_path: str


class AgentsFeedbackResponse(BaseModel):
    model_config = {"extra": "allow"}
    ok: bool
    path: str | None = None
    mtime: str | None = None
    cleared: bool | None = None
