/**
 * Runtime Projection Compatibility Layer
 * Normalizes various response formats to canonical RuntimeProjectionPayload
 *
 * This module provides compatibility functions to convert legacy response formats
 * into the canonical RuntimeProjectionPayload shape. It supports gradual migration
 * of backend services to the canonical contract.
 */

import {
  RuntimeProjectionPayload,
  PMLocalStatus,
  DirectorLocalStatus,
  WorkflowStatus,
  EngineStatus,
  SnapshotCompatFields,
  WorkflowTask,
  PMPhase,
  DirectorPhase,
  TaskStatus,
  EngineRoleStatus,
} from "./projection";

// ============================================================================
// Legacy Response Types
// ============================================================================

/**
 * Legacy PM response format
 */
interface LegacyPMResponse {
  pm_status?: string;
  pm_current_task?: string | null;
  pm_running?: boolean;
  pm_phase?: string;
  pm_progress?: number;
  pm_message?: string;
}

/**
 * Legacy Director response format
 */
interface LegacyDirectorResponse {
  director_status?: string;
  director_active?: number;
  director_running?: boolean;
  director_phase?: string;
  director_completed?: number;
  director_failed?: number;
  director_run_id?: string;
  director_queue_depth?: number;
}

/**
 * Legacy Workflow response format
 */
interface LegacyWorkflowResponse {
  workflow_loaded?: boolean;
  workflow_tasks?: number;
  workflow_run_id?: string;
  workflow_completed_at?: string;
  tasks?: Array<{
    id?: string;
    title?: string;
    name?: string;
    status?: string;
    assignee?: string;
    priority?: string;
    started_at?: string;
    completed_at?: string;
  }>;
}

/**
 * Legacy Engine response format
 */
interface LegacyEngineResponse {
  engine_available?: boolean;
  engine_version?: string;
  engine_mode?: string;
  engine_health?: string;
  engine_last_check?: string;
}

/**
 * Nested status format (from WebSocket message)
 */
