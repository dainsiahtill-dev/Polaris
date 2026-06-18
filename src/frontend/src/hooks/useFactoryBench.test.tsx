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

    const { result } = renderHook(() => useFactoryBench({ pollIntervalMs: 60_000 }));

    await waitFor(() => expect(result.current.currentSession?.session_id).toBe('bench-terminal'));
    expect(result.current.isStreaming).toBe(true);

    act(() => {
      handler?.({
        event: {
          channel: 'event.bench',
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
});
