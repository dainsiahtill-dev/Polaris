import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useRuntime } from '../useRuntime';
import { useRuntimeStore } from '../useRuntimeStore';

type RuntimeMessageHandler = (message: unknown) => void;

const runtimeConnectionMock = vi.hoisted(() => {
  let handler: RuntimeMessageHandler | null = null;
  const registerMessageHandler = vi.fn((nextHandler: RuntimeMessageHandler) => {
    handler = nextHandler;
    return vi.fn(() => {
      if (handler === nextHandler) {
        handler = null;
      }
    });
  });
  const sendCommand = vi.fn();
  const connect = vi.fn();
  const disconnect = vi.fn();
  const reconnect = vi.fn();
  const updateSubscription = vi.fn();

  return {
    registerMessageHandler,
    sendCommand,
    connect,
    disconnect,
    reconnect,
    updateSubscription,
    getHandler: () => handler,
    reset: () => {
      handler = null;
      registerMessageHandler.mockClear();
      sendCommand.mockClear();
      connect.mockClear();
      disconnect.mockClear();
      reconnect.mockClear();
      updateSubscription.mockClear();
    },
  };
});

const settingsHookMock = vi.hoisted(() => {
  const load = vi.fn();

  return {
    load,
    reset: () => {
      load.mockClear();
    },
  };
});

vi.mock('../useRuntimeConnection', () => ({
  useRuntimeConnection: vi.fn(() => ({
    live: false,
    connected: false,
    isConnected: false,
    error: null,
    reconnecting: false,
    attemptCount: 0,
    connect: runtimeConnectionMock.connect,
    disconnect: runtimeConnectionMock.disconnect,
    reconnect: runtimeConnectionMock.reconnect,
    updateSubscription: runtimeConnectionMock.updateSubscription,
    transportConnected: false,
    transportReconnecting: false,
    transportError: null,
    transportAttemptCount: 0,
    transportReconnect: runtimeConnectionMock.reconnect,
    registerMessageHandler: runtimeConnectionMock.registerMessageHandler,
    sendCommand: runtimeConnectionMock.sendCommand,
    workspaceRef: { current: '/test/workspace' },
    rolesRef: { current: ['pm', 'director', 'qa'] as ('pm' | 'director' | 'qa')[] },
    activeRef: { current: true },
  })),
}));

vi.mock('@/hooks', () => ({
  useSettings: vi.fn(() => ({
    settings: { workspace: '/test/workspace' },
    load: settingsHookMock.load,
  })),
}));

function emitRuntimeMessage(message: unknown): void {
  const handler = runtimeConnectionMock.getHandler();
  if (!handler) {
    throw new Error('runtime message handler not registered');
  }
  act(() => {
    handler(message);
  });
}

