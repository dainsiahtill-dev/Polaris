/**
 * Runtime Projection Adapter
 * Converts runtime.v2 status events into the canonical RuntimeProjectionPayload.
 *
 * Backend services should eventually emit RuntimeProjectionPayload directly.
 * Until then, the frontend accepts only canonical projections and runtime.v2
 * status events from the unified WebSocket transport.
 */

import {
  RuntimeProjectionPayload,
  RuntimeProjectionProvenance,
  RuntimeProjectionSource,
  PMLocalStatus,
  DirectorLocalStatus,
  WorkflowStatus,
  EngineStatus,
  WorkflowTask,
  PMPhase,
  DirectorPhase,
  TaskStatus,
  isRuntimeProjectionPayload,
} from "./projection";

// ============================================================================
// Runtime Status Event Types
// ============================================================================

interface DirectorServiceMetrics {
  tasks_submitted?: number | string;
  tasks_completed?: number | string;
  tasks_failed?: number | string;
  tasks_active?: number | string;
  active_tasks?: number | string;
  tasks_running?: number | string;
  running_tasks?: number | string;
  tasks_pending?: number | string;
  pending_tasks?: number | string;
  queue_depth?: number | string;
}

interface DirectorServiceStatus {
  state?: string;
  execution_state?: string;
  run_id?: string;
  current_run_id?: string;
  id?: string;
  is_running?: boolean;
  metrics?: DirectorServiceMetrics | null;
}

interface RuntimeStatusEvent {
  type?: string;
  protocol?: string;
  pm_status?: {
    running?: boolean;
    phase?: string;
    current_task_id?: string;
    progress?: number;
    message?: string;
  } | null;
  director_status?: {
    running?: boolean;
    state?: string;
    execution_state?: string;
    phase?: string;
    active_tasks?: number;
    completed_tasks?: number;
    failed_tasks?: number;
    current_run_id?: string;
    queue_depth?: number;
    mode?: string;
    pid?: number | null;
    status?: DirectorServiceStatus | string | null;
  } | null;
  snapshot?: {
    run_id?: string;
    tasks?: Array<{
      id?: string;
      title?: string;
      name?: string;
      subject?: string;
      goal?: string;
      status?: string;
      assignee?: string;
      priority?: string;
      done?: boolean;
      completed?: boolean;
      metadata?: Record<string, unknown>;
      blueprint_id?: string | null;
      blueprint_path?: string | null;
      runtime_blueprint_path?: string | null;
      acceptance?: unknown[];
      acceptance_criteria?: string[];
      execution_checklist?: string[];
      target_files?: string[];
      scope_paths?: string[];
      files?: string[];
      dependencies?: string[];
      blocked_by?: string[];
    }>;
    timestamp?: string;
    progress?: number;
  } | null;
  engine_status?: {
    version?: string;
    mode?: string;
    health?: string;
    roles?: Record<string, unknown>;
    error?: string;
    summary?: Record<string, unknown>;
    run_id?: string;
  } | null;
}

// ============================================================================
// Projection Adapter Functions
// ============================================================================

const RUNTIME_STATUS_EVENT_FIELDS = [
  "pm_status",
  "director_status",
  "snapshot",
  "engine_status",
] as const;

function toFiniteNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function toOptionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function metricValue(metrics: DirectorServiceMetrics | null | undefined, ...keys: Array<keyof DirectorServiceMetrics>): number | undefined {
  if (!metrics) {
    return undefined;
  }
  for (const key of keys) {
    const value = toFiniteNumber(metrics[key]);
    if (value !== undefined) {
      return value;
    }
  }
  return undefined;
}

function deriveOutstandingTasks(
  metrics: DirectorServiceMetrics | null | undefined,
  completedTasks: number,
  failedTasks: number
): number | undefined {
  const explicit = metricValue(
    metrics,
    "active_tasks",
    "tasks_active",
    "tasks_running",
    "running_tasks",
    "queue_depth",
    "tasks_pending",
    "pending_tasks"
  );
  if (explicit !== undefined) {
    return Math.max(0, explicit);
  }

  const submitted = metricValue(metrics, "tasks_submitted");
  if (submitted === undefined) {
    return undefined;
  }
  return Math.max(0, submitted - completedTasks - failedTasks);
}

