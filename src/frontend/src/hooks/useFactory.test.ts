import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

const startFactoryRunMock = vi.fn();
const stopFactoryRunMock = vi.fn();
const getFactoryRunMock = vi.fn();
const getFactoryRunArtifactsMock = vi.fn();
const listFactoryRunsMock = vi.fn();
const connectFactoryStreamMock = vi.fn();
const toastSuccessMock = vi.fn();
const toastErrorMock = vi.fn();

let lastHandlers: Record<string, ((payload?: unknown) => void) | undefined> | null = null;
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
  getFactoryRun: (...args: unknown[]) => getFactoryRunMock(...args),
  getFactoryRunArtifacts: (...args: unknown[]) => getFactoryRunArtifactsMock(...args),
  listFactoryRuns: (...args: unknown[]) => listFactoryRunsMock(...args),
  connectFactoryStream: (...args: unknown[]) => connectFactoryStreamMock(...args),
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
    lastHandlers = null;
    closeMock = vi.fn();

    startFactoryRunMock.mockResolvedValue({ ok: true, data: baseRun });
    stopFactoryRunMock.mockResolvedValue({
      ok: true,
      data: { ...baseRun, status: 'cancelled', phase: 'cancelled', progress: 25 },
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
    connectFactoryStreamMock.mockImplementation(async (_runId, handlers) => {
      lastHandlers = handlers as Record<string, ((payload?: unknown) => void) | undefined>;
      handlers.onOpen?.();
      return {
        eventSource: {} as EventSource,
        close: closeMock,
      };
    });
  });

  it('starts a run and auto-connects the stream', async () => {
    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.startRun({ workspace: 'X:/workspace', run_director: true });
    });

    expect(startFactoryRunMock).toHaveBeenCalledTimes(1);
    expect(connectFactoryStreamMock).toHaveBeenCalledTimes(1);
    expect(result.current.currentRun?.run_id).toBe('run-1');
    expect(result.current.isStreaming).toBe(true);
  });

  it('fetches artifacts and summary when a current run is available', async () => {
    getFactoryRunArtifactsMock.mockImplementation(async (runId: string) => ({
      ok: true,
      data: {
        run_id: runId,
        artifacts: [{ name: 'director-audit.json', path: '.polaris/runs/run-1/artifacts/director-audit.json', size: 512 }],
        summary_md: 'Director audit passed.',
        summary_json: { director_status: 'passed' },
      },
    }));

    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.startRun({ workspace: 'X:/workspace', run_director: true });
    });

    await waitFor(() => {
      expect(getFactoryRunArtifactsMock).toHaveBeenCalledWith('run-1');
    });

    await waitFor(() => {
      expect(result.current.currentRun?.artifacts?.[0]?.name).toBe('director-audit.json');
    });

    expect(result.current.artifacts).toHaveLength(1);
    expect(result.current.summaryMd).toBe('Director audit passed.');
    expect(result.current.summaryJson).toEqual({ director_status: 'passed' });
  });

  it('replaces currentRun from status events and stops on done', async () => {
    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.startRun({ workspace: 'X:/workspace', run_director: true });
    });

    await act(async () => {
      lastHandlers?.onStatus?.({
        ...baseRun,
        phase: 'implementation',
        current_stage: 'director_dispatch',
        progress: 60,
      });
    });

    expect(result.current.currentRun?.phase).toBe('implementation');
    expect(result.current.currentRun?.current_stage).toBe('director_dispatch');

    await act(async () => {
      lastHandlers?.onDone?.({
        ...baseRun,
        phase: 'completed',
        status: 'completed',
        progress: 100,
      });
    });

    expect(result.current.currentRun?.status).toBe('completed');
    expect(result.current.isStreaming).toBe(false);
    expect(closeMock).toHaveBeenCalled();
  });

  it('refreshes artifacts after stream done', async () => {
    getFactoryRunArtifactsMock
      .mockResolvedValueOnce({
        ok: true,
        data: {
          run_id: 'run-1',
          artifacts: [],
          summary_md: null,
          summary_json: null,
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          run_id: 'run-1',
          artifacts: [{ name: 'handover.md', path: '.polaris/runs/run-1/artifacts/handover.md', size: 2048 }],
          summary_md: 'Final handover is ready.',
          summary_json: { status: 'completed' },
        },
      });

    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.startRun({ workspace: 'X:/workspace', run_director: true });
    });

    await waitFor(() => {
      expect(getFactoryRunArtifactsMock).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      lastHandlers?.onDone?.({
        ...baseRun,
        phase: 'completed',
        status: 'completed',
        progress: 100,
      });
    });

    await waitFor(() => {
      expect(result.current.currentRun?.artifacts?.[0]?.name).toBe('handover.md');
    });

    expect(getFactoryRunArtifactsMock).toHaveBeenCalledTimes(2);
    expect(result.current.summaryMd).toBe('Final handover is ready.');
  });

  it('uses stop response as the terminal snapshot', async () => {
    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.startRun({ workspace: 'X:/workspace', run_director: true });
    });

    await act(async () => {
      await result.current.stopRun('run-1', 'operator stop');
    });

    expect(stopFactoryRunMock).toHaveBeenCalledWith('run-1', 'operator stop');
    expect(result.current.currentRun?.status).toBe('cancelled');
    expect(result.current.isStreaming).toBe(false);
  });

  it('falls back to fetch and reconnects after connection errors', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useFactory(), { wrapper: createWrapper() });

    await act(async () => {
      await result.current.startRun({ workspace: 'X:/workspace', run_director: true });
    });

    getFactoryRunMock.mockResolvedValueOnce({
      ok: true,
      data: {
        ...baseRun,
        phase: 'implementation',
        current_stage: 'director_dispatch',
        progress: 55,
      },
    });

    await act(async () => {
      lastHandlers?.onConnectionError?.();
    });

    await waitFor(() => {
      expect(getFactoryRunMock).toHaveBeenCalledWith('run-1');
    });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    await waitFor(() => {
      expect(connectFactoryStreamMock).toHaveBeenCalledTimes(2);
    });
  });

  it('resumes the latest non-terminal run for the active workspace', async () => {
    listFactoryRunsMock.mockResolvedValueOnce({
      ok: true,
      data: [{ ...baseRun, run_id: 'latest-run', phase: 'implementation', current_stage: 'director_dispatch' }],
    });

    const { result } = renderHook(() => useFactory({ workspace: 'X:/workspace' }), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(listFactoryRunsMock).toHaveBeenCalledWith(1);
    });

    await waitFor(() => {
      expect(result.current.currentRun?.run_id).toBe('latest-run');
    });

    expect(connectFactoryStreamMock).toHaveBeenCalledWith(
      'latest-run',
      expect.objectContaining({
        onStatus: expect.any(Function),
      }),
    );
  });

  it('disconnects and clears stale state when workspace changes', async () => {
    listFactoryRunsMock
      .mockResolvedValueOnce({ ok: true, data: [{ ...baseRun, run_id: 'run-a' }] })
      .mockResolvedValueOnce({ ok: true, data: [] });

    const { result, rerender } = renderHook(
      ({ workspace }) => useFactory({ workspace }),
      {
        initialProps: { workspace: 'X:/workspace-a' },
        wrapper: createWrapper(),
      },
    );

    await waitFor(() => {
      expect(result.current.currentRun?.run_id).toBe('run-a');
    });

    rerender({ workspace: 'X:/workspace-b' });

    await waitFor(() => {
      expect(listFactoryRunsMock).toHaveBeenLastCalledWith(1);
    });

    await waitFor(() => {
      expect(result.current.currentRun).toBeNull();
    });

    expect(closeMock).toHaveBeenCalled();
  });
});
