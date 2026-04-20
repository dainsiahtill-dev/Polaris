/**
 * useRuntimeStore Tests
 *
 * 测试运行时全局状态管理 (Zustand + Immer)
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { enableMapSet } from 'immer';
import { useRuntimeStore } from '../useRuntimeStore';
import type { BackendStatus, EngineStatus, LlmStatus, LanceDbStatus } from '@/app/types/appContracts';
import type { DialogueEvent } from '@/app/components/DialoguePanel';

// Enable MapSet plugin for Immer
enableMapSet();

// Helper to create a fresh store for each test
function createStoreHook() {
  return () => useRuntimeStore();
}

describe('useRuntimeStore', () => {
  beforeEach(() => {
    // Reset store state before each test
    act(() => {
      useRuntimeStore.getState().resetAll();
    });
  });

  afterEach(() => {
    act(() => {
      useRuntimeStore.getState().resetAll();
    });
  });

  describe('Initial State', () => {
    it('should have correct initial connection state', () => {
      const { result } = renderHook(createStoreHook());

      expect(result.current.live).toBe(false);
      expect(result.current.connected).toBe(false);
      expect(result.current.error).toBeNull();
      expect(result.current.reconnecting).toBe(false);
      expect(result.current.attemptCount).toBe(0);
    });

    it('should have null initial role statuses', () => {
      const { result } = renderHook(createStoreHook());

      expect(result.current.pmStatus).toBeNull();
      expect(result.current.directorStatus).toBeNull();
      expect(result.current.engineStatus).toBeNull();
      expect(result.current.llmStatus).toBeNull();
      expect(result.current.lancedbStatus).toBeNull();
      expect(result.current.snapshot).toBeNull();
      expect(result.current.anthroState).toBeNull();
    });

    it('should have empty initial event streams', () => {
      const { result } = renderHook(createStoreHook());

      expect(result.current.dialogueEvents).toEqual([]);
      expect(result.current.executionLogs).toEqual([]);
      expect(result.current.llmStreamEvents).toEqual([]);
      expect(result.current.processStreamEvents).toEqual([]);
    });

    it('should have empty initial tasks and workers', () => {
      const { result } = renderHook(createStoreHook());

      expect(result.current.tasks).toEqual([]);
      expect(result.current.workers).toEqual([]);
      expect(result.current.fileEditEvents).toEqual([]);
      expect(result.current.taskProgressMap.size).toBe(0);
      expect(result.current.taskTraceMap.size).toBe(0);
      expect(result.current.sequentialTraceMap.size).toBe(0);
    });

    it('should have correct initial derived state', () => {
      const { result } = renderHook(createStoreHook());

      expect(result.current.qualityGate).toBeNull();
      expect(result.current.currentPhase).toBe('idle');
      expect(result.current.runId).toBeNull();
    });
  });

  describe('Connection State Management', () => {
    it('should update connection state', () => {
      const { result } = renderHook(createStoreHook());

      act(() => {
        useRuntimeStore.getState().setConnectionState({
          live: true,
          reconnecting: false,
          attemptCount: 1,
        });
      });

      expect(result.current.live).toBe(true);
      expect(result.current.reconnecting).toBe(false);
      expect(result.current.attemptCount).toBe(1);
    });

    it('should update error state', () => {
      const { result } = renderHook(createStoreHook());

      act(() => {
        useRuntimeStore.getState().setConnectionState({
          error: 'Connection failed',
        });
      });

      expect(result.current.error).toBe('Connection failed');
    });

    it('should reset connection state', () => {
      const { result } = renderHook(createStoreHook());

      act(() => {
        useRuntimeStore.getState().setConnectionState({
          live: true,
          error: 'some error',
          reconnecting: true,
          attemptCount: 3,
        });
      });

      act(() => {
        useRuntimeStore.getState().resetForWorkspace();
      });

      expect(result.current.live).toBe(false);
      expect(result.current.error).toBeNull();
      expect(result.current.reconnecting).toBe(false);
      expect(result.current.attemptCount).toBe(0);
    });
  });

  describe('Role Status Management', () => {
    it('should update PM status', () => {
      const { result } = renderHook(createStoreHook());

      const pmStatus: BackendStatus = {
        running: true,
        pid: 12345,
        started_at: Date.now(),
        mode: 'standard',
        status: {},
      };

      act(() => {
        useRuntimeStore.getState().setPmStatus(pmStatus);
      });

      expect(result.current.pmStatus).toEqual(pmStatus);
    });

    it('should update Director status', () => {
      const { result } = renderHook(createStoreHook());

      const directorStatus: BackendStatus = {
        running: true,
        pid: 12346,
        started_at: Date.now(),
        mode: 'v2_service',
        status: {},
      };

      act(() => {
        useRuntimeStore.getState().setDirectorStatus(directorStatus);
      });

      expect(result.current.directorStatus).toEqual(directorStatus);
    });

    it('should update Engine status', () => {
      const { result } = renderHook(createStoreHook());

      const engineStatus: EngineStatus = {
        state: 'running',
        iterations: 5,
      };

      act(() => {
        useRuntimeStore.getState().setEngineStatus(engineStatus);
      });

      expect(result.current.engineStatus).toEqual(engineStatus);
    });

    it('should update LLM status', () => {
      const { result } = renderHook(createStoreHook());

      const llmStatus: LlmStatus = {
        state: 'ready',
        roles: {},
      };

      act(() => {
        useRuntimeStore.getState().setLlmStatus(llmStatus);
      });

      expect(result.current.llmStatus).toEqual(llmStatus);
    });

    it('should update LanceDB status', () => {
      const { result } = renderHook(createStoreHook());

      const lancedbStatus: LanceDbStatus = {
        ok: true,
        python: '3.11.0',
        version: '0.5.0',
      };

      act(() => {
        useRuntimeStore.getState().setLancedbStatus(lancedbStatus);
      });

      expect(result.current.lancedbStatus).toEqual(lancedbStatus);
    });
  });

  describe('Dialogue Events', () => {
    it('should append dialogue events', () => {
      const { result } = renderHook(createStoreHook());

      const event: DialogueEvent = {
        id: 'evt-1',
        role: 'pm',
        type: 'message',
        content: 'Test message',
        timestamp: new Date().toISOString(),
      };

      act(() => {
        useRuntimeStore.getState().appendDialogueEvent(event);
      });

      expect(result.current.dialogueEvents).toHaveLength(1);
      expect(result.current.dialogueEvents[0]).toEqual(event);
    });

    it('should limit dialogue events to 500', () => {
      const { result } = renderHook(createStoreHook());

      // Append 600 events
      for (let i = 0; i < 600; i++) {
        act(() => {
          useRuntimeStore.getState().appendDialogueEvent({
            id: `evt-${i}`,
            role: 'pm',
            type: 'message',
            content: `Message ${i}`,
            timestamp: new Date().toISOString(),
          });
        });
      }

      expect(result.current.dialogueEvents).toHaveLength(500);
      // Should keep the last 500
      expect(result.current.dialogueEvents[0].id).toBe('evt-100');
      expect(result.current.dialogueEvents[499].id).toBe('evt-599');
    });

    it('should set dialogue events', () => {
      const { result } = renderHook(createStoreHook());

      const events: DialogueEvent[] = [
        {
          id: 'evt-1',
          role: 'pm',
          type: 'message',
          content: 'First',
          timestamp: new Date().toISOString(),
        },
        {
          id: 'evt-2',
          role: 'director',
          type: 'message',
          content: 'Second',
          timestamp: new Date().toISOString(),
        },
      ];

      act(() => {
        useRuntimeStore.getState().setDialogueEvents(events);
      });

      expect(result.current.dialogueEvents).toEqual(events);
    });
  });

  describe('Execution Logs', () => {
    it('should append execution logs', () => {
      const { result } = renderHook(createStoreHook());

      const log = {
        id: 'log-1',
        timestamp: new Date().toISOString(),
        level: 'info' as const,
        source: 'System',
        message: 'Test log',
      };

      act(() => {
        useRuntimeStore.getState().appendExecutionLog(log);
      });

      expect(result.current.executionLogs).toHaveLength(1);
    });

    it('should limit execution logs to 100', () => {
      const { result } = renderHook(createStoreHook());

      for (let i = 0; i < 120; i++) {
        act(() => {
          useRuntimeStore.getState().appendExecutionLog({
            id: `log-${i}`,
            timestamp: new Date().toISOString(),
            level: 'info' as const,
            source: 'System',
            message: `Log ${i}`,
          });
        });
      }

      expect(result.current.executionLogs).toHaveLength(100);
    });

    it('should set execution logs', () => {
      const { result } = renderHook(createStoreHook());

      const logs = [
        { id: 'log-1', timestamp: new Date().toISOString(), level: 'info' as const, source: 'System', message: 'Log 1' },
        { id: 'log-2', timestamp: new Date().toISOString(), level: 'error' as const, source: 'PM', message: 'Log 2' },
      ];

      act(() => {
        useRuntimeStore.getState().setExecutionLogs(logs);
      });

      expect(result.current.executionLogs).toEqual(logs);
    });
  });

  describe('LLM Stream Events', () => {
    it('should append LLM stream events', () => {
      const { result } = renderHook(createStoreHook());

      const event = {
        id: 'llm-1',
        timestamp: new Date().toISOString(),
        level: 'thinking' as const,
        source: 'LLM',
        message: 'Thinking...',
      };

      act(() => {
        useRuntimeStore.getState().appendLlmStreamEvent(event);
      });

      expect(result.current.llmStreamEvents).toHaveLength(1);
    });

    it('should limit LLM stream events to 180', () => {
      const { result } = renderHook(createStoreHook());

      for (let i = 0; i < 200; i++) {
        act(() => {
          useRuntimeStore.getState().appendLlmStreamEvent({
            id: `llm-${i}`,
            timestamp: new Date().toISOString(),
            level: 'thinking' as const,
            source: 'LLM',
            message: `LLM ${i}`,
          });
        });
      }

      expect(result.current.llmStreamEvents).toHaveLength(180);
    });
  });

  describe('Process Stream Events', () => {
    it('should append process stream events', () => {
      const { result } = renderHook(createStoreHook());

      const event = {
        id: 'proc-1',
        timestamp: new Date().toISOString(),
        level: 'info' as const,
        source: 'Process',
        message: 'Process started',
      };

      act(() => {
        useRuntimeStore.getState().appendProcessStreamEvent(event);
      });

      expect(result.current.processStreamEvents).toHaveLength(1);
    });

    it('should limit process stream events to 240', () => {
      const { result } = renderHook(createStoreHook());

      for (let i = 0; i < 260; i++) {
        act(() => {
          useRuntimeStore.getState().appendProcessStreamEvent({
            id: `proc-${i}`,
            timestamp: new Date().toISOString(),
            level: 'info' as const,
            source: 'Process',
            message: `Process ${i}`,
          });
        });
      }

      expect(result.current.processStreamEvents).toHaveLength(240);
    });
  });

  describe('Derived State', () => {
    it('should update quality gate', () => {
      const { result } = renderHook(createStoreHook());

      const qualityGate = {
        score: 85,
        passed: true,
        attempt: 1,
        maxAttempts: 3,
        summary: 'Quality check passed',
        issues: [],
        metrics: { critical: 0, warnings: 2, score: 85 },
      };

      act(() => {
        useRuntimeStore.getState().setQualityGate(qualityGate);
      });

      expect(result.current.qualityGate).toEqual(qualityGate);
    });

    it('should update current phase', () => {
      const { result } = renderHook(createStoreHook());

      act(() => {
        useRuntimeStore.getState().setCurrentPhase('planning');
      });

      expect(result.current.currentPhase).toBe('planning');
    });

    it('should update run ID', () => {
      const { result } = renderHook(createStoreHook());

      act(() => {
        useRuntimeStore.getState().setRunId('run-123');
      });

      expect(result.current.runId).toBe('run-123');
    });
  });

  describe('Task Management', () => {
    it('should set tasks', () => {
      const { result } = renderHook(createStoreHook());

      const tasks = [
        {
          id: 'task-1',
          title: 'Task 1',
          status: 'PENDING' as const,
          goal: 'Task 1 goal',
          priority: 1 as const,
          done: false,
          acceptance: [],
        },
        {
          id: 'task-2',
          title: 'Task 2',
          status: 'IN_PROGRESS' as const,
          goal: 'Task 2 goal',
          priority: 2 as const,
          done: false,
          acceptance: [],
        },
      ];

      act(() => {
        useRuntimeStore.getState().setTasks(tasks);
      });

      expect(result.current.tasks).toEqual(tasks);
    });

    it('should update task progress', () => {
      const { result } = renderHook(createStoreHook());

      act(() => {
        useRuntimeStore.getState().updateTaskProgress('task-1', {
          phase: 'planning',
          phaseIndex: 2,
          phaseTotal: 5,
          retryCount: 0,
          maxRetries: 3,
          currentFile: 'src/test.ts',
        });
      });

      expect(result.current.taskProgressMap.get('task-1')).toEqual({
        phase: 'planning',
        phaseIndex: 2,
        phaseTotal: 5,
        retryCount: 0,
        maxRetries: 3,
        currentFile: 'src/test.ts',
      });
    });

    it('should append task trace', () => {
      const { result } = renderHook(createStoreHook());

      const trace = {
        event_id: 'trace-1',
        run_id: 'run-1',
        role: 'pm' as const,
        task_id: 'task-1',
        seq: 1,
        phase: 'planning',
        step_kind: 'phase' as const,
        step_title: 'Planning phase',
        step_detail: 'Starting planning',
        status: 'started' as const,
        attempt: 1,
        visibility: 'summary' as const,
        ts: new Date().toISOString(),
        refs: {},
      };

      act(() => {
        useRuntimeStore.getState().appendTaskTrace(trace);
      });

      expect(result.current.taskTraceMap.get('task-1')).toHaveLength(1);
    });

    it('should limit task traces to 100 per task', () => {
      const { result } = renderHook(createStoreHook());

      for (let i = 0; i < 120; i++) {
        act(() => {
          useRuntimeStore.getState().appendTaskTrace({
            event_id: `trace-${i}`,
            run_id: 'run-1',
            role: 'pm' as const,
            task_id: 'task-1',
            seq: i,
            phase: 'planning',
            step_kind: 'phase' as const,
            step_title: `Step ${i}`,
            step_detail: 'Planning',
            status: 'started' as const,
            attempt: 1,
            visibility: 'summary' as const,
            ts: new Date().toISOString(),
            refs: {},
          });
        });
      }

      expect(result.current.taskTraceMap.get('task-1')).toHaveLength(100);
    });
  });

  describe('Worker Management', () => {
    it('should set workers', () => {
      const { result } = renderHook(createStoreHook());

      const workers = [
        { id: 'worker-1', name: 'Worker 1', status: 'idle', healthy: true },
        { id: 'worker-2', name: 'Worker 2', status: 'busy', currentTaskId: 'task-1', healthy: true },
      ];

      act(() => {
        useRuntimeStore.getState().setWorkers(workers);
      });

      expect(result.current.workers).toEqual(workers);
    });

    it('should append file edit events', () => {
      const { result } = renderHook(createStoreHook());

      const event = {
        id: 'edit-1',
        filePath: 'src/test.ts',
        operation: 'modify' as const,
        contentSize: 1024,
        timestamp: new Date().toISOString(),
      };

      act(() => {
        useRuntimeStore.getState().appendFileEditEvent(event);
      });

      expect(result.current.fileEditEvents).toHaveLength(1);
    });

    it('should limit file edit events to 50', () => {
      const { result } = renderHook(createStoreHook());

      for (let i = 0; i < 60; i++) {
        act(() => {
          useRuntimeStore.getState().appendFileEditEvent({
            id: `edit-${i}`,
            filePath: `src/test${i}.ts`,
            operation: 'modify' as const,
            contentSize: 1024,
            timestamp: new Date().toISOString(),
          });
        });
      }

      expect(result.current.fileEditEvents).toHaveLength(50);
    });
  });

  describe('Bulk Reset', () => {
    it('should reset all state to initial values', () => {
      const { result } = renderHook(createStoreHook());

      // Set various states
      act(() => {
        useRuntimeStore.getState().setConnectionState({ live: true, attemptCount: 5 });
        useRuntimeStore.getState().setPmStatus({ running: true, pid: 123 } as BackendStatus);
        useRuntimeStore.getState().setCurrentPhase('planning');
        useRuntimeStore.getState().setRunId('run-123');
        useRuntimeStore.getState().setTasks([{
          id: 'task-1',
          title: 'Test',
          status: 'PENDING' as const,
          goal: 'Test',
          priority: 1 as const,
          done: false,
          acceptance: [],
        }]);
      });

      // Reset
      act(() => {
        useRuntimeStore.getState().resetAll();
      });

      // Verify all states are reset
      expect(result.current.live).toBe(false);
      expect(result.current.attemptCount).toBe(0);
      expect(result.current.pmStatus).toBeNull();
      expect(result.current.currentPhase).toBe('idle');
      expect(result.current.runId).toBeNull();
      expect(result.current.tasks).toEqual([]);
    });

    it('should reset workspace-specific state', () => {
      const { result } = renderHook(createStoreHook());

      // Set various states
      act(() => {
        useRuntimeStore.getState().setPmStatus({ running: true, pid: 123 } as BackendStatus);
        useRuntimeStore.getState().setCurrentPhase('planning');
        useRuntimeStore.getState().setTasks([{
          id: 'task-1',
          title: 'Test',
          status: 'PENDING' as const,
          goal: 'Test',
          priority: 1 as const,
          done: false,
          acceptance: [],
        }]);
      });

      // Reset for workspace
      act(() => {
        useRuntimeStore.getState().resetForWorkspace();
      });

      // Verify workspace-specific states are reset
      expect(result.current.pmStatus).toBeNull();
      expect(result.current.currentPhase).toBe('idle');
      expect(result.current.tasks).toEqual([]);
      expect(result.current.runId).toBeNull();
    });
  });

  describe('Selectors', () => {
    it('should provide correct selectors', () => {
      const { result } = renderHook(createStoreHook());

      // Verify selectors exist and work
      expect(useRuntimeStore.getState().live).toBe(result.current.live);
      expect(useRuntimeStore.getState().error).toBe(result.current.error);
      expect(useRuntimeStore.getState().reconnecting).toBe(result.current.reconnecting);
    });
  });
});
