import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useNDJSONStream } from './useNDJSONStream';

const getBackendInfoMock = vi.hoisted(() => vi.fn());
const runtimeTransportMock = vi.hoisted(() => ({
  connected: true,
  reconnecting: false,
  error: null as string | null,
  attemptCount: 0,
  subscribeChannels: vi.fn(() => vi.fn()),
  sendCommand: vi.fn(() => true),
  getLastCursor: vi.fn(() => 0),
  reconnect: vi.fn(),
  registerMessageHandler: vi.fn(() => vi.fn()),
}));

vi.mock('@/api', () => ({
  getBackendInfo: () => getBackendInfoMock(),
}));

vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: () => runtimeTransportMock,
  useConnectionState: () => ({ connected: runtimeTransportMock.connected }),
  useTransportActions: () => ({
    reconnect: runtimeTransportMock.reconnect,
    subscribeChannels: runtimeTransportMock.subscribeChannels,
  }),
  useMessageHandler: () => ({
    registerMessageHandler: runtimeTransportMock.registerMessageHandler,
  }),
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

function emitDocsChunk(channel: string, type: string, data: Record<string, unknown>) {
  const handler = runtimeTransportMock.registerMessageHandler.mock.calls.at(-1)?.[0];
  expect(handler).toBeTypeOf('function');
  act(() => {
    handler?.({
      channel,
      payload: { type, data, seq: 0 },
    });
  });
}

describe('useNDJSONStream', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getBackendInfoMock.mockResolvedValue({
      baseUrl: 'http://127.0.0.1:49977',
      token: 'test-token',
    });
    runtimeTransportMock.connected = true;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('starts docs init streams through Nats-JetStream runtime events', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        ok: true,
        session_id: 'docs-dialogue-1',
        status: 'started',
        channel: 'docs-init-dialogue:docs-dialogue-1',
        subject: 'hp.runtime.docs.init.dialogue.docs-dialogue-1',
        transport: 'nats-jetstream',
      })
    );
    vi.stubGlobal('fetch', fetchMock);
    const onEvent = vi.fn();
    const onComplete = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useNDJSONStream({ onEvent, onComplete, onError }));

    await act(async () => {
      await result.current.startStream('/v2/docs/init/dialogue/jetstream', {
        session_id: 'docs-dialogue-1',
        message: '请规划',
      });
    });

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:49977/v2/docs/init/dialogue/jetstream',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
      }),
    );
    expect(runtimeTransportMock.subscribeChannels).toHaveBeenCalledWith([
      { channel: 'docs-init-dialogue:docs-dialogue-1', tailLines: 0 },
    ]);

    emitDocsChunk('docs-init-dialogue:docs-dialogue-1', 'thinking_chunk', { content: '分析' });
    emitDocsChunk('docs-init-dialogue:docs-dialogue-1', 'complete', { reply: '完成' });

    expect(onEvent).toHaveBeenCalledWith({ type: 'thinking_chunk', data: { content: '分析' } });
    expect(onComplete).toHaveBeenCalledWith({ reply: '完成' });
    expect(onError).not.toHaveBeenCalled();
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
  });
});
