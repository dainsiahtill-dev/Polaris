import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAIDialogue } from '../useAIDialogue';

const apiFetchMock = vi.hoisted(() => vi.fn());
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
  apiFetch: apiFetchMock,
}));

vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: () => runtimeTransportMock,
}));

vi.mock('@/app/utils/devLogger', () => ({
  devLogger: {
    debug: vi.fn(),
    error: vi.fn(),
    warn: vi.fn(),
  },
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

function findApiCall(path: string): [string, RequestInit | undefined] {
  const call = apiFetchMock.mock.calls.find(([candidate]) => candidate === path);
  expect(call).toBeTruthy();
  return call as [string, RequestInit | undefined];
}

const productWorkspaceQuery = 'workspace=C%3A%2FTemp%2FProduct';
const directorStatusPath = `/v2/role/director/chat/status?${productWorkspaceQuery}`;
const directorJetstreamPath = `/v2/role/director/chat/jetstream?${productWorkspaceQuery}`;
const pmStatusPath = `/v2/role/pm/chat/status?${productWorkspaceQuery}`;
const pmJetstreamPath = `/v2/role/pm/chat/jetstream?${productWorkspaceQuery}`;
const pmLegacyStreamPath = `/v2/role/pm/chat/stream?${productWorkspaceQuery}`;

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

describe('useAIDialogue RoleSession bridge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    runtimeTransportMock.connected = true;
  });

  it('creates, attaches, and streams through a RoleSession Nat-JetStream channel when desktop task context exists', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === directorStatusPath) {
        return Promise.resolve(jsonResponse({ ready: true, configured: true, role: 'director' }));
      }
      if (path === '/v2/roles/sessions') {
        return Promise.resolve(jsonResponse({ ok: true, session: { id: 'session-1' } }));
      }
      if (path === '/v2/roles/sessions/session-1/actions/attach') {
        return Promise.resolve(jsonResponse({ ok: true, attachment: { id: 'attach-1' } }));
      }
      if (path === '/v2/roles/sessions/session-1/messages/jetstream') {
        return Promise.resolve(jsonResponse({
          ok: true,
          session_id: 'session-1',
          status: 'started',
          channel: 'chat:session-1',
          subject: 'hp.runtime.chat.session-1',
          transport: 'nat-jetstream',
        }));
      }
      return Promise.resolve(jsonResponse({ ok: true }));
    });

    const onSessionChange = vi.fn();
    const { result } = renderHook(() => useAIDialogue({
      role: 'director',
      roleName: 'Director',
      welcomeMessage: 'ready',
      workspace: 'C:/Temp/Product',
      context: { selected_task_id: 'PM-1' },
      attachmentMode: 'attached_readonly',
      attachedTaskId: 'PM-1',
      onSessionChange,
    }));

    await waitFor(() => expect(result.current.isChatReady).toBe(true));
    await waitFor(() => expect(onSessionChange).toHaveBeenCalledWith('session-1'));
    await waitFor(() => findApiCall('/v2/roles/sessions/session-1/actions/attach'));

    const [, createInit] = findApiCall('/v2/roles/sessions');
    expect(JSON.parse(String(createInit?.body))).toMatchObject({
      role: 'director',
      host_kind: 'electron_workbench',
      workspace: 'C:/Temp/Product',
      attachment_mode: 'attached_readonly',
      context_config: { selected_task_id: 'PM-1' },
    });

    const [, attachInit] = findApiCall('/v2/roles/sessions/session-1/actions/attach');
    expect(JSON.parse(String(attachInit?.body))).toMatchObject({
      run_id: null,
      task_id: 'PM-1',
      mode: 'attached_readonly',
    });

    act(() => {
      result.current.setInputValue('Implement the selected task');
    });

    await waitFor(() => expect(result.current.inputValue).toBe('Implement the selected task'));
    await act(async () => {
      await result.current.handleSend();
    });

    const [, streamInit] = findApiCall('/v2/roles/sessions/session-1/messages/jetstream');
    expect(JSON.parse(String(streamInit?.body))).toMatchObject({
      role: 'user',
      content: 'Implement the selected task',
      meta: {
        context: {
          selected_task_id: 'PM-1',
        },
      },
    });
    expect(runtimeTransportMock.subscribeChannels).toHaveBeenCalledWith([
      { channel: 'chat:session-1', tailLines: 0 },
    ]);
    expect(apiFetchMock).not.toHaveBeenCalledWith(
      directorJetstreamPath,
      expect.anything(),
    );
    expect(apiFetchMock).not.toHaveBeenCalledWith(
      '/v2/roles/sessions/session-1/messages/stream',
      expect.anything(),
    );

    emitChatChunk('chat:session-1', 'complete', { content: 'session answer' });

    await waitFor(() => {
      expect(result.current.messages.some((message) => message.content === 'session answer')).toBe(true);
      expect(result.current.isLoading).toBe(false);
    });
  });

  it('uses role chat Nat-JetStream before a RoleSession exists', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === pmStatusPath) {
        return Promise.resolve(jsonResponse({ ready: true, configured: true, role: 'pm' }));
      }
      if (path === pmJetstreamPath) {
        return Promise.resolve(jsonResponse({
          ok: true,
          session_id: 'pm-chat-session',
          status: 'started',
          channel: 'chat:pm-chat-session',
          subject: 'hp.runtime.chat.pm-chat-session',
          transport: 'nat-jetstream',
        }));
      }
      return Promise.resolve(jsonResponse({ ok: true }));
    });

    const { result } = renderHook(() => useAIDialogue({
      role: 'pm',
      roleName: 'PM',
      welcomeMessage: 'ready',
      hostKind: 'cli',
      workspace: 'C:/Temp/Product',
    }));

    await waitFor(() => expect(result.current.isChatReady).toBe(true));
    act(() => {
      result.current.setInputValue('Plan the work');
    });

    await waitFor(() => expect(result.current.inputValue).toBe('Plan the work'));
    await act(async () => {
      await result.current.handleSend();
    });

    const [, streamInit] = findApiCall(pmJetstreamPath);
    const payload = JSON.parse(String(streamInit?.body));
    expect(payload).toMatchObject({
      message: 'Plan the work',
    });
    expect(payload.context).toMatchObject({ workspace: 'C:/Temp/Product', history: [] });
    expect(payload.context).toHaveProperty('conversation_id', null);
    expect(apiFetchMock).not.toHaveBeenCalledWith(
      pmLegacyStreamPath,
      expect.anything(),
    );
    expect(runtimeTransportMock.subscribeChannels).toHaveBeenCalledWith([
      { channel: 'chat:pm-chat-session', tailLines: 0 },
    ]);

    emitChatChunk('chat:pm-chat-session', 'complete', { content: 'pm answer' });

    await waitFor(() => {
      expect(result.current.messages.some((message) => message.content === 'pm answer')).toBe(true);
      expect(result.current.isLoading).toBe(false);
    });
  });
});
