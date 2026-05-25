import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAIDialogue } from '../useAIDialogue';

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/api', () => ({
  apiFetch: apiFetchMock,
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
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  } as Response;
}

function streamResponse(text: string): Response {
  const encoder = new TextEncoder();
  let consumed = false;
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    body: {
      getReader: () => ({
        read: async () => {
          if (consumed) {
            return { done: true, value: undefined };
          }
          consumed = true;
          return { done: false, value: encoder.encode(text) };
        },
      }),
    },
  } as unknown as Response;
}

function findApiCall(path: string): [string, RequestInit | undefined] {
  const call = apiFetchMock.mock.calls.find(([candidate]) => candidate === path);
  expect(call).toBeTruthy();
  return call as [string, RequestInit | undefined];
}

const productWorkspaceQuery = 'workspace=C%3A%2FTemp%2FProduct';
const directorStatusPath = `/v2/role/director/chat/status?${productWorkspaceQuery}`;
const directorStreamPath = `/v2/role/director/chat/stream?${productWorkspaceQuery}`;
const pmStatusPath = `/v2/role/pm/chat/status?${productWorkspaceQuery}`;
const pmStreamPath = `/v2/role/pm/chat/stream?${productWorkspaceQuery}`;

describe('useAIDialogue RoleSession bridge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('creates, attaches, and streams through a RoleSession when desktop task context exists', async () => {
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
      if (path === '/v2/roles/sessions/session-1/messages/stream') {
        return Promise.resolve(streamResponse('event: complete\ndata: {"content":"session answer"}\n\n'));
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

    const [, streamInit] = findApiCall('/v2/roles/sessions/session-1/messages/stream');
    expect(JSON.parse(String(streamInit?.body))).toMatchObject({
      role: 'user',
      content: 'Implement the selected task',
      meta: {
        context: {
          selected_task_id: 'PM-1',
        },
      },
    });
    expect(apiFetchMock).not.toHaveBeenCalledWith(
      directorStreamPath,
      expect.anything(),
    );
    expect(result.current.messages.some((message) => message.content === 'session answer')).toBe(true);
  });

  it('falls back to legacy role chat streaming before a RoleSession exists', async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === pmStatusPath) {
        return Promise.resolve(jsonResponse({ ready: true, configured: true, role: 'pm' }));
      }
      if (path === pmStreamPath) {
        return Promise.resolve(streamResponse('event: complete\ndata: {"content":"pm answer"}\n\n'));
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

    const [, streamInit] = findApiCall(pmStreamPath);
    const payload = JSON.parse(String(streamInit?.body));
    expect(payload).toMatchObject({
      message: 'Plan the work',
    });
    expect(payload.context).toMatchObject({ workspace: 'C:/Temp/Product', history: [] });
    expect(payload.context).toHaveProperty('conversation_id', null);
    expect(result.current.messages.some((message) => message.content === 'pm answer')).toBe(true);
  });
});
