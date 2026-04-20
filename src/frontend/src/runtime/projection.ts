/**
 * Runtime Projection Types - Canonical contract for runtime state
 * Single source of truth for frontend runtime state consumption
 *
 * This module defines the canonical contract between backend runtime projection
 * and frontend state consumption. All runtime state should flow through this
 * contract to ensure consistency across the application.
 */

// ============================================================================
// Core Runtime Projection
// ============================================================================

/**
 * Main Runtime Projection Payload
 * This is the single source of truth for all runtime state consumed by the frontend.
 * Backend services should produce this shape, frontend components should consume it.
 */
export interface RuntimeProjectionPayload {
  /** PM (Project Manager) local status */
  pm: PMLocalStatus | null;
  /** Director local status */
  director: DirectorLocalStatus | null;
  /** Workflow archive status */
  workflow: WorkflowStatus | null;
  /** Engine fallback status (for backward compatibility) */
  engine: EngineStatus | null;
  /** Backward compatibility fields for legacy consumers */
  snapshot_compat: SnapshotCompatFields;
  /** ISO timestamp when this projection was generated */
  generated_at: string;
}

// ============================================================================
// PM Local Status
// ============================================================================

/**
 * PM (Project Manager) local runtime status
 * Represents the current state of the PM orchestration loop
 */
export interface PMLocalStatus {
  /** Whether PM is currently running */
  running: boolean;
  /** Current task ID being processed, null if idle */
  current_task_id: string | null;
  /** Current PM phase */
  phase: PMPhase;
  /** Optional progress percentage (0-100) */
  progress?: number;
  /** Optional human-readable status message */
  message?: string;
  /** ISO timestamp of last status update */
  last_updated: string;
}

/**
 * PM operation phases
 */
export type PMPhase =
  | "idle"
  | "planning"
  | "dispatching"
  | "completed"
  | "error"
  | "paused";

// ============================================================================
// Director Local Status
// ============================================================================

/**
 * Director local runtime status
 * Represents the current state of the Director execution engine
 */
export interface DirectorLocalStatus {
  /** Whether Director is currently running */
  running: boolean;
  /** Number of currently active tasks */
  active_tasks: number;
  /** Number of completed tasks in current run */
  completed_tasks: number;
  /** Number of failed tasks in current run */
  failed_tasks: number;
  /** Current Director phase */
  phase: DirectorPhase;
  /** Current run ID, null if not running */
  current_run_id: string | null;
  /** Number of tasks waiting in queue */
  queue_depth: number;
  /** ISO timestamp of last status update */
  last_updated: string;
}

/**
 * Director operation phases
 */
export type DirectorPhase =
  | "idle"
  | "running"
  | "completed"
  | "error"
  | "paused"
  | "recovering";

// ============================================================================
// Workflow Archive Status
// ============================================================================

/**
 * Workflow archive status
 * Represents the state of a persisted workflow run
 */
export interface WorkflowStatus {
  /** Whether a workflow is loaded */
  loaded: boolean;
  /** Run ID of the loaded workflow */
  run_id: string | null;
  /** Tasks in the workflow */
  tasks: WorkflowTask[];
  /** ISO timestamp when workflow completed, null if not completed */
  completed_at: string | null;
  /** Optional workflow metadata */
  metadata?: WorkflowMetadata;
}

/**
 * Individual workflow task
 */
export interface WorkflowTask {
  /** Task ID */
  id: string;
  /** Task title/name */
  title: string;
  /** Current task status */
  status: TaskStatus;
  /** Optional assignee role */
  assignee?: string;
  /** Task priority */
  priority?: "low" | "medium" | "high" | "critical";
  /** ISO timestamp when task started */
  started_at?: string;
  /** ISO timestamp when task completed */
  completed_at?: string;
}

/**
 * Task status values
 */
export type TaskStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "success"
  | "blocked"
  | "failed"
  | "cancelled";

/**
 * Workflow metadata
 */
export interface WorkflowMetadata {
  /** Total number of tasks */
  total_tasks: number;
  /** Number of completed tasks */
  completed_tasks: number;
  /** Number of failed tasks */
  failed_tasks: number;
  /** Overall progress percentage (0-100) */
  progress_percentage: number;
}

// ============================================================================
// Engine Fallback Status
// ============================================================================

/**
 * Engine fallback status
 * Used for backward compatibility with legacy engine-based status reporting
 */
export interface EngineStatus {
  /** Whether engine is available */
  available: boolean;
  /** Engine version string */
  version?: string;
  /** Engine operation mode */
  mode: "local" | "remote" | "hybrid";
  /** Engine health status */
  health: "healthy" | "degraded" | "unhealthy" | "unknown";
  /** ISO timestamp of last health check */
  last_check: string;
}

// ============================================================================
// Backward Compatibility Fields
// ============================================================================

/**
 * Snapshot compatibility fields
 * These fields support legacy consumers during migration period.
 * @deprecated Use canonical fields (pm, director, workflow) instead
 */
export interface SnapshotCompatFields {
  /** Legacy PM status string */
  pm_status?: string;
  /** Legacy PM current task */
  pm_current_task?: string | null;
  /** Legacy Director status string */
  director_status?: string;
  /** Legacy Director active task count */
  director_active?: number;
  /** Legacy workflow loaded flag */
  workflow_loaded?: boolean;
  /** Legacy workflow task count */
  workflow_tasks?: number;
  /** Legacy engine roles status */
  engine_roles?: Record<string, EngineRoleStatus>;
  /** Legacy engine error message */
  engine_error?: string;
  /** Legacy engine summary data */
  engine_summary?: Record<string, unknown>;
  /** Legacy engine run id */
  engine_run_id?: string;
  /** Allow additional legacy fields */
  [key: string]: unknown;
}