function normalizeDirectorStateToken(value: unknown, runningHint: boolean): string {
  const state = String(value || "").trim().toLowerCase();
  if (["running", "working", "active", "executing", "in_progress", "in-progress"].includes(state)) {
    return "running";
  }
  if (["completed", "complete", "done", "success", "succeeded", "stopped", "completed_verified"].includes(state)) {
    return "completed";
  }
  if (
    ["failed", "failure", "error", "failed_platform", "failed_artifact", "blocked_with_reason"].includes(state) ||
    state.startsWith("failed_") ||
    state.includes("failure") ||
    state.includes("error")
  ) {
    return "error";
  }
  if (["paused", "pause"].includes(state)) {
    return "paused";
  }
  if (["recovering", "retrying"].includes(state)) {
    return "recovering";
  }
  if (["idle", "ready", "waiting"].includes(state)) {
    return "idle";
  }
  return runningHint ? "running" : "idle";
}

function createProjectionProvenance(params: {
  source: RuntimeProjectionSource;
  transformed: boolean;
  receivedAt: string;
  reason?: string;
  sourceFields?: string[];
  sourceSchema?: string;
}): RuntimeProjectionProvenance {
  return {
    source: params.source,
    transformed: params.transformed,
    received_at: params.receivedAt,
    source_schema: params.sourceSchema,
    adaptation_reason: params.reason,
    source_fields: params.sourceFields && params.sourceFields.length > 0 ? params.sourceFields : undefined,
  };
}

function collectRuntimeStatusFields(response: unknown): string[] {
  if (!response || typeof response !== "object") return [];
  const obj = response as Record<string, unknown>;
  return RUNTIME_STATUS_EVENT_FIELDS.filter((field) => field in obj);
}

function attachProjectionProvenance(
  projection: RuntimeProjectionPayload,
  provenance: RuntimeProjectionProvenance
): RuntimeProjectionPayload {
  return {
    ...projection,
    projection_source: provenance.source,
    provenance,
  };
}

/**
 * Normalize canonical runtime projection or runtime.v2 status event input.
 *
 * @param response - Runtime projection or runtime.v2 status event.
 * @returns Canonical RuntimeProjectionPayload.
 */
export function normalizeRuntimeProjection(response: unknown): RuntimeProjectionPayload {
  // Handle null/undefined
  if (!response) {
    return createEmptyProjection();
  }

  // Already canonical
  if (isCanonicalProjection(response)) {
    const canonical = response as RuntimeProjectionPayload;
    if (canonical.projection_source && canonical.provenance) {
      return canonical;
    }
    const receivedAt = canonical.generated_at || new Date().toISOString();
    return attachProjectionProvenance(
      canonical,
      createProjectionProvenance({
        source: "canonical",
        transformed: false,
        receivedAt,
        sourceSchema: "runtime_projection",
      })
    );
  }

  if (!isRuntimeStatusEvent(response)) {
    return createEmptyProjection("non_projection_runtime_payload");
  }

  const event = response as RuntimeStatusEvent;
  const generatedAt = new Date().toISOString();
  const provenance = createProjectionProvenance({
    source: "runtime_status_event",
    transformed: true,
    receivedAt: generatedAt,
    reason: "runtime_v2_status_event",
    sourceFields: collectRuntimeStatusFields(response),
    sourceSchema: typeof event.protocol === "string" ? event.protocol : "runtime.v2",
  });

  return attachProjectionProvenance({
    pm: normalizePMStatus(event),
    director: normalizeDirectorStatus(event),
    workflow: normalizeWorkflowStatus(event),
    engine: normalizeEngineStatus(event),
    generated_at: generatedAt,
  }, provenance);
}

/**
 * Check if response is already in canonical format
 */
function isCanonicalProjection(response: unknown): boolean {
  return isRuntimeProjectionPayload(response);
}

/**
 * Check whether input is a runtime.v2 status event shape.
 */
function isRuntimeStatusEvent(response: unknown): boolean {
  if (!response || typeof response !== "object") return false;
  const event = response as RuntimeStatusEvent;
  return (
    (event.pm_status !== undefined && (event.pm_status === null || typeof event.pm_status === "object")) ||
    (event.director_status !== undefined &&
      (event.director_status === null || typeof event.director_status === "object")) ||
    (event.snapshot !== undefined && (event.snapshot === null || typeof event.snapshot === "object")) ||
    (event.engine_status !== undefined && (event.engine_status === null || typeof event.engine_status === "object"))
  );
}

/**
 * Normalize PM status from runtime status event.
 */
function normalizePMStatus(event: RuntimeStatusEvent): PMLocalStatus | null {
  const pmNested = event.pm_status;
  if (pmNested && typeof pmNested === 'object') {
    return {
      running: Boolean(pmNested.running),
      current_task_id: pmNested.current_task_id ?? null,
      phase: normalizePMPhase(pmNested.phase),
      progress: pmNested.progress,
      message: pmNested.message,
      last_updated: new Date().toISOString(),
    };
  }
  return null;
}

/**
 * Normalize Director status from runtime status event.
 */