interface NestedStatusResponse {
  pm_status?: {
    running?: boolean;
    phase?: string;
    current_task_id?: string;
    progress?: number;
    message?: string;
  } | null;
  director_status?: {
    running?: boolean;
    phase?: string;
    active_tasks?: number;
    completed_tasks?: number;
    failed_tasks?: number;
    current_run_id?: string;
    queue_depth?: number;
  } | null;
  snapshot?: {
    run_id?: string;
    tasks?: Array<{
      id?: string;
      title?: string;
      name?: string;
      goal?: string;
      status?: string;
      assignee?: string;
      priority?: string;
      done?: boolean;
      completed?: boolean;
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

/**
 * Combined legacy response type
 */
type LegacyResponse = LegacyPMResponse &
  LegacyDirectorResponse &
  LegacyWorkflowResponse &
  LegacyEngineResponse;

// ============================================================================
// Compatibility Functions
// ============================================================================

/**
 * Convert any response format to canonical RuntimeProjectionPayload
 *
 * @param response - Response object from backend (any format)
 * @returns Canonical RuntimeProjectionPayload
 */
export function toCanonicalProjection(response: unknown): RuntimeProjectionPayload {
  // Handle null/undefined
  if (!response) {
    return createEmptyProjection();
  }

  // Already canonical
  if (isCanonicalProjection(response)) {
    return response as RuntimeProjectionPayload;
  }

  // Check for nested WebSocket format (pm_status, director_status, snapshot as objects)
  const nested = response as NestedStatusResponse;
  const hasNestedFormat =
    (nested.pm_status && typeof nested.pm_status === 'object') ||
    (nested.director_status && typeof nested.director_status === 'object') ||
    (nested.snapshot && typeof nested.snapshot === 'object') ||
    (nested.engine_status && typeof nested.engine_status === 'object');

  // Legacy format conversion
  const legacy = response as LegacyResponse;

  return {
    pm: normalizePMStatus(legacy, hasNestedFormat ? nested : undefined),
    director: normalizeDirectorStatus(legacy, hasNestedFormat ? nested : undefined),
    workflow: normalizeWorkflowStatus(legacy, hasNestedFormat ? nested : undefined),
    engine: normalizeEngineStatus(legacy, hasNestedFormat ? nested : undefined),
    snapshot_compat: extractCompatFields(legacy, hasNestedFormat ? nested : undefined),
    generated_at: new Date().toISOString(),
  };
}

/**
 * Check if response is already in canonical format
 */
function isCanonicalProjection(response: unknown): boolean {
  return (
    response !== null &&
    typeof response === "object" &&
    "snapshot_compat" in response &&
    "generated_at" in response
  );
}

/**
 * Normalize PM status from legacy format
 */
function normalizePMStatus(legacy: LegacyResponse, nested?: NestedStatusResponse): PMLocalStatus | null {
  // Check nested format first (WebSocket message)
  const pmNested = nested?.pm_status;
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

  // Fall back to flat format
  if (!legacy.pm_status && !legacy.pm_running && !legacy.pm_phase) {
    return null;
  }

  return {
    running: legacy.pm_running || legacy.pm_status === "running",
    current_task_id: legacy.pm_current_task || null,
    phase: normalizePMPhase(legacy.pm_phase || legacy.pm_status),
    progress: legacy.pm_progress,
    message: legacy.pm_message,
    last_updated: new Date().toISOString(),
  };
}

/**
 * Normalize Director status from legacy format
 */
function normalizeDirectorStatus(legacy: LegacyResponse, nested?: NestedStatusResponse): DirectorLocalStatus | null {
  // Check nested format first (WebSocket message)
  const directorNested = nested?.director_status;
  if (directorNested && typeof directorNested === 'object') {
    return {
      running: Boolean(directorNested.running),
      active_tasks: directorNested.active_tasks || 0,
      completed_tasks: directorNested.completed_tasks || 0,
      failed_tasks: directorNested.failed_tasks || 0,
      phase: normalizeDirectorPhase(directorNested.phase),
      current_run_id: directorNested.current_run_id || null,
      queue_depth: directorNested.queue_depth || 0,
      last_updated: new Date().toISOString(),
    };
  }

  // Fall back to flat format
  if (!legacy.director_status && !legacy.director_running) {
    return null;
  }

  return {
    running: legacy.director_running || legacy.director_status === "running",
    active_tasks: legacy.director_active || 0,
    completed_tasks: legacy.director_completed || 0,
    failed_tasks: legacy.director_failed || 0,
    phase: normalizeDirectorPhase(legacy.director_phase || legacy.director_status),
    current_run_id: legacy.director_run_id || null,
    queue_depth: legacy.director_queue_depth || 0,
    last_updated: new Date().toISOString(),
  };
}

/**
 * Normalize Workflow status from legacy format
 */
function normalizeWorkflowStatus(legacy: LegacyResponse, nested?: NestedStatusResponse): WorkflowStatus | null {
  // Check nested format first (WebSocket message)
  const snapshot = nested?.snapshot;
  if (snapshot && typeof snapshot === 'object') {
    const rawTasks = snapshot.tasks || [];
    const tasks: WorkflowTask[] = rawTasks.map((t, index) => {
      const status = String(t.status ?? '').toLowerCase();
      return {
        id: t.id || `task-${index}`,
        title: t.title || t.name || t.goal || `Task ${index}`,
        status: normalizeTaskStatus(status),
        assignee: t.assignee,
        priority: normalizePriority(t.priority),
        started_at: undefined,
        completed_at: undefined,
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

  // Fall back to flat format
  if (!legacy.workflow_loaded && !legacy.tasks) {
    return null;
  }

  const tasks: WorkflowTask[] =
    legacy.tasks?.map((t, index) => ({
      id: t.id || `task-${index}`,
      title: t.title || t.name || `Task ${index}`,
      status: normalizeTaskStatus(t.status),
      assignee: t.assignee,
      priority: normalizePriority(t.priority),
      started_at: t.started_at,
      completed_at: t.completed_at,
    })) || [];

  return {
    loaded: legacy.workflow_loaded || tasks.length > 0,
    run_id: legacy.workflow_run_id || null,
    tasks,
    completed_at: legacy.workflow_completed_at || null,
    metadata: {
      total_tasks: tasks.length,
      completed_tasks: tasks.filter((t) => t.status === "completed" || t.status === "success").length,
      failed_tasks: tasks.filter((t) => t.status === "failed").length,
      progress_percentage: calculateProgress(tasks),
    },
  };
}

/**
 * Normalize Engine status from legacy format
 */
function normalizeEngineStatus(legacy: LegacyResponse, nested?: NestedStatusResponse): EngineStatus | null {
  // Check nested format first (WebSocket message)
  const engineNested = nested?.engine_status;
  if (engineNested && typeof engineNested === 'object') {
    return {
      available: true,
      version: engineNested.version,
      mode: normalizeEngineMode(engineNested.mode),
      health: normalizeHealthStatus(engineNested.health),
      last_check: new Date().toISOString(),
    };
  }

  // Fall back to flat format
  if (!legacy.engine_available && !legacy.engine_version) {
    return null;
  }

  return {
    available: legacy.engine_available || false,
    version: legacy.engine_version,
    mode: normalizeEngineMode(legacy.engine_mode),
    health: normalizeHealthStatus(legacy.engine_health),
    last_check: legacy.engine_last_check || new Date().toISOString(),
  };
}

/**
 * Extract compatibility fields from legacy response
 */
function extractCompatFields(legacy: LegacyResponse, nested?: NestedStatusResponse): SnapshotCompatFields {
  const compat: SnapshotCompatFields = {};

  // Flat format
  if (legacy.pm_status !== undefined) compat.pm_status = legacy.pm_status;
  if (legacy.pm_current_task !== undefined) compat.pm_current_task = legacy.pm_current_task;
  if (legacy.director_status !== undefined) compat.director_status = legacy.director_status;
  if (legacy.director_active !== undefined) compat.director_active = legacy.director_active;
  if (legacy.workflow_loaded !== undefined) compat.workflow_loaded = legacy.workflow_loaded;
  if (legacy.workflow_tasks !== undefined) compat.workflow_tasks = legacy.workflow_tasks;

  // Nested format (WebSocket)
  if (nested?.pm_status) {
    compat.pm_status = nested.pm_status.phase || 'idle';
  }
  if (nested?.director_status) {
    compat.director_status = nested.director_status.phase || 'idle';
  }
  if (nested?.snapshot) {
    compat.workflow_loaded = Boolean(nested.snapshot.run_id);
    compat.workflow_tasks = nested.snapshot.tasks?.length;
  }
  if (nested?.engine_status) {
    compat.engine_roles = nested.engine_status.roles as Record<string, EngineRoleStatus> | undefined;
    compat.engine_error = nested.engine_status.error;
    compat.engine_summary = nested.engine_status.summary;
    compat.engine_run_id = nested.engine_status.run_id;
  }

  return compat;
}

// ============================================================================
// Normalization Helpers
// ============================================================================

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
// Migration Helpers
// ============================================================================

/**
 * Create an empty projection for initialization
 */
export function createEmptyProjection(): RuntimeProjectionPayload {
  return {
    pm: null,
    director: null,
    workflow: null,
    engine: null,
    snapshot_compat: {},
    generated_at: new Date().toISOString(),
  };
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
  return {
    ...base,
    ...update,
    snapshot_compat: {
      ...base.snapshot_compat,
      ...update.snapshot_compat,
    },
    // Keep the most recent generated_at if not explicitly provided
    generated_at: update.generated_at || base.generated_at,
  };
}

/**
 * Create a projection from partial data (useful for testing)
 */
export function createPartialProjection(
  partial: Partial<RuntimeProjectionPayload>
): RuntimeProjectionPayload {
  return mergeProjections(createEmptyProjection(), partial);
}

// ============================================================================
// Legacy Format Detectors
// ============================================================================

/**
 * Detect if response is in legacy PM format
 */
export function isLegacyPMFormat(response: unknown): boolean {
  if (!response || typeof response !== "object") return false;
  const obj = response as Record<string, unknown>;
  return "pm_status" in obj || "pm_running" in obj || "pm_phase" in obj;
}

/**
 * Detect if response is in legacy Director format
 */
export function isLegacyDirectorFormat(response: unknown): boolean {
  if (!response || typeof response !== "object") return false;
  const obj = response as Record<string, unknown>;
  return "director_status" in obj || "director_active" in obj || "director_running" in obj;
}

/**
 * Detect if response is in legacy Workflow format
 */
export function isLegacyWorkflowFormat(response: unknown): boolean {
  if (!response || typeof response !== "object") return false;
  const obj = response as Record<string, unknown>;
  return "workflow_loaded" in obj || "workflow_tasks" in obj;
}
