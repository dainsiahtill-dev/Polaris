import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRoleChat } from '../useRoleChat';

const apiFetchMock = vi.hoisted(() => vi.fn());
const apiGetMock = vi.hoisted(() => vi.fn());
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
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

vi.mock('@/services/apiClient', () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPost: vi.fn(),
}));

vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: () => runtimeTransportMock,
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

function emitChatChunk(channel: string, type: string, data: Record<string, unknown>) {
  const handler = runtimeTransportMock.registerMessageHandler.mock.calls.at(-1)?.[0];
  expect(handler).toBeTypeOf('function');
  act(() => {
    handler?.({
      channel,
      payload: { type, data, seq: 0 },
    });
  });
}

describe('useRoleChat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    runtimeTransportMock.connected = true;
    apiGetMock.mockResolvedValue({
      ok: true,
      data: {
        ready: true,
        role: 'pm',
      },
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('streams role chat through Nats-JetStream instead of the legacy SSE stream endpoint', async () => {
    apiFetchMock.mockResolvedValue(jsonResponse({
      ok: true,
      session_id: 'pm-chat-1',
      status: 'started',
      channel: 'chat:pm-chat-1',
      subject: 'hp.runtime.chat.pm-chat-1',
      transport: 'nats-jetstream',
    }));

    const { result } = renderHook(() => useRoleChat({
      role: 'pm',
      welcomeMessage: 'ready',
      context: { workspace: 'C:/Temp/Product' },
    }));

    await waitFor(() => expect(result.current.chatStatus?.ready).toBe(true));

    act(() => {
      result.current.setInputValue('Plan the work');
    });
    await waitFor(() => expect(result.current.inputValue).toBe('Plan the work'));

    await act(async () => {
      await result.current.sendMessage();
    });

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/role/pm/chat/jetstream?workspace=C%3A%2FTemp%2FProduct',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    expect(runtimeTransportMock.subscribeChannels).toHaveBeenCalledWith([
      { channel: 'chat:pm-chat-1', tailLines: 0 },
    ]);

    emitChatChunk('chat:pm-chat-1', 'content_chunk', { content: 'hello' });
    emitChatChunk('chat:pm-chat-1', 'complete', { content: 'hello world' });

    await waitFor(() => {
      expect(result.current.messages.some((message) => message.content === 'hello world')).toBe(true);
      expect(result.current.isLoading).toBe(false);
    });
  });
});
