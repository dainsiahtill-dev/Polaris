import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useFactoryBench } from './useFactoryBench';

const benchServiceMock = vi.hoisted(() => ({
  listBenchSessions: vi.fn(),
  getBenchSession: vi.fn(),
}));

const runtimeTransportMock = vi.hoisted(() => ({
  subscribeChannels: vi.fn(() => vi.fn()),
  registerMessageHandler: vi.fn(() => vi.fn()),
}));

vi.mock('@/services/benchService', () => benchServiceMock);
vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: () => runtimeTransportMock,
}));

describe('useFactoryBench', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    benchServiceMock.listBenchSessions.mockResolvedValue({
      ok: true,
      data: [
        {
          session_id: 'bench-terminal',
          work_dir: '/tmp/ws',
          project_ids: ['L1-01', 'L1-02'],
          total: 2,
          completed: 0,
          failed: 0,
          status: 'running',
          created_at: '2026-06-18T07:08:00Z',
          updated_at: '2026-06-18T07:09:00Z',
          metadata: {},
        },
      ],
    });
    benchServiceMock.getBenchSession.mockResolvedValue({
      ok: true,
      data: {
        session_id: 'bench-terminal',
        work_dir: '/tmp/ws',
        project_ids: ['L1-01', 'L1-02'],
        total: 2,
        completed: 0,
        failed: 0,
        status: 'running',
        created_at: '2026-06-18T07:08:00Z',
        updated_at: '2026-06-18T07:09:00Z',
        metadata: {},
        events_path: '/tmp/ws/events.jsonl',
        events: [],
      },
    });
  });

  it('applies terminal bench events to the current session and stops streaming', async () => {
    let handler: ((message: unknown) => void) | null = null;
    runtimeTransportMock.registerMessageHandler.mockImplementation((nextHandler: (message: unknown) => void) => {
      handler = nextHandler;
      return vi.fn();
    });

    const { result } = renderHook(() => useFactoryBench());

    await waitFor(() => expect(result.current.currentSession?.session_id).toBe('bench-terminal'));
    expect(result.current.isStreaming).toBe(true);

    act(() => {
      handler?.({
        event: {
          channel: 'event.bench:bench-terminal',
          run_id: 'bench-terminal',
          kind: 'factory_bench.run.cancelled',
          ts: '2026-06-18T07:10:45Z',
          payload: {
            type: 'factory_bench.run.cancelled',
            summary: 'pause LLM connectivity recovery',
            meta: {
              completed: 0,
              failed: 1,
              status: 'cancelled',
              completed_at: '2026-06-18T07:10:45Z',
            },
          },
        },
      });
    });

    await waitFor(() => expect(result.current.currentSession?.status).toBe('cancelled'));
    expect(result.current.currentSession?.failed).toBe(1);
    expect(result.current.currentSession?.completed_at).toBe('2026-06-18T07:10:45Z');
    expect(result.current.isStreaming).toBe(false);
  });

  it('tears down the Nat-JetStream bench subscription on unmount', async () => {
    const unsubscribe = vi.fn();
    const unregisterHandler = vi.fn();
    runtimeTransportMock.subscribeChannels.mockReturnValue(unsubscribe);
    runtimeTransportMock.registerMessageHandler.mockReturnValue(unregisterHandler);

    const { result, unmount } = renderHook(() => useFactoryBench());

    await waitFor(() => expect(result.current.currentSession?.session_id).toBe('bench-terminal'));
    expect(runtimeTransportMock.subscribeChannels).toHaveBeenCalledTimes(1);
    expect(runtimeTransportMock.subscribeChannels).toHaveBeenCalledWith([
      { channel: 'event.bench', tailLines: 0 },
    ]);

    unmount();

    expect(unsubscribe).toHaveBeenCalledTimes(1);
    expect(unregisterHandler).toHaveBeenCalledTimes(1);
    expect(runtimeTransportMock.subscribeChannels).toHaveBeenCalledTimes(1);
  });

  it('auto-selects a newly announced bench session from Nat-JetStream without polling', async () => {
    let handler: ((message: unknown) => void) | null = null;
    runtimeTransportMock.registerMessageHandler.mockImplementation((nextHandler: (message: unknown) => void) => {
      handler = nextHandler;
      return vi.fn();
    });
    benchServiceMock.listBenchSessions.mockResolvedValueOnce({ ok: true, data: [] });

    const { result } = renderHook(() => useFactoryBench());

    await waitFor(() => expect(handler).not.toBeNull());
    act(() => {
      handler?.({
        event: {
          channel: 'event.bench:bench-live',
          run_id: 'bench-live',
          kind: 'factory_bench.session.started',
          ts: '2026-06-18T07:11:00Z',
          payload: {
            type: 'factory_bench.session.started',
            session_id: 'bench-live',
            summary: 'Factory bench session started: bench-live',
            meta: {
              session_id: 'bench-live',
              work_dir: '/tmp/live',
              project_ids: ['L1-01'],
              total: 1,
              completed: 0,
              failed: 0,
              status: 'running',
              created_at: '2026-06-18T07:11:00Z',
              updated_at: '2026-06-18T07:11:00Z',
              metadata: {},
            },
          },
        },
      });
    });

    await waitFor(() => expect(result.current.sessions[0]?.session_id).toBe('bench-live'));
    await waitFor(() => expect(result.current.currentSession?.session_id).toBe('bench-live'));
    expect(benchServiceMock.listBenchSessions).toHaveBeenCalledTimes(1);
    expect(benchServiceMock.getBenchSession).not.toHaveBeenCalledWith('bench-live');
    expect(result.current.events[0]?.type).toBe('factory_bench.session.started');
  });

  it('appends consecutive naked runtime.v2 bench envelopes for the selected session', async () => {
    let handler: ((message: unknown) => void) | null = null;
    runtimeTransportMock.registerMessageHandler.mockImplementation((nextHandler: (message: unknown) => void) => {
      handler = nextHandler;
      return vi.fn();
    });
    benchServiceMock.listBenchSessions.mockResolvedValueOnce({ ok: true, data: [] });

    const { result } = renderHook(() => useFactoryBench());

    await waitFor(() => expect(handler).not.toBeNull());
    act(() => {
      handler?.({
        schema_version: 'runtime.v2',
        channel: 'event.bench:bench-live',
        run_id: 'bench-live',
        kind: 'factory_bench.project.started',
        cursor: 101,
        ts: '2026-06-18T07:11:00Z',
        payload: {
          type: 'factory_bench.project.started',
          summary: 'first pushed event',
          meta: {
            work_dir: '/tmp/live',
            project_ids: ['L1-01', 'L2-01'],
            total: 2,
            completed: 0,
            failed: 0,
            status: 'running',
            updated_at: '2026-06-18T07:11:00Z',
          },
        },
      });
    });

    await waitFor(() => expect(result.current.currentSession?.session_id).toBe('bench-live'));
    expect(result.current.events.at(-1)?.summary).toBe('first pushed event');

    act(() => {
      handler?.({
        schema_version: 'runtime.v2',
        channel: 'event.bench:bench-live',
        run_id: 'bench-live',
        kind: 'factory_bench.project.started',
        cursor: 102,
        ts: '2026-06-18T07:12:00Z',
        payload: {
          type: 'factory_bench.project.started',
          summary: 'second pushed event',
          meta: {
            work_dir: '/tmp/live',
            project_ids: ['L1-01', 'L2-01'],
            total: 2,
            completed: 1,
            failed: 0,
            status: 'running',
            updated_at: '2026-06-18T07:12:00Z',
          },
        },
      });
    });

    await waitFor(() => expect(result.current.events.at(-1)?.summary).toBe('second pushed event'));
    expect(result.current.currentSession?.completed).toBe(1);
  });

  it('notifies the active workspace from live project bench events', async () => {
    let handler: ((message: unknown) => void) | null = null;
    runtimeTransportMock.registerMessageHandler.mockImplementation((nextHandler: (message: unknown) => void) => {
      handler = nextHandler;
      return vi.fn();
    });
    benchServiceMock.listBenchSessions.mockResolvedValueOnce({ ok: true, data: [] });
    const onWorkspaceChange = vi.fn();

    renderHook(() => useFactoryBench({ onWorkspaceChange }));

    await waitFor(() => expect(handler).not.toBeNull());
    act(() => {
      handler?.({
        schema_version: 'runtime.v2',
        channel: 'event.bench:bench-live',
        run_id: 'bench-live',
        kind: 'factory_bench.session.started',
        ts: '2026-06-18T07:10:00Z',
        payload: {
          type: 'factory_bench.session.started',
          summary: 'session started',
          meta: {
            session_id: 'bench-live',
            workspace: '/tmp/ignored-session-workspace',
            project_ids: ['L1-01'],
            total: 1,
            status: 'running',
          },
        },
      });
      handler?.({
        schema_version: 'runtime.v2',
        channel: 'event.bench:bench-live',
        run_id: 'bench-live',
        kind: 'factory_bench.project.phase',
        cursor: 102,
        ts: '2026-06-18T07:12:00Z',
        payload: {
          type: 'factory_bench.project.phase',
          summary: 'L1-01 director running',
          meta: {
            session_id: 'bench-live',
            project_id: 'L1-01',
            workspace: '/tmp/bench/L1-01',
            phase: 'director_dispatch',
            status: 'running',
          },
        },
      });
    });

    await waitFor(() => expect(onWorkspaceChange).toHaveBeenCalledTimes(1));
    expect(onWorkspaceChange).toHaveBeenCalledWith(
      '/tmp/bench/L1-01',
      expect.objectContaining({
        type: 'factory_bench.project.phase',
        session_id: 'bench-live',
      }),
    );
  });

  it('restores the active workspace from hydrated project bench events', async () => {
    const onWorkspaceChange = vi.fn();
    benchServiceMock.getBenchSession.mockResolvedValueOnce({
      ok: true,
      data: {
        session_id: 'bench-terminal',
        work_dir: '/tmp/ws',
        project_ids: ['L1-01'],
        total: 1,
        completed: 1,
        failed: 0,
        status: 'completed',
        created_at: '2026-06-18T07:08:00Z',
        updated_at: '2026-06-18T07:09:00Z',
        metadata: {},
        events_path: '/tmp/ws/events.jsonl',
        events: [
          {
            seq: 12,
            type: 'factory_bench.project.phase',
            summary: 'L1-01 director running',
            meta: {
              session_id: 'bench-terminal',
              project_id: 'L1-01',
              workspace: '/tmp/bench/L1-01',
              phase: 'director_dispatch',
              status: 'running',
            },
            session_id: 'bench-terminal',
          },
        ],
      },
    });

    renderHook(() => useFactoryBench({ onWorkspaceChange }));

    await waitFor(() => expect(onWorkspaceChange).toHaveBeenCalledWith(
      '/tmp/bench/L1-01',
      expect.objectContaining({ type: 'factory_bench.project.phase' }),
    ));
  });

  it('promotes a newer live bench session over an auto-selected running session', async () => {
    let handler: ((message: unknown) => void) | null = null;
    runtimeTransportMock.registerMessageHandler.mockImplementation((nextHandler: (message: unknown) => void) => {
      handler = nextHandler;
      return vi.fn();
    });

    const { result } = renderHook(() => useFactoryBench());

    await waitFor(() => expect(result.current.currentSession?.session_id).toBe('bench-terminal'));

    act(() => {
      handler?.({
        event: {
          channel: 'event.bench:bench-live-newer',
          run_id: 'bench-live-newer',
          kind: 'factory_bench.project.started',
          ts: '2026-06-18T07:12:00Z',
          payload: {
            type: 'factory_bench.project.started',
            summary: 'newer bench session should become active',
            meta: {
              session_id: 'bench-live-newer',
              work_dir: '/tmp/live-newer',
              project_ids: ['L1-01', 'L2-07'],
              total: 2,
              completed: 0,
              failed: 0,
              status: 'running',
              updated_at: '2026-06-18T07:12:00Z',
              metadata: {},
            },
          },
        },
      });
    });

    await waitFor(() => expect(result.current.currentSession?.session_id).toBe('bench-live-newer'));
    expect(result.current.sessions[0]?.session_id).toBe('bench-live-newer');
    expect(result.current.events[0]?.summary).toBe('newer bench session should become active');
  });

  it('keeps a manually selected running session pinned while other live sessions arrive', async () => {
    let handler: ((message: unknown) => void) | null = null;
    runtimeTransportMock.registerMessageHandler.mockImplementation((nextHandler: (message: unknown) => void) => {
      handler = nextHandler;
      return vi.fn();
    });

    const { result } = renderHook(() => useFactoryBench());

    await waitFor(() => expect(result.current.currentSession?.session_id).toBe('bench-terminal'));
    await act(async () => {
      await result.current.select('bench-terminal');
    });

    act(() => {
      handler?.({
        event: {
          channel: 'event.bench:bench-live-other',
          run_id: 'bench-live-other',
          kind: 'factory_bench.project.started',
          ts: '2026-06-18T07:13:00Z',
          payload: {
            type: 'factory_bench.project.started',
            summary: 'other live bench session',
            meta: {
              session_id: 'bench-live-other',
              work_dir: '/tmp/live-other',
              project_ids: ['L8-44'],
              total: 1,
              completed: 0,
              failed: 0,
              status: 'running',
              updated_at: '2026-06-18T07:13:00Z',
              metadata: {},
            },
          },
        },
      });
    });

    await waitFor(() => expect(result.current.sessions[0]?.session_id).toBe('bench-live-other'));
    expect(result.current.currentSession?.session_id).toBe('bench-terminal');
  });
});
