/**
 * Tests for useFactory hook — the factory events now flow through the
 * platform's unified WebSocket + NAT JetStream pipeline (no legacy HTTP
 * event-stream client). We mock the transport's subscribeChannels and
 * registerMessageHandler instead of the legacy connectFactoryStream.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

const startFactoryRunMock = vi.fn();
const stopFactoryRunMock = vi.fn();
const pauseFactoryRunMock = vi.fn();
const resumeFactoryRunMock = vi.fn();
const retryFactoryRunFromCheckpointMock = vi.fn();
const getFactoryRunMock = vi.fn();
const getFactoryRunArtifactsMock = vi.fn();
const listFactoryRunsMock = vi.fn();
const transportSubscribeMock = vi.fn(() => () => {});
const transportRegisterMock = vi.fn(() => () => {});
const toastSuccessMock = vi.fn();
const toastErrorMock = vi.fn();

let lastMessageHandler: ((message: unknown) => void) | null = null;
let lastChannelUnsubscribe: () => void = () => {};
let closeMock = vi.fn();

vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccessMock(...args),
    error: (...args: unknown[]) => toastErrorMock(...args),
  },
}));

vi.mock('@/services', () => ({
  startFactoryRun: (...args: unknown[]) => startFactoryRunMock(...args),
  stopFactoryRun: (...args: unknown[]) => stopFactoryRunMock(...args),
  pauseFactoryRun: (...args: unknown[]) => pauseFactoryRunMock(...args),
  resumeFactoryRun: (...args: unknown[]) => resumeFactoryRunMock(...args),
  retryFactoryRunFromCheckpoint: (...args: unknown[]) =>
    retryFactoryRunFromCheckpointMock(...args),
  getFactoryRun: (...args: unknown[]) => getFactoryRunMock(...args),
  getFactoryRunArtifacts: (...args: unknown[]) => getFactoryRunArtifactsMock(...args),
  listFactoryRuns: (...args: unknown[]) => listFactoryRunsMock(...args),
}));

vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: () => ({
    subscribeChannels: (...args: unknown[]) => {
      const unsub = transportSubscribeMock(...args);
      lastChannelUnsubscribe = unsub;
      return unsub;
    },
    registerMessageHandler: (...args: unknown[]) => {
      const handler = args[0] as (message: unknown) => void;
      lastMessageHandler = handler;
      return transportRegisterMock(...args);
    },
  }),
}));

import { useFactory } from './useFactory';

const baseRun = {
  run_id: 'run-1',
  phase: 'planning',
  status: 'running',
  current_stage: 'pm_planning',
  last_successful_stage: null,
  progress: 25,
  roles: {},
  gates: [],
  created_at: '2026-03-07T00:00:00Z',
};

/** Build a runtime.v2 envelope the hook's WS message handler will consume. */
function envelope(payload: Record<string, unknown>, kind: string) {
  return {
    type: 'EVENT',
    protocol: 'runtime.v2',
    cursor: 1,
    event: {
      schema_version: 'runtime.v2',
      workspace_key: 'ws',
      run_id: baseRun.run_id,
      channel: 'event.factory',
      kind,
      ts: new Date().toISOString(),
      cursor: 1,
      payload,
      meta: { source: 'factory_run_service' },
    },
  };
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

describe('useFactory', () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    lastMessageHandler = null;
    lastChannelUnsubscribe = () => {};
    closeMock = vi.fn();

    startFactoryRunMock.mockResolvedValue({ ok: true, data: baseRun });
    stopFactoryRunMock.mockResolvedValue({
      ok: true,
      data: { ...baseRun, status: 'cancelled', phase: 'cancelled', progress: 25 },
    });
    pauseFactoryRunMock.mockResolvedValue({
      ok: true,
      data: { ...baseRun, status: 'paused' },
    });
    resumeFactoryRunMock.mockResolvedValue({ ok: true, data: baseRun });
    retryFactoryRunFromCheckpointMock.mockResolvedValue({
      ok: true,
      data: { ...baseRun, status: 'recovering' },
    });
    getFactoryRunMock.mockResolvedValue({ ok: true, data: baseRun });
    getFactoryRunArtifactsMock.mockImplementation(async (runId: string) => ({
      ok: true,
      data: {
        run_id: runId,
        artifacts: [],
        summary_md: null,
        summary_json: null,
      },
    }));
    listFactoryRunsMock.mockResolvedValue({ ok: true, data: [baseRun] });
  });

  it('starts a run and auto-connects the stream', async () => {
    const { result } = renderHook(() => useFactory({ workspace: '/tmp/ws' }), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.startRun({ workspace: 'ws' });
    });

    expect(transportSubscribeMock).toHaveBeenCalled();
    expect(transportRegisterMock).toHaveBeenCalled();
    expect(result.current.isStreaming).toBe(true);
  });

  it('fetches artifacts and summary when a current run is available', async () => {
    const { result } = renderHook(() => useFactory({ workspace: '/tmp/ws' }), { wrapper: createWrapper() });
    await act(async () => {
      await result.current.startRun({ workspace: 'ws' });
    });
    await waitFor(() => {
      expect(getFactoryRunArtifactsMock).toHaveBeenCalledWith('run-1');
    });
  });

  it('replaces currentRun from status events and stops on done', async () => {
    const { result } = renderHook(() => useFactory({ workspace: '/tmp/ws' }), { wrapper: createWrapper() });
    await act(async () => {
      await result.current.startRun({ workspace: 'ws' });
    });
    expect(result.current.currentRun?.run_id).toBe('run-1');

    await act(async () => {
      // Status envelope.
      lastMessageHandler?.(
        envelope(
          { ...baseRun, progress: 50, current_stage: 'director_dispatch' },
          'stage_started',
        ),
      );
    });
    expect(result.current.currentRun?.progress).toBe(50);

    await act(async () => {
      // Complete envelope (terminal).
      lastMessageHandler?.(
        envelope(
          { ...baseRun, status: 'completed', phase: 'completed', progress: 100 },
          'complete',
        ),
      );
    });
    expect(result.current.isStreaming).toBe(false);
  });

  it('refreshes artifacts after stream done', async () => {
    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });
    await act(async () => {
      await result.current.startRun({ workspace: 'ws' });
    });
    const before = getFactoryRunArtifactsMock.mock.calls.length;
    await act(async () => {
      lastMessageHandler?.(
        envelope(
          { ...baseRun, status: 'completed', phase: 'completed', progress: 100 },
          'complete',
        ),
      );
    });
    await waitFor(() => {
      expect(getFactoryRunArtifactsMock.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it('uses the factory envelope run id when runtime payload carries a Director run id', async () => {
    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });
    await act(async () => {
      await result.current.startRun({ workspace: 'ws' });
    });
    getFactoryRunArtifactsMock.mockClear();

    await act(async () => {
      lastMessageHandler?.(
        envelope(
          {
            ...baseRun,
            run_id: 'director-123456789abc',
            status: 'completed',
            phase: 'completed',
            progress: 100,
          },
          'task_runtime_execution',
        ),
      );
    });

    await waitFor(() => {
      expect(getFactoryRunArtifactsMock).toHaveBeenCalledWith('run-1');
    });
    expect(getFactoryRunArtifactsMock).not.toHaveBeenCalledWith('director-123456789abc');
    expect(result.current.currentRun?.run_id).toBe('run-1');
  });

  it('uses stop response as the terminal snapshot', async () => {
    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });
    await act(async () => {
      const stopped = await result.current.stopRun('run-1');
      expect(stopped?.status).toBe('cancelled');
    });
  });

  it('exposes pause, resume and retry controls through the canonical factory control API', async () => {
    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });
    await act(async () => {
      await result.current.pauseRun('run-1');
    });
    expect(pauseFactoryRunMock).toHaveBeenCalledWith('run-1', undefined);
    await act(async () => {
      await result.current.resumeRun('run-1');
    });
    expect(resumeFactoryRunMock).toHaveBeenCalledWith('run-1', undefined);
    await act(async () => {
      await result.current.retryRunFromCheckpoint('run-1');
    });
    expect(retryFactoryRunFromCheckpointMock).toHaveBeenCalledWith('run-1', undefined);
  });

  it('does not replace a failed realtime subscription with an HTTP status fetch on start', async () => {
    transportSubscribeMock.mockImplementation(() => {
      throw new Error('runtime ws unavailable');
    });

    const { result } = renderHook(
      () => useFactory({ workspace: '/tmp/ws', autoResumeLatest: false }),
      { wrapper: createWrapper() },
    );
    await act(async () => {
      const run = await result.current.startRun({ workspace: 'ws' });
      expect(run?.run_id).toBe('run-1');
    });
    expect(transportSubscribeMock).toHaveBeenCalled();
    expect(getFactoryRunMock).not.toHaveBeenCalled();
    expect(result.current.isStreaming).toBe(false);
    expect(toastErrorMock).toHaveBeenCalledWith('runtime ws unavailable');
  });

  it('does not replace a failed realtime subscription with an HTTP status fetch on retry', async () => {
    transportSubscribeMock.mockImplementation(() => {
      throw new Error('runtime ws unavailable');
    });

    const { result } = renderHook(
      () => useFactory({ workspace: '/tmp/ws', autoResumeLatest: false }),
      { wrapper: createWrapper() },
    );
    await act(async () => {
      const run = await result.current.retryRunFromCheckpoint('run-1');
      expect(run?.status).toBe('recovering');
    });
    expect(retryFactoryRunFromCheckpointMock).toHaveBeenCalledWith('run-1', undefined);
    expect(transportSubscribeMock).toHaveBeenCalled();
    expect(getFactoryRunMock).not.toHaveBeenCalled();
    expect(result.current.isStreaming).toBe(false);
  });

  it('does not replace a failed realtime subscription with an HTTP status fetch on auto resume', async () => {
    transportSubscribeMock.mockImplementation(() => {
      throw new Error('runtime ws unavailable');
    });

    const { result } = renderHook(() => useFactory({ workspace: '/tmp/ws' }), { wrapper: createWrapper() });
    await waitFor(() => {
      expect(result.current.currentRun?.run_id).toBe('run-1');
    });
    expect(listFactoryRunsMock).toHaveBeenCalledWith(1);
    expect(transportSubscribeMock).toHaveBeenCalled();
    expect(getFactoryRunMock).not.toHaveBeenCalled();
    expect(result.current.isStreaming).toBe(false);
  });

  it('resumes the latest non-terminal run for the active workspace', async () => {
    const { result } = renderHook(() => useFactory({ workspace: '/tmp/ws' }), { wrapper: createWrapper() });
    await waitFor(() => {
      expect(result.current.currentRun?.run_id).toBe('run-1');
    });
    expect(transportSubscribeMock).toHaveBeenCalled();
  });

  it('does not connect the stream when the latest run is terminal canceled', async () => {
    listFactoryRunsMock.mockResolvedValue({
      ok: true,
      data: [{ ...baseRun, status: 'cancelled', phase: 'cancelled' }],
    });
    const { result } = renderHook(() => useFactory({ workspace: '/tmp/ws' }), { wrapper: createWrapper() });
    await waitFor(() => {
      expect(result.current.currentRun?.status).toBe('cancelled');
    });
  });

  it('does not connect the stream when latest run is terminal by phase only', async () => {
    listFactoryRunsMock.mockResolvedValue({
      ok: true,
      data: [{ ...baseRun, phase: 'completed', status: 'running' }],
    });
    const { result } = renderHook(() => useFactory({ workspace: '/tmp/ws' }), { wrapper: createWrapper() });
    await waitFor(() => {
      expect(result.current.currentRun?.phase).toBe('completed');
    });
  });

  it('fetches artifacts when resuming a latest terminal run', async () => {
    listFactoryRunsMock.mockResolvedValue({
      ok: true,
      data: [{ ...baseRun, status: 'completed', phase: 'completed' }],
    });
    getFactoryRunArtifactsMock.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'run-1',
        artifacts: [
          {
            name: 'ce_TASK-1.json',
            path: 'runtime/blueprints/ce_TASK-1.json',
            size: 128,
            task_id: 'TASK-1',
          },
        ],
        summary_md: '# Summary',
        summary_json: null,
      },
    });

    const { result } = renderHook(() => useFactory({ workspace: '/tmp/ws' }), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.currentRun?.status).toBe('completed');
    });
    await waitFor(() => {
      expect(result.current.artifacts).toHaveLength(1);
    });
    expect(getFactoryRunArtifactsMock).toHaveBeenCalledWith('run-1');
    expect(transportSubscribeMock).not.toHaveBeenCalled();
  });

  it('keeps artifacts when the same terminal latest run is resumed again', async () => {
    listFactoryRunsMock.mockResolvedValue({
      ok: true,
      data: [{ ...baseRun, status: 'completed', phase: 'completed' }],
    });
    getFactoryRunArtifactsMock.mockResolvedValue({
      ok: true,
      data: {
        run_id: 'run-1',
        artifacts: [
          {
            name: 'ce_TASK-1.json',
            path: 'runtime/blueprints/ce_TASK-1.json',
            size: 128,
            task_id: 'TASK-1',
          },
        ],
        summary_md: '# Summary',
        summary_json: null,
      },
    });

    const { result } = renderHook(() => useFactory({ workspace: '/tmp/ws' }), { wrapper: createWrapper() });
    await waitFor(() => {
      expect(result.current.artifacts).toHaveLength(1);
    });

    await act(async () => {
      await result.current.resumeLatestRun();
    });

    await waitFor(() => {
      expect(result.current.artifacts).toHaveLength(1);
    });
  });

  it('disconnects and clears stale state when workspace changes', async () => {
    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });
    await act(async () => {
      await result.current.startRun({ workspace: 'ws' });
    });
    expect(lastChannelUnsubscribe).toBeDefined();
  });
});