function normalizeDirectorStatus(event: RuntimeStatusEvent): DirectorLocalStatus | null {
  const directorNested = event.director_status;
  if (directorNested && typeof directorNested === 'object') {
    const serviceStatus = directorNested.status && typeof directorNested.status === "object" ? directorNested.status : null;
    const metrics = serviceStatus?.metrics || null;
    const completedTasks = directorNested.completed_tasks ?? metricValue(metrics, "tasks_completed") ?? 0;
    const failedTasks = directorNested.failed_tasks ?? metricValue(metrics, "tasks_failed") ?? 0;
    const activeTasks = directorNested.active_tasks ?? deriveOutstandingTasks(metrics, completedTasks, failedTasks) ?? 0;
    const queueDepth = directorNested.queue_depth ?? deriveOutstandingTasks(metrics, completedTasks, failedTasks) ?? 0;
    const stateToken = normalizeDirectorStateToken(
      directorNested.execution_state || directorNested.state || serviceStatus?.execution_state || serviceStatus?.state,
      Boolean(directorNested.running) || Boolean(serviceStatus?.is_running) || activeTasks > 0
    );
    const running = stateToken === "running" || stateToken === "recovering";
    const phase = normalizeDirectorPhase(directorNested.phase || stateToken);

    return {
      running,
      active_tasks: activeTasks,
      completed_tasks: completedTasks,
      failed_tasks: failedTasks,
      phase,
      current_run_id: directorNested.current_run_id
        || serviceStatus?.current_run_id
        || serviceStatus?.run_id
        || serviceStatus?.id
        || null,
      queue_depth: queueDepth,
      last_updated: new Date().toISOString(),
    };
  }

  return null;
}

/**
 * Normalize workflow status from runtime status event.
 */
function normalizeWorkflowStatus(event: RuntimeStatusEvent): WorkflowStatus | null {
  const snapshot = event.snapshot;
  if (snapshot && typeof snapshot === 'object') {
    const rawTasks = snapshot.tasks || [];
    const tasks: WorkflowTask[] = rawTasks.map((t, index) => {
      const status = String(t.status ?? '').toLowerCase();
      return {
        id: t.id || `task-${index}`,
        title: t.title || t.name || t.subject || t.goal || t.id || '未命名任务',
        status: normalizeTaskStatus(status),
        assignee: t.assignee,
        priority: normalizePriority(t.priority),
        started_at: undefined,
        completed_at: undefined,
        metadata: t.metadata,
        blueprint_id: t.blueprint_id,
        blueprint_path: t.blueprint_path,
        runtime_blueprint_path: t.runtime_blueprint_path,
        acceptance: t.acceptance,
        acceptance_criteria: t.acceptance_criteria,
        execution_checklist: t.execution_checklist,
        target_files: t.target_files,
        scope_paths: t.scope_paths,
        files: t.files,
        dependencies: t.dependencies,
        blocked_by: t.blocked_by,
      };
    });

    const completedTasks = tasks.filter((t) => t.status === "completed" || t.status === "success").length;
    const failedTasks = tasks.filter((t) => t.status === "failed").length;
    const totalTasks = tasks.length;
    const progressPercentage = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;

    return {
      loaded: Boolean(snapshot.run_id) || tasks.length > 0,
      run_id: snapshot.run_id || null,
      tasks,
      completed_at: snapshot.timestamp ? new Date(snapshot.timestamp).toISOString() : null,
      metadata: {
        total_tasks: totalTasks,
        completed_tasks: completedTasks,
        failed_tasks: failedTasks,
        progress_percentage: snapshot.progress ?? progressPercentage,
      },
    };
  }
  return null;
}

/**
 * Normalize engine status from runtime status event.
 */
function normalizeEngineStatus(event: RuntimeStatusEvent): EngineStatus | null {
  const engineNested = event.engine_status;
  if (engineNested && typeof engineNested === 'object') {
    return {
      available: true,
      version: engineNested.version,
      mode: normalizeEngineMode(engineNested.mode),
      health: normalizeHealthStatus(engineNested.health),
      last_check: new Date().toISOString(),
    };
  }
  return null;
}

/**
 * Normalize PM phase string to PMPhase type
 */
function normalizePMPhase(phase: string | undefined): PMPhase {
  const validPhases: PMPhase[] = ["idle", "planning", "dispatching", "completed", "error", "paused"];
  const normalized = String(phase || "").toLowerCase().trim();
  return validPhases.includes(normalized as PMPhase) ? (normalized as PMPhase) : "idle";
}

/**
 * Normalize Director phase string to DirectorPhase type
 */