/**
 * Legacy engine role status (for backward compatibility)
 * @deprecated Use canonical fields in PMLocalStatus/DirectorLocalStatus instead
 */
export interface EngineRoleStatus {
  status?: string;
  running?: boolean;
  task_id?: string;
  task_title?: string;
  detail?: string;
  updated_at?: string;
  meta?: Record<string, unknown>;
}

// ============================================================================
// Selection Helpers
// ============================================================================

/**
 * Select task rows using priority rules:
 * 1. If workflow archive has tasks: use workflow rows
 * 2. If workflow missing + local running: use local live rows
 * 3. All unavailable: fallback to empty
 *
 * @param projection - Runtime projection payload
 * @returns Array of workflow tasks to display
 */
export function selectTaskRows(projection: RuntimeProjectionPayload): WorkflowTask[] {
  // Priority 1: Workflow archive
  if (projection.workflow?.tasks?.length) {
    return projection.workflow.tasks;
  }

  // Priority 2: Director local tasks (if represented as WorkflowTasks)
  if (projection.director?.active_tasks && projection.director.active_tasks > 0) {
    // Convert director active tasks to workflow task format
    return [{
      id: `director-${projection.director.current_run_id || "active"}`,
      title: "Director Active Tasks",
      status: "in_progress" as TaskStatus,
    }];
  }

  // Fallback: empty
  return [];
}

/**
 * Determine primary system status
 *
 * @param projection - Runtime projection payload
 * @returns Primary status string identifier
 */
export function selectPrimaryStatus(projection: RuntimeProjectionPayload): string {
  if (projection.director?.running) {
    return `director-${projection.director.phase}`;
  }
  if (projection.pm?.running) {
    return `pm-${projection.pm.phase}`;
  }
  if (projection.workflow?.loaded) {
    return "workflow-loaded";
  }
  return "idle";
}

/**
 * Check if any system component is actively running
 *
 * @param projection - Runtime projection payload
 * @returns True if any component is actively running
 */
export function isSystemActive(projection: RuntimeProjectionPayload): boolean {
  return Boolean(
    projection.pm?.running ||
    projection.director?.running ||
    (projection.workflow?.tasks?.some(t => t.status === "in_progress"))
  );
}

/**
 * Get overall system progress percentage
 *
 * @param projection - Runtime projection payload
 * @returns Progress percentage (0-100)
 */
export function selectOverallProgress(projection: RuntimeProjectionPayload): number {
  // Use workflow metadata if available
  if (projection.workflow?.metadata?.progress_percentage !== undefined) {
    return projection.workflow.metadata.progress_percentage;
  }

  // Use PM progress if available
  if (projection.pm?.progress !== undefined) {
    return projection.pm.progress;
  }

  // Calculate from tasks if available
  const tasks = selectTaskRows(projection);
  if (tasks.length > 0) {
    const completed = tasks.filter(t => t.status === "completed" || t.status === "success").length;
    return Math.round((completed / tasks.length) * 100);
  }

  return 0;
}

/**
 * Get the most recent activity timestamp
 *
 * @param projection - Runtime projection payload
 * @returns ISO timestamp of most recent activity, or null if none
 */
export function selectLastActivityTimestamp(projection: RuntimeProjectionPayload): string | null {
  const timestamps: string[] = [];

  if (projection.pm?.last_updated) {
    timestamps.push(projection.pm.last_updated);
  }
  if (projection.director?.last_updated) {
    timestamps.push(projection.director.last_updated);
  }
  if (projection.workflow?.completed_at) {
    timestamps.push(projection.workflow.completed_at);
  }

  if (timestamps.length === 0) {
    return null;
  }

  // Return the most recent timestamp
  return timestamps.sort().reverse()[0];
}

// ============================================================================
// Type Guards
// ============================================================================

/**
 * Type guard for PMPhase
 */
export function isPMPhase(value: unknown): value is PMPhase {
  const validPhases: PMPhase[] = ["idle", "planning", "dispatching", "completed", "error", "paused"];
  return typeof value === "string" && validPhases.includes(value as PMPhase);
}

/**
 * Type guard for DirectorPhase
 */
export function isDirectorPhase(value: unknown): value is DirectorPhase {
  const validPhases: DirectorPhase[] = ["idle", "running", "completed", "error", "paused", "recovering"];
  return typeof value === "string" && validPhases.includes(value as DirectorPhase);
}

/**
 * Type guard for TaskStatus
 */
export function isTaskStatus(value: unknown): value is TaskStatus {
  const validStatuses: TaskStatus[] = ["pending", "in_progress", "completed", "success", "blocked", "failed", "cancelled"];
  return typeof value === "string" && validStatuses.includes(value as TaskStatus);
}

/**
 * Type guard for RuntimeProjectionPayload
 */
export function isRuntimeProjectionPayload(value: unknown): value is RuntimeProjectionPayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as RuntimeProjectionPayload;
  return (
    "snapshot_compat" in payload &&
    "generated_at" in payload &&
    typeof payload.generated_at === "string"
  );
}
