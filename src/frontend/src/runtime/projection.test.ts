/**
 * Tests for Runtime Projection types and utilities
 */

import { describe, it, expect } from 'vitest';
import {
  RuntimeProjectionPayload,
  PMLocalStatus,
  DirectorLocalStatus,
  WorkflowStatus,
  WorkflowTask,
  selectTaskRows,
  selectPrimaryStatus,
  isSystemActive,
  selectOverallProgress,
  isRuntimeProjectionPayload,
  isPMPhase,
  isDirectorPhase,
  isTaskStatus,
} from './projection';

describe('Runtime Projection', () => {
  describe('Type Guards', () => {
    it('should identify valid PM phases', () => {
      expect(isPMPhase('idle')).toBe(true);
      expect(isPMPhase('planning')).toBe(true);
      expect(isPMPhase('dispatching')).toBe(true);
      expect(isPMPhase('completed')).toBe(true);
      expect(isPMPhase('error')).toBe(true);
      expect(isPMPhase('paused')).toBe(true);
      expect(isPMPhase('invalid')).toBe(false);
      expect(isPMPhase(null)).toBe(false);
      expect(isPMPhase(undefined)).toBe(false);
    });

    it('should identify valid Director phases', () => {
      expect(isDirectorPhase('idle')).toBe(true);
      expect(isDirectorPhase('running')).toBe(true);
      expect(isDirectorPhase('completed')).toBe(true);
      expect(isDirectorPhase('error')).toBe(true);
      expect(isDirectorPhase('paused')).toBe(true);
      expect(isDirectorPhase('recovering')).toBe(true);
      expect(isDirectorPhase('invalid')).toBe(false);
      expect(isDirectorPhase(null)).toBe(false);
    });

    it('should identify valid Task statuses', () => {
      expect(isTaskStatus('pending')).toBe(true);
      expect(isTaskStatus('in_progress')).toBe(true);
      expect(isTaskStatus('completed')).toBe(true);
      expect(isTaskStatus('success')).toBe(true);
      expect(isTaskStatus('blocked')).toBe(true);
      expect(isTaskStatus('failed')).toBe(true);
      expect(isTaskStatus('cancelled')).toBe(true);
      expect(isTaskStatus('invalid')).toBe(false);
      expect(isTaskStatus(null)).toBe(false);
    });

    it('should identify RuntimeProjectionPayload', () => {
      const validPayload: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: null,
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };
      expect(isRuntimeProjectionPayload(validPayload)).toBe(true);

      expect(isRuntimeProjectionPayload(null)).toBe(false);
      expect(isRuntimeProjectionPayload({})).toBe(false);
      expect(isRuntimeProjectionPayload({ generated_at: 'test' })).toBe(false);
    });
  });

  describe('selectTaskRows', () => {
    it('should return workflow tasks when available', () => {
      const tasks: WorkflowTask[] = [
        { id: '1', title: 'Task 1', status: 'completed' },
        { id: '2', title: 'Task 2', status: 'in_progress' },
      ];
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: {
          loaded: true,
          run_id: 'run-1',
          tasks,
          completed_at: null,
        },
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectTaskRows(projection)).toEqual(tasks);
    });

    it('should return director placeholder when workflow empty but director active', () => {
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: {
          running: true,
          active_tasks: 3,
          completed_tasks: 1,
          failed_tasks: 0,
          phase: 'running',
          current_run_id: 'run-123',
          queue_depth: 0,
          last_updated: '2024-01-01T00:00:00Z',
        },
        workflow: null,
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      const result = selectTaskRows(projection);
      expect(result).toHaveLength(1);
      expect(result[0].id).toBe('director-run-123');
      expect(result[0].status).toBe('in_progress');
    });

    it('should return empty array when no tasks available', () => {
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: null,
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectTaskRows(projection)).toEqual([]);
    });
  });

  describe('selectPrimaryStatus', () => {
    it('should return director status when director running', () => {
      const projection: RuntimeProjectionPayload = {
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
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectPrimaryStatus(projection)).toBe('director-running');
    });

    it('should return pm status when pm running', () => {
      const projection: RuntimeProjectionPayload = {
        pm: {
          running: true,
          current_task_id: 'task-1',
          phase: 'planning',
          last_updated: '2024-01-01T00:00:00Z',
        },
        director: null,
        workflow: null,
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectPrimaryStatus(projection)).toBe('pm-planning');
    });

    it('should return workflow-loaded when workflow loaded', () => {
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: {
          loaded: true,
          run_id: 'run-1',
          tasks: [],
          completed_at: null,
        },
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectPrimaryStatus(projection)).toBe('workflow-loaded');
    });

    it('should return idle when nothing active', () => {
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: null,
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectPrimaryStatus(projection)).toBe('idle');
    });
  });

  describe('isSystemActive', () => {
    it('should return true when PM running', () => {
      const projection: RuntimeProjectionPayload = {
        pm: {
          running: true,
          current_task_id: null,
          phase: 'planning',
          last_updated: '2024-01-01T00:00:00Z',
        },
        director: null,
        workflow: null,
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(isSystemActive(projection)).toBe(true);
    });

    it('should return true when Director running', () => {
      const projection: RuntimeProjectionPayload = {
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
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(isSystemActive(projection)).toBe(true);
    });

    it('should return true when workflow has in-progress tasks', () => {
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: {
          loaded: true,
          run_id: 'run-1',
          tasks: [
            { id: '1', title: 'Task 1', status: 'in_progress' },
          ],
          completed_at: null,
        },
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(isSystemActive(projection)).toBe(true);
    });

    it('should return false when nothing active', () => {
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: {
          loaded: true,
          run_id: 'run-1',
          tasks: [
            { id: '1', title: 'Task 1', status: 'completed' },
          ],
          completed_at: '2024-01-01T00:00:00Z',
        },
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(isSystemActive(projection)).toBe(false);
    });
  });

  describe('selectOverallProgress', () => {
    it('should return workflow metadata progress when available', () => {
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: {
          loaded: true,
          run_id: 'run-1',
          tasks: [],
          completed_at: null,
          metadata: {
            total_tasks: 10,
            completed_tasks: 5,
            failed_tasks: 0,
            progress_percentage: 50,
          },
        },
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectOverallProgress(projection)).toBe(50);
    });

    it('should return PM progress when available', () => {
      const projection: RuntimeProjectionPayload = {
        pm: {
          running: true,
          current_task_id: null,
          phase: 'planning',
          progress: 75,
          last_updated: '2024-01-01T00:00:00Z',
        },
        director: null,
        workflow: null,
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectOverallProgress(projection)).toBe(75);
    });

    it('should calculate progress from tasks', () => {
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: {
          loaded: true,
          run_id: 'run-1',
          tasks: [
            { id: '1', title: 'Task 1', status: 'completed' },
            { id: '2', title: 'Task 2', status: 'completed' },
            { id: '3', title: 'Task 3', status: 'in_progress' },
            { id: '4', title: 'Task 4', status: 'pending' },
          ],
          completed_at: null,
        },
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectOverallProgress(projection)).toBe(50);
    });

    it('should return 0 when no progress data available', () => {
      const projection: RuntimeProjectionPayload = {
        pm: null,
        director: null,
        workflow: null,
        engine: null,
        snapshot_compat: {},
        generated_at: '2024-01-01T00:00:00Z',
      };

      expect(selectOverallProgress(projection)).toBe(0);
    });
  });
});