function normalizeDirectorPhase(phase: string | undefined): DirectorPhase {
  const validPhases: DirectorPhase[] = ["idle", "running", "completed", "error", "paused", "recovering"];
  const normalized = String(phase || "").toLowerCase().trim();
  return validPhases.includes(normalized as DirectorPhase) ? (normalized as DirectorPhase) : "idle";
}

/**
 * Normalize task status string to TaskStatus type
 */
function normalizeTaskStatus(status: string | undefined): TaskStatus {
  const validStatuses: TaskStatus[] = ["pending", "in_progress", "completed", "success", "blocked", "failed", "cancelled"];
  const normalized = String(status || "").toLowerCase().trim();

  // Handle common variations
  if (normalized === "in progress" || normalized === "in-progress" || normalized === "running") {
    return "in_progress";
  }
  if (normalized === "done" || normalized === "success") {
    return "success";
  }
  if (normalized === "error" || normalized === "failure") {
    return "failed";
  }
  if (normalized === "blocked") {
    return "blocked";
  }
  if (normalized === "canceled") {
    return "cancelled";
  }

  return validStatuses.includes(normalized as TaskStatus) ? (normalized as TaskStatus) : "pending";
}

/**
 * Normalize priority string
 */
function normalizePriority(priority: string | undefined): "low" | "medium" | "high" | "critical" | undefined {
  if (!priority) return undefined;

  const normalized = String(priority).toLowerCase().trim();
  const validPriorities: Array<"low" | "medium" | "high" | "critical"> = ["low", "medium", "high", "critical"];

  return validPriorities.includes(normalized as "low" | "medium" | "high" | "critical")
    ? (normalized as "low" | "medium" | "high" | "critical")
    : undefined;
}

/**
 * Normalize engine mode string
 */
function normalizeEngineMode(mode: string | undefined): "local" | "remote" | "hybrid" {
  const normalized = String(mode || "").toLowerCase().trim();
  if (normalized === "remote") return "remote";
  if (normalized === "hybrid") return "hybrid";
  return "local";
}

/**
 * Normalize health status string
 */
function normalizeHealthStatus(health: string | undefined): "healthy" | "degraded" | "unhealthy" | "unknown" {
  const normalized = String(health || "").toLowerCase().trim();
  if (normalized === "healthy" || normalized === "ok" || normalized === "good") return "healthy";
  if (normalized === "degraded" || normalized === "warning") return "degraded";
  if (normalized === "unhealthy" || normalized === "error" || normalized === "bad") return "unhealthy";
  return "unknown";
}

/**
 * Calculate progress percentage from tasks
 */
function calculateProgress(tasks: WorkflowTask[]): number {
  if (tasks.length === 0) return 0;
  const completed = tasks.filter((t) => t.status === "completed" || t.status === "success").length;
  return Math.round((completed / tasks.length) * 100);
}

// ============================================================================
// Projection Helpers
// ============================================================================

/**
 * Create an empty projection for initialization
 */
export function createEmptyProjection(reason = "empty_runtime_projection"): RuntimeProjectionPayload {
  const generatedAt = new Date().toISOString();
  return attachProjectionProvenance({
    pm: null,
    director: null,
    workflow: null,
    engine: null,
    generated_at: generatedAt,
  }, createProjectionProvenance({
    source: "empty",
    transformed: false,
    receivedAt: generatedAt,
    reason,
  }));
}

/**
 * Merge two projections, with update taking precedence
 *
 * @param base - Base projection
 * @param update - Update to apply
 * @returns Merged projection
 */
export function mergeProjections(
  base: RuntimeProjectionPayload,
  update: Partial<RuntimeProjectionPayload>
): RuntimeProjectionPayload {
  const generatedAt = update.generated_at || base.generated_at;
  const provenance =
    update.provenance ||
    base.provenance ||
    createProjectionProvenance({
      source: "merged",
      transformed: false,
      receivedAt: generatedAt,
      reason: "projection_merge_without_source",
    });
  return {
    ...base,
    ...update,
    projection_source: update.projection_source || base.projection_source || provenance.source,
    provenance,
    // Keep the most recent generated_at if not explicitly provided
    generated_at: generatedAt,
  };
}

/**
 * Create a projection from partial data (useful for testing)
 */
export function createPartialProjection(
  partial: Partial<RuntimeProjectionPayload>
): RuntimeProjectionPayload {
  const generatedAt = partial.generated_at || new Date().toISOString();
  return mergeProjections(createEmptyProjection(), {
    projection_source: "partial",
    provenance: createProjectionProvenance({
      source: "partial",
      transformed: false,
      receivedAt: generatedAt,
      reason: "partial_runtime_projection",
    }),
    generated_at: generatedAt,
    ...partial,
  });
}
