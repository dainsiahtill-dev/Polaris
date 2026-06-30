/**
 * Tests for Runtime Projection Adapter.
 */

import { describe, expect, it } from 'vitest';
import {
  createEmptyProjection,
  createPartialProjection,
  mergeProjections,
  normalizeRuntimeProjection,
} from './projectionAdapter';
import { RuntimeProjectionPayload } from './projection';

describe('Runtime Projection Adapter', () => {
  describe('normalizeRuntimeProjection', () => {
    it('returns an empty projection for null input', () => {
      const result = normalizeRuntimeProjection(null);

      expect(result.pm).toBeNull();
      expect(result.director).toBeNull();
      expect(result.workflow).toBeNull();
      expect(result.engine).toBeNull();
      expect(result.projection_source).toBe('empty');
      expect(result.provenance).toEqual(expect.objectContaining({
        source: 'empty',
        transformed: false,
        adaptation_reason: 'empty_runtime_projection',
      }));
    });

    it('marks a canonical projection with provenance when missing', () => {
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
        generated_at: '2024-01-01T00:00:00Z',
      };

      const result = normalizeRuntimeProjection(canonical);

      expect(result.pm).toEqual(canonical.pm);
      expect(result.projection_source).toBe('canonical');
      expect(result.provenance).toEqual(expect.objectContaining({
        source: 'canonical',
        transformed: false,
        source_schema: 'runtime_projection',
      }));
    });

    it('adapts runtime.v2 status events into canonical projection rows', () => {
      const payload = {
        type: 'status',
        protocol: 'runtime.v2',
        pm_status: {
          running: true,
          phase: 'dispatching',
          current_task_id: 'task-2',
          progress: 55,
          message: 'Dispatching',
        },
        director_status: {
          running: true,
          state: 'running',
          current_run_id: 'run-1',
          status: {
            metrics: {
              tasks_completed: 2,
              tasks_failed: 1,
              tasks_pending: 3,
            },
          },
        },
        snapshot: {
          run_id: 'run-1',
          progress: 50,
          tasks: [
            { id: 'task-1', title: 'Done', status: 'completed', priority: 'high' },
            { id: 'task-2', title: 'Running', status: 'running', priority: 'medium' },
            { id: 'task-3', title: 'Blocked', status: 'blocked', priority: 'low' },
          ],
          timestamp: '2024-01-01T01:00:00Z',
        },
        engine_status: {
          version: '1.0.0',
          mode: 'local',
          health: 'healthy',
          run_id: 'engine-run-1',
          roles: { pm: { state: 'running' } },
        },
      };

      const result = normalizeRuntimeProjection(payload);

      expect(result.projection_source).toBe('runtime_status_event');
      expect(result.provenance).toEqual(expect.objectContaining({
        source: 'runtime_status_event',
        transformed: true,
        source_schema: 'runtime.v2',
        adaptation_reason: 'runtime_v2_status_event',
        source_fields: expect.arrayContaining(['pm_status', 'director_status', 'snapshot', 'engine_status']),
      }));
      expect(result.pm).toEqual(expect.objectContaining({
        running: true,
        current_task_id: 'task-2',
        phase: 'dispatching',
        progress: 55,
      }));
      expect(result.director).toEqual(expect.objectContaining({
        running: true,
        active_tasks: 3,
        completed_tasks: 2,
        failed_tasks: 1,
        current_run_id: 'run-1',
        queue_depth: 3,
      }));
      expect(result.workflow).toEqual(expect.objectContaining({
        loaded: true,
        run_id: 'run-1',
      }));
      expect(result.workflow?.tasks).toHaveLength(3);
      expect(result.workflow?.metadata).toEqual(expect.objectContaining({
        total_tasks: 3,
        completed_tasks: 1,
        progress_percentage: 50,
      }));
      expect(result.engine).toEqual(expect.objectContaining({
        available: true,
        version: '1.0.0',
        health: 'healthy',
      }));
    });

    it('does not guess unsupported flat payloads into runtime projections', () => {
      const result = normalizeRuntimeProjection({
        pm_status: 'running',
        director_running: true,
      });

      expect(result.projection_source).toBe('empty');
      expect(result.pm).toBeNull();
      expect(result.director).toBeNull();
      expect(result.provenance).toEqual(expect.objectContaining({
        adaptation_reason: 'non_projection_runtime_payload',
      }));
    });
  });

  describe('createEmptyProjection', () => {
    it('creates an empty projection with provenance', () => {
      const result = createEmptyProjection();

      expect(result.pm).toBeNull();
      expect(result.director).toBeNull();
      expect(result.workflow).toBeNull();
      expect(result.engine).toBeNull();
      expect(result.projection_source).toBe('empty');
      expect(result.provenance?.source).toBe('empty');
    });
  });

  describe('createPartialProjection', () => {
    it('creates a projection from partial data', () => {
      const result = createPartialProjection({
        pm: {
          running: true,
          current_task_id: 'task-1',
          phase: 'planning',
          last_updated: '2024-01-01T00:00:00Z',
        },
      });

      expect(result.pm?.running).toBe(true);
      expect(result.director).toBeNull();
      expect(result.projection_source).toBe('partial');
    });
  });

  describe('mergeProjections', () => {
    it('merges projections with update taking precedence', () => {
      const base = createEmptyProjection();
      const update: Partial<RuntimeProjectionPayload> = {
        pm: {
          running: true,
          current_task_id: 'task-1',
          phase: 'planning',
          last_updated: '2024-01-01T00:00:00Z',
        },
      };

      const result = mergeProjections(base, update);

      expect(result.pm?.running).toBe(true);
      expect(result.projection_source).toBe('empty');
    });
  });
});
