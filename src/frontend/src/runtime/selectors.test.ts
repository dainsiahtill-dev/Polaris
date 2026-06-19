import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useRoles, useRuntimeEvents } from './selectors';

const runtimeMock = vi.hoisted(() => {
  const state: Record<string, unknown> = {};
  return {
    state,
    reset: () => {
      Object.keys(state).forEach((key) => {
        delete state[key];
      });
      Object.assign(state, {
        currentPhase: 'planning',
        engineStatus: null,
        executionLogs: [],
        pmStatus: null,
        directorStatus: null,
        workers: [],
        tasks: [],
        runId: 'run-1',
      });
    },
  };
});

vi.mock('@/app/hooks/useRuntime', () => ({
  useRuntime: vi.fn(() => runtimeMock.state),
}));

describe('runtime selectors', () => {
  beforeEach(() => {
    runtimeMock.reset();
  });

  it('maps ChiefEngineer role state from runtime engine status', () => {
    runtimeMock.state.engineStatus = {
      roles: {
        ChiefEngineer: {
          status: 'completed',
          task_id: 'TASK-1',
          task_title: '设计交付蓝图',
          detail: 'blueprint ready',
          updated_at: '2026-06-19T08:00:00Z',
        },
      },
    };

    const { result } = renderHook(() => useRoles());

    expect(result.current.ChiefEngineer.state).toBe('completed');
    expect(result.current.ChiefEngineer.task_id).toBe('TASK-1');
    expect(result.current.ChiefEngineer.task_title).toBe('设计交付蓝图');
    expect(result.current.ChiefEngineer.detail).toBe('blueprint ready');
  });

  it('maps Chief Engineer execution log sources to ChiefEngineer runtime events', () => {
    runtimeMock.state.executionLogs = [
      {
        id: 'ce-event-1',
        timestamp: '2026-06-19T08:00:00Z',
        level: 'info',
        source: 'Chief Engineer',
        message: '蓝图已生成',
        details: 'TASK-1',
        meta: { task_id: 'TASK-1' },
      },
    ];

    const { result } = renderHook(() => useRuntimeEvents());

    expect(result.current).toHaveLength(1);
    expect(result.current[0]?.role).toBe('ChiefEngineer');
    expect(result.current[0]?.message).toBe('蓝图已生成');
  });
});