describe('useRuntime llm filtering and dedup', () => {
  beforeEach(() => {
    runtimeConnectionMock.reset();
    settingsHookMock.reset();
    act(() => {
      useRuntimeStore.getState().resetAll();
    });
  });

  afterEach(() => {
    act(() => {
      useRuntimeStore.getState().resetAll();
    });
  });

  it('accepts llm line when payload domain is llm even if nested channel is runtime_events', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        channel: 'runtime_events',
        domain: 'llm',
        event: 'invoke_done',
        data: {
          summary: 'LLM response accepted',
          output_chars: 32,
        },
      }),
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    expect(result.current.llmStreamEvents[0]?.message).toBe('LLM response accepted');
  });

  it('processes EVENT query_result batches item-by-item and preserves v2 dedup', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'event',
      action: 'query_result',
      events: [
        {
          event_id: 'evt-1',
          channel: 'llm',
          domain: 'llm',
          event: 'invoke_done',
          data: {
            summary: 'query-result-item-1',
            output_chars: 10,
          },
        },
        {
          event_id: 'evt-1',
          channel: 'llm',
          domain: 'llm',
          event: 'invoke_done',
          data: {
            summary: 'query-result-item-duplicate',
            output_chars: 10,
          },
        },
        {
          event_id: 'evt-2',
          channel: 'llm',
          domain: 'llm',
          event: 'invoke_done',
          data: {
            summary: 'query-result-item-2',
            output_chars: 10,
          },
        },
      ],
    });

    expect(result.current.llmStreamEvents).toHaveLength(2);
    expect(result.current.llmStreamEvents[0]?.message).toBe('query-result-item-1');
    expect(result.current.llmStreamEvents[1]?.message).toBe('query-result-item-2');
  });

  it('dedups repeated llm line within same run but allows same payload after run switch', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    const repeatedLlmLine = {
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        event: 'invoke_done',
        data: {
          summary: 'same-line-across-runs',
          output_chars: 21,
        },
      }),
    };

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-1' },
    });

    emitRuntimeMessage(repeatedLlmLine);
    emitRuntimeMessage(repeatedLlmLine);
    expect(result.current.llmStreamEvents).toHaveLength(1);

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-2' },
    });

    emitRuntimeMessage(repeatedLlmLine);
    expect(result.current.llmStreamEvents).toHaveLength(2);
    expect(result.current.llmStreamEvents[0]?.message).toBe('same-line-across-runs');
    expect(result.current.llmStreamEvents[1]?.message).toBe('same-line-across-runs');
  });

  it('clears stale llm dedup scope when runtime returns to idle without run_id', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    const repeatedLlmLine = {
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        event: 'invoke_done',
        data: {
          summary: 'same-line-after-idle-boundary',
          output_chars: 14,
        },
      }),
    };

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-1' },
    });

    emitRuntimeMessage(repeatedLlmLine);
    emitRuntimeMessage(repeatedLlmLine);
    expect(result.current.llmStreamEvents).toHaveLength(1);

    emitRuntimeMessage({
      type: 'status',
      pm_status: { running: false },
      director_status: { running: false },
      snapshot: null,
    });

    emitRuntimeMessage(repeatedLlmLine);
    expect(result.current.llmStreamEvents).toHaveLength(2);
    expect(result.current.llmStreamEvents[0]?.message).toBe('same-line-after-idle-boundary');
    expect(result.current.llmStreamEvents[1]?.message).toBe('same-line-after-idle-boundary');
  });

  it('does not rollback dedup scope when late log from previous run arrives', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    const sharedLine = {
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        event: 'invoke_done',
        data: {
          summary: 'shared-cross-run-line',
          output_chars: 12,
        },
      }),
    };

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-1' },
    });
    emitRuntimeMessage(sharedLine);
    expect(result.current.llmStreamEvents).toHaveLength(1);

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-2' },
    });

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        run_id: 'run-1',
        event: 'invoke_done',
        data: {
          summary: 'late-run1-line',
          output_chars: 8,
        },
      }),
    });

    emitRuntimeMessage(sharedLine);
    expect(result.current.llmStreamEvents).toHaveLength(3);
    expect(result.current.llmStreamEvents[2]?.message).toBe('shared-cross-run-line');
  });

  it('reloads runtime settings when a v2 settings_changed event updates the workspace', () => {
    renderHook(() => useRuntime({ autoConnect: false }));

    emitRuntimeMessage({
      type: 'event',
      event: {
        event_id: 'settings-evt-1',
        event_name: 'settings_changed',
        category: 'system',
        payload: {
          workspace: '/new/workspace',
          previous_workspace: '/test/workspace',
          changed_fields: ['workspace'],
        },
      },
    });

    expect(settingsHookMock.load).toHaveBeenCalledTimes(1);
  });

  it('does not reload runtime settings for controlled workspace props', () => {
    renderHook(() => useRuntime({ autoConnect: false, workspace: '/test/workspace' }));

    emitRuntimeMessage({
      type: 'event',
      event: {
        event_id: 'settings-evt-2',
        event_name: 'settings_changed',
        category: 'system',
        payload: {
          workspace: '/new/workspace',
          previous_workspace: '/test/workspace',
          changed_fields: ['workspace'],
        },
      },
    });

    expect(settingsHookMock.load).not.toHaveBeenCalled();
  });
});
