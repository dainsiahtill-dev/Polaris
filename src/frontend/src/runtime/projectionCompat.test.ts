/**
 * Tests for Runtime Projection Compatibility Layer
 */

import { describe, it, expect } from 'vitest';
import {
  toCanonicalProjection,
  createEmptyProjection,
  createPartialProjection,
  mergeProjections,
  isLegacyPMFormat,
  isLegacyDirectorFormat,
  isLegacyWorkflowFormat,
} from './projectionCompat';
import { RuntimeProjectionPayload } from './projection';

describe('Runtime Projection Compatibility', () => {
  describe('toCanonicalProjection', () => {
    it('should return empty projection for null input', () => {
      const result = toCanonicalProjection(null);
      expect(result.pm).toBeNull();
      expect(result.director).toBeNull();
      expect(result.workflow).toBeNull();
      expect(result.engine).toBeNull();
      expect(result.snapshot_compat).toEqual({});
      expect(result.generated_at).toBeDefined();
    });

    it('should return empty projection for undefined input', () => {
      const result = toCanonicalProjection(undefined);
      expect(result.pm).toBeNull();
      expect(result.director).toBeNull();
    });

    it('should pass through canonical projection unchanged', () => {
      const canonical: RuntimeProjectionPayload = {
        pm: {
          running: true,
          current_task_id: 'task-1',
          phase: 'planning',
          last_updated: '2024-01-01T00:00:00Z',
        },
        director: null,
        workflow: null,
        engine: null,
        snapshot_compat: { legacy_field: 'value' },
        generated_at: '2024-01-01T00:00:00Z',
      };

      const result = toCanonicalProjection(canonical);
      expect(result).toEqual(canonical);
    });

    it('should convert legacy PM format', () => {
      const legacy = {
        pm_status: 'running',
        pm_current_task: 'task-123',
        pm_running: true,
        pm_phase: 'planning',
      };

      const result = toCanonicalProjection(legacy);
      expect(result.pm).toEqual({
        running: true,
        current_task_id: 'task-123',
        phase: 'planning',
        last_updated: expect.any(String),
      });
    });

    it('should convert legacy Director format', () => {
      const legacy = {
        director_status: 'running',
        director_active: 5,
        director_running: true,
        director_phase: 'running',
        director_completed: 10,
        director_failed: 2,
        director_run_id: 'run-123',
        director_queue_depth: 3,
      };

      const result = toCanonicalProjection(legacy);
      expect(result.director).toEqual({
        running: true,
        active_tasks: 5,
        completed_tasks: 10,
        failed_tasks: 2,
        phase: 'running',
        current_run_id: 'run-123',
        queue_depth: 3,
        last_updated: expect.any(String),
      });
    });

    it('should convert legacy Workflow format', () => {
      const legacy = {
        workflow_loaded: true,
        workflow_run_id: 'run-456',
        workflow_completed_at: '2024-01-01T12:00:00Z',
        tasks: [
          { id: '1', title: 'Task 1', status: 'completed' },
          { id: '2', name: 'Task 2', status: 'in_progress' },
        ],
      };

      const result = toCanonicalProjection(legacy);
      expect(result.workflow).toEqual({
        loaded: true,
        run_id: 'run-456',
        tasks: [
          { id: '1', title: 'Task 1', status: 'completed' },
          { id: '2', title: 'Task 2', status: 'in_progress' },
        ],
        completed_at: '2024-01-01T12:00:00Z',
        metadata: {
          total_tasks: 2,
          completed_tasks: 1,
          failed_tasks: 0,
          progress_percentage: 50,
        },
      });
    });

    it('should convert legacy Engine format', () => {
      const legacy = {
        engine_available: true,
        engine_version: '1.0.0',
        engine_mode: 'local',
        engine_health: 'healthy',
        engine_last_check: '2024-01-01T00:00:00Z',
      };

      const result = toCanonicalProjection(legacy);
      expect(result.engine).toEqual({
        available: true,
        version: '1.0.0',
        mode: 'local',
        health: 'healthy',
        last_check: '2024-01-01T00:00:00Z',
      });
    });

    it('should extract compat fields from legacy response', () => {
      const legacy = {
        pm_status: 'running',
        pm_current_task: 'task-1',
        director_status: 'idle',
        director_active: 0,
        workflow_loaded: true,
        workflow_tasks: 5,
      };

      const result = toCanonicalProjection(legacy);
      expect(result.snapshot_compat).toEqual({
        pm_status: 'running',
        pm_current_task: 'task-1',
        director_status: 'idle',
        director_active: 0,
        workflow_loaded: true,
        workflow_tasks: 5,
      });
    });

    it('should normalize task status variations', () => {
      const legacy = {
        tasks: [
          { id: '1', title: 'Task 1', status: 'in progress' },
          { id: '2', title: 'Task 2', status: 'running' },
          { id: '3', title: 'Task 3', status: 'done' },
          { id: '4', title: 'Task 4', status: 'error' },
          { id: '5', title: 'Task 5', status: 'canceled' },
        ],
      };

      const result = toCanonicalProjection(legacy);
      expect(result.workflow?.tasks[0].status).toBe('in_progress');
      expect(result.workflow?.tasks[1].status).toBe('in_progress');
      expect(result.workflow?.tasks[2].status).toBe('success');
      expect(result.workflow?.tasks[3].status).toBe('failed');
      expect(result.workflow?.tasks[4].status).toBe('cancelled');
    });

    it('should normalize nested websocket payloads into the canonical projection', () => {
      const websocketPayload = {
        pm_status: {
          running: true,
          phase: 'planning',
          current_task_id: 'pm-task-1',
          progress: 30,
          message: 'planning',
        },
        director_status: {
          running: true,
          phase: 'running',
          active_tasks: 2,
          completed_tasks: 1,
          failed_tasks: 0,
          current_run_id: 'director-run-1',
          queue_depth: 3,
        },
        snapshot: {
          run_id: 'workflow-run-1',
          timestamp: '2024-01-02T00:00:00Z',
          progress: 75,
          tasks: [
            { id: 'task-1', title: 'Write docs', status: 'done' },
            { id: 'task-2', goal: 'Ship feature', status: 'in progress' },
          ],
        },
        engine_status: {
          version: '2.0.0',
          mode: 'hybrid',
          health: 'healthy',
          roles: {
            Director: { status: 'running', task_id: 'task-2' },
          },
          error: 'none',
          summary: { total: 2, failures: 0 },
          run_id: 'engine-run-1',
        },
      };

      const result = toCanonicalProjection(websocketPayload);

      expect(result.pm?.current_task_id).toBe('pm-task-1');
      expect(result.director?.current_run_id).toBe('director-run-1');
      expect(result.workflow?.run_id).toBe('workflow-run-1');
      expect(result.workflow?.tasks[0].status).toBe('success');
      expect(result.workflow?.tasks[1].status).toBe('in_progress');
      expect(result.engine?.mode).toBe('hybrid');
      expect(result.snapshot_compat.engine_run_id).toBe('engine-run-1');
      expect(result.snapshot_compat.engine_roles).toEqual({
        Director: { status: 'running', task_id: 'task-2' },
      });
    });
  });

  describe('createEmptyProjection', () => {
    it('should create projection with all null components', () => {
      const result = createEmptyProjection();
      expect(result.pm).toBeNull();
      expect(result.director).toBeNull();
      expect(result.workflow).toBeNull();
      expect(result.engine).toBeNull();
      expect(result.snapshot_compat).toEqual({});
      expect(result.generated_at).toBeDefined();
    });
  });

  describe('createPartialProjection', () => {
    it('should merge partial data with empty projection', () => {
      const partial = {
        pm: {
          running: true,
          current_task_id: 'task-1',
          phase: 'planning' as const,
          last_updated: '2024-01-01T00:00:00Z',
        },
      };

      const result = createPartialProjection(partial);
      expect(result.pm).toEqual(partial.pm);
      expect(result.director).toBeNull();
      expect(result.workflow).toBeNull();
    });
  });

  describe('mergeProjections', () => {
    it('should merge update into base', () => {
      const base: RuntimeProjectionPayload = {
        pm: null,
        director: {
          running: true,
          active_tasks: 1,
          completed_tasks: 0,
          failed_tasks: 0,
          phase: 'running',
          current_run_id: null,
          queue_depth: 0,
          last_updated: '2024-01-01T00:00:00Z',
        },
        workflow: null,
        engine: null,
        snapshot_compat: { old: 'value' },
        generated_at: '2024-01-01T00:00:00Z',
      };

      const update: Partial<RuntimeProjectionPayload> = {
        pm: {
          running: true,
          current_task_id: 'task-1',
          phase: 'planning',
          last_updated: '2024-01-02T00:00:00Z',
        },
        snapshot_compat: { new: 'value' },
      };

      const result = mergeProjections(base, update);
      expect(result.pm).toEqual(update.pm);
      expect(result.director).toEqual(base.director);
      expect(result.snapshot_compat).toEqual({ old: 'value', new: 'value' });
    });

    it('should preserve base generated_at if not provided in update', () => {
      const base = createEmptyProjection();
      base.generated_at = '2024-01-01T00:00:00Z';

      const update: Partial<RuntimeProjectionPayload> = {
        pm: {
          running: true,
          current_task_id: null,
          phase: 'planning',
          last_updated: '2024-01-02T00:00:00Z',
        },
      };

      const result = mergeProjections(base, update);
      expect(result.generated_at).toBe('2024-01-01T00:00:00Z');
    });
  });

  describe('Legacy Format Detectors', () => {
    it('should detect legacy PM format', () => {
      expect(isLegacyPMFormat({ pm_status: 'running' })).toBe(true);
      expect(isLegacyPMFormat({ pm_running: true })).toBe(true);
      expect(isLegacyPMFormat({ pm_phase: 'planning' })).toBe(true);
      expect(isLegacyPMFormat({ director_status: 'running' })).toBe(false);
      expect(isLegacyPMFormat(null)).toBe(false);
    });

    it('should detect legacy Director format', () => {
      expect(isLegacyDirectorFormat({ director_status: 'running' })).toBe(true);
      expect(isLegacyDirectorFormat({ director_active: 5 })).toBe(true);
      expect(isLegacyDirectorFormat({ director_running: true })).toBe(true);
      expect(isLegacyDirectorFormat({ pm_status: 'running' })).toBe(false);
      expect(isLegacyDirectorFormat(null)).toBe(false);
    });

    it('should detect legacy Workflow format', () => {
      expect(isLegacyWorkflowFormat({ workflow_loaded: true })).toBe(true);
      expect(isLegacyWorkflowFormat({ workflow_tasks: 5 })).toBe(true);
      expect(isLegacyWorkflowFormat({ pm_status: 'running' })).toBe(false);
      expect(isLegacyWorkflowFormat(null)).toBe(false);
    });
  });
});
