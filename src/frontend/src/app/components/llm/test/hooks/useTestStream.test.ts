import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTestStream } from './useTestStream';

const mockGetBackendInfo = vi.hoisted(() => vi.fn());
const runtimeSocketManagerMock = vi.hoisted(() => ({
  getState: vi.fn(() => ({ connected: true, reconnecting: false, error: null, attemptCount: 0 })),
  start: vi.fn(),
  reconnect: vi.fn(),
  subscribeChannels: vi.fn(),
  unsubscribeChannels: vi.fn(),
  registerMessageListener: vi.fn(() => vi.fn()),
}));

vi.mock('@/api', () => ({
  getBackendInfo: () => mockGetBackendInfo(),
}));

vi.mock('@/app/utils/devLogger', () => ({
  devLogger: {
    debug: vi.fn(),
    error: vi.fn(),
    warn: vi.fn(),
  },
}));

vi.mock('@/runtime/transport', () => ({
  runtimeSocketManager: runtimeSocketManagerMock,
}));

function jsonResponse(payload: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? 'OK' : 'Error',
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  } as Response;
}

function emitTestChunk(channel: string, type: string, data: Record<string, unknown>) {
  const listener = runtimeSocketManagerMock.registerMessageListener.mock.calls.at(-1)?.[0];
  expect(listener?.handler).toBeTypeOf('function');
  listener.handler({
    channel,
    payload: { type, data, seq: 0 },
  });
}

describe('useTestStream', () => {
  beforeEach(() => {
    mockGetBackendInfo.mockResolvedValue({
      baseUrl: 'http://127.0.0.1:49977',
      token: 'test-token',
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('starts LLM tests through the Nats-JetStream endpoint, not the legacy stream endpoint', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({
      ok: true,
      test_run_id: 'hook-run-1',
      status: 'started',
      channel: 'llm-test:hook-run-1',
      subject: 'hp.runtime.llm.test.hook-run-1',
      transport: 'nats-jetstream',
    }));
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useTestStream());

    let streamPromise: Promise<void> | undefined;
    act(() => {
      streamPromise = result.current.startStream({
        role: 'connectivity',
        providerId: 'provider-1',
        model: 'model-1',
        suites: ['connectivity'],
      });
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:49977/v2/llm/test/jetstream',
      expect.objectContaining({ method: 'POST' }),
    );

    emitTestChunk('llm-test:hook-run-1', 'complete', {
      schema_version: 1,
      test_run_id: 'hook-run-1',
      target: {
        role: 'connectivity',
        provider_id: 'provider-1',
        model: 'model-1',
      },
      suites: {},
      final: {
        ready: true,
        grade: 'PASS',
      },
    });

    await act(async () => {
      await streamPromise;
    });
  });
});
