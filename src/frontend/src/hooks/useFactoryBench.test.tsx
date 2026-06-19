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
    benchServiceMock.getBenchSession.mockResolvedValueOnce({
      ok: true,
      data: {
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
        events_path: '/tmp/live/events.jsonl',
        events: [],
      },
    });

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
    expect(benchServiceMock.getBenchSession).toHaveBeenCalledWith('bench-live');
  });
});
