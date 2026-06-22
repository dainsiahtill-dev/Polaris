import { waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { runStreamingTest } from './streamingTest';

const mockGetBackendInfo = vi.fn();
const runtimeSocketManagerMock = vi.hoisted(() => ({
  getState: vi.fn(() => ({ connected: true, reconnecting: false, error: null, attemptCount: 0 })),
  start: vi.fn(),
  reconnect: vi.fn(),
  subscribeChannels: vi.fn(),
  unsubscribeChannels: vi.fn(),
  registerMessageListener: vi.fn(() => vi.fn()),
}));

vi.mock('../../../../api', () => ({
  getBackendInfo: () => mockGetBackendInfo(),
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

describe('runStreamingTest', () => {
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

  it('runs LLM tests over Nats-JetStream runtime WebSocket events', async () => {
    const report = {
      schema_version: 1,
      test_run_id: 'run-1',
      target: {
        role: 'connectivity',
        provider_id: 'provider-1',
        model: 'model-1',
      },
      suites: {
        connectivity: { ok: true },
      },
      final: {
        ready: true,
        grade: 'PASS',
      },
    };

    const fetchMock = vi.fn(async () => jsonResponse({
      ok: true,
      test_run_id: 'run-1',
      status: 'started',
      channel: 'llm-test:run-1',
      subject: 'hp.runtime.llm.test.run-1',
      transport: 'nats-jetstream',
    }));
    vi.stubGlobal('fetch', fetchMock);

    const onEvent = vi.fn();
    const onSuiteStart = vi.fn();
    const onSuiteComplete = vi.fn();
    const onComplete = vi.fn();

    const resultPromise = runStreamingTest({
      role: 'connectivity',
      providerId: 'provider-1',
      model: 'model-1',
      testRunId: 'run-1',
      suites: ['connectivity'],
      onEvent,
      onSuiteStart,
      onSuiteComplete,
      onComplete,
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:49977/v2/llm/test/jetstream',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
      }),
    );
    expect(runtimeSocketManagerMock.subscribeChannels).toHaveBeenCalledWith([
      { channel: 'llm-test:run-1', tailLines: 0 },
    ]);

    emitTestChunk('llm-test:run-1', 'start', { run_id: 'run-1' });
    emitTestChunk('llm-test:run-1', 'suite_start', { suite: 'connectivity' });
    emitTestChunk('llm-test:run-1', 'suite_result', { suite: 'connectivity', result: { ok: true } });
    emitTestChunk('llm-test:run-1', 'complete', report);

    const result = await resultPromise;

    expect(onSuiteStart).toHaveBeenCalledWith('connectivity');
    expect(onSuiteComplete).toHaveBeenCalledWith('connectivity', true);
    expect(onComplete).toHaveBeenCalledWith(expect.objectContaining({ test_run_id: 'run-1' }));
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ content: '测试完成' }));
    expect(result).toEqual(expect.objectContaining({ test_run_id: 'run-1' }));
  });
});
