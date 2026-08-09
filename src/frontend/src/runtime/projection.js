/**
 * Runtime Projection Types - Canonical contract for runtime state
 * Single source of truth for frontend runtime state consumption
 *
 * This module defines the canonical contract between backend runtime projection
 * and frontend state consumption. All runtime state should flow through this
 * contract to ensure consistency across the application.
 */
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
export function selectTaskRows(projection) {
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
                status: "in_progress",
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
export function selectPrimaryStatus(projection) {
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
export function isSystemActive(projection) {
    return Boolean(projection.pm?.running ||
        projection.director?.running ||
        (projection.workflow?.tasks?.some(t => t.status === "in_progress")));
}
/**
 * Get overall system progress percentage
 *
 * @param projection - Runtime projection payload
 * @returns Progress percentage (0-100)
 */
export function selectOverallProgress(projection) {
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
export function selectLastActivityTimestamp(projection) {
    const timestamps = [];
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
export function isPMPhase(value) {
    const validPhases = ["idle", "planning", "dispatching", "completed", "error", "paused"];
    return typeof value === "string" && validPhases.includes(value);
}
/**
 * Type guard for DirectorPhase
 */
export function isDirectorPhase(value) {
    const validPhases = ["idle", "running", "completed", "error", "paused", "recovering"];
    return typeof value === "string" && validPhases.includes(value);
}
/**
 * Type guard for TaskStatus
 */
export function isTaskStatus(value) {
    const validStatuses = ["pending", "in_progress", "completed", "success", "blocked", "failed", "cancelled"];
    return typeof value === "string" && validStatuses.includes(value);
}
/**
 * Type guard for RuntimeProjectionPayload
 */
export function isRuntimeProjectionPayload(value) {
    if (!value || typeof value !== "object")
        return false;
    const payload = value;
    return ("pm" in payload &&
        "director" in payload &&
        "workflow" in payload &&
        "engine" in payload &&
        "generated_at" in payload &&
        typeof payload.generated_at === "string");
}
