import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AIDialoguePanel } from '../AIDialoguePanel';

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

describe('AIDialoguePanel RoleSession visibility', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    runtimeTransportMock.connected = true;
    let sessionIndex = 0;
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === `/v2/role/pm/chat/status?${productWorkspaceQuery}`) {
        return Promise.resolve(jsonResponse({ ready: true, configured: true, role: 'pm' }));
      }
      if (path === '/v2/roles/capabilities/pm?host_kind=electron_workbench') {
        return Promise.resolve(jsonResponse({
          ok: true,
          role: 'pm',
          capabilities: {
            electron_workbench: ['read_files', 'write_tasks', 'export_workflow'],
          },
        }));
      }
      if (path === '/v2/roles/sessions') {
        sessionIndex += 1;
        return Promise.resolve(jsonResponse({ ok: true, session: { id: `session-${sessionIndex}` } }));
      }
      if (path === '/v2/roles/sessions/session-1') {
        return Promise.resolve(jsonResponse({
          ok: true,
          session: {
            id: 'session-1',
            title: 'PM desktop session',
            role: 'pm',
            host_kind: 'electron_workbench',
            attachment_mode: 'attached_readonly',
            state: 'active',
            message_count: 4,
            updated_at: '2026-05-23T00:00:02Z',
          },
        }));
      }
      if (path === '/v2/roles/sessions/session-2') {
        return Promise.resolve(jsonResponse({
          ok: true,
          session: {
            id: 'session-2',
            title: 'PM fresh session',
            role: 'pm',
            host_kind: 'electron_workbench',
            attachment_mode: 'attached_readonly',
            state: 'active',
            message_count: 0,
          },
        }));
      }
      if (path === '/v2/roles/sessions/session-old') {
        return Promise.resolve(jsonResponse({
          ok: true,
          session: {
            id: 'session-old',
            title: 'Previous planning thread',
            role: 'pm',
            host_kind: 'electron_workbench',
            attachment_mode: 'attached_readonly',
            state: 'active',
            message_count: 2,
          },
        }));
      }
      if (path.startsWith('/v2/roles/sessions?')) {
        return Promise.resolve(jsonResponse({
          ok: true,
          sessions: [
            { id: 'session-1', title: 'Current session', state: 'active', updated_at: '2026-05-23T00:00:00Z' },
            { id: 'session-old', title: 'Previous planning thread', state: 'active', updated_at: '2026-05-22T00:00:00Z' },
          ],
          total: 2,
        }));
      }
      if (path === '/v2/roles/sessions/session-old/messages?limit=100&offset=0') {
        return Promise.resolve(jsonResponse({
          ok: true,
          session: { id: 'session-old' },
          messages: [
            { id: 'msg-user', role: 'user', content: 'old question', created_at: '2026-05-22T00:00:00Z' },
            { id: 'msg-assistant', role: 'assistant', content: 'old answer', created_at: '2026-05-22T00:00:01Z' },
          ],
        }));
      }
      if (path === '/v2/roles/sessions/session-1/messages?limit=5&offset=0') {
        return Promise.resolve(jsonResponse({
          ok: true,
          session: { id: 'session-1' },
          messages: [
            {
              id: 'msg-evidence-1',
              role: 'assistant',
              content: 'Persisted PM evidence message',
              created_at: '2026-05-23T00:00:03Z',
            },
          ],
        }));
      }
      if (path === '/v2/roles/sessions/session-1/artifacts') {
        return Promise.resolve(jsonResponse({
          ok: true,
          artifacts: [
            {
              id: 'artifact-1',
              type: 'directive',
              content: 'Use persisted PM directive',
              metadata: { source: 'test' },
            },
          ],
        }));
      }
      if (path === '/v2/roles/sessions/session-1/audit?limit=5&offset=0') {
        return Promise.resolve(jsonResponse({
          ok: true,
          audit_events: [
            {
              id: 'audit-1',
              event_type: 'message_sent',
              timestamp: '2026-05-23T00:00:01Z',
              payload: { content: 'Plan work' },
            },
          ],
        }));
      }
      if (path === '/v2/roles/sessions/session-1/actions/detach') {
        return Promise.resolve(jsonResponse({
          ok: true,
          session: {
            id: 'session-1',
            title: 'PM desktop session',
            role: 'pm',
            host_kind: 'electron_workbench',
            attachment_mode: 'isolated',
            attached_run_id: null,
            attached_task_id: null,
            state: 'active',
            message_count: 4,
          },
        }));
      }
      if (path.startsWith('/v2/roles/sessions/session-1/memory/search?')) {
        return Promise.resolve(jsonResponse({
          ok: true,
          session_id: 'session-1',
          query: 'PM-1',
          total: 1,
          items: [
            {
              id: 'memory-artifact-1',
              kind: 'artifact',
              entity: 'pm.directive',
              text: 'Persisted PM planning memory',
              metadata: { source: 'context-os' },
            },
          ],
        }));
      }
      if (path === '/v2/roles/sessions/session-1/memory/artifacts/memory-artifact-1') {
        return Promise.resolve(jsonResponse({
          ok: true,
          session_id: 'session-1',
          artifact: {
            artifact_id: 'memory-artifact-1',
            content: 'Persisted PM planning memory detail',
          },
        }));
      }
      if (path === '/v2/roles/sessions/session-1/actions/export') {
        const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
        if (body.format === 'markdown') {
          return Promise.resolve(jsonResponse({
            ok: true,
            export: {
              markdown: '# PM desktop session\n\nPersisted PM snapshot markdown',
            },
          }));
        }
        return Promise.resolve(jsonResponse({
          ok: true,
          export: {
            id: 'session-1',
            title: 'PM desktop session',
            messages: [
              { role: 'user', content: 'Plan work' },
            ],
          },
        }));
      }
      if (path.endsWith('/actions/attach')) {
        return Promise.resolve(jsonResponse({ ok: true, attachment: { id: 'attach-1' } }));
      }
      if (path.endsWith('/actions/export-to-workflow')) {
        return Promise.resolve(jsonResponse({
          ok: true,
          exported_to: 'pm',
          run_id: 'pm-run-1',
          session_id: 'session-1',
          artifact_count: 2,
          message_count: 3,
        }));
      }
      if (path.startsWith('/v2/conversations?')) {
        return Promise.resolve(jsonResponse({ conversations: [], total: 0 }));
      }
      return Promise.resolve(jsonResponse({ ok: true }));
    });
  });

  it('shows persisted session evidence and can create a fresh RoleSession', async () => {
    const onSessionChange = vi.fn();

    render(
      <AIDialoguePanel
        dialogueRole="pm"
        roleDisplayName="PM"
        workspace="C:/Temp/Product"
        attachmentMode="attached_readonly"
        attachedTaskId="PM-1"
        context={{ selected_task_id: 'PM-1' }}
        welcomeMessage="PM ready"
        workflowExportTarget="pm"
        workflowExportLabel="导出PM"
        onSessionChange={onSessionChange}
      />,
    );

    expect(screen.getByTestId('ai-role-session-strip')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('ai-role-session-id')).toHaveTextContent('RS session-1'));
    expect(screen.getByTestId('ai-role-session-id')).toHaveAttribute('title', 'session-1');
    expect(screen.getByTestId('ai-role-session-attachment')).toHaveTextContent('任务');
    expect(screen.getByTestId('ai-role-session-attachment')).toHaveAttribute('title', 'PM-1');
    await waitFor(() => expect(screen.getByTestId('ai-role-capability-chip')).toHaveTextContent('3项'));
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/capabilities/pm?host_kind=electron_workbench');
    await waitFor(() => expect(screen.getByTestId('ai-role-session-detail-chip')).toHaveTextContent('就绪'));
    expect(screen.getByTestId('ai-role-session-message-chip')).toHaveTextContent('4');
    expect(screen.getByTestId('ai-role-session-message-chip')).toHaveAttribute('title', 'messages=4');
    expect(screen.getByTestId('ai-role-session-strip')).toHaveClass('shrink-0');
    expect(screen.getByTestId('ai-role-session-status-row')).toHaveClass('flex-wrap', 'overflow-hidden');
    expect(screen.getByTestId('ai-role-session-status-row')).not.toHaveClass('overflow-x-auto');
    expect(screen.getByTestId('ai-role-session-actions')).toHaveClass('justify-end', 'overflow-x-auto', 'border-t');
    expect(screen.getByTestId('ai-role-session-actions')).not.toHaveClass('flex-wrap', 'overflow-hidden');
    expect(screen.getByTestId('ai-role-session-detail-chip')).toHaveClass('overflow-hidden', 'max-w-[5.5rem]');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-1');
    expect(onSessionChange).toHaveBeenCalledWith('session-1');

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/roles/sessions/session-1/actions/attach',
      expect.objectContaining({ method: 'POST' }),
    ));
    fireEvent.click(screen.getByTestId('ai-role-session-detach'));

    await waitFor(() => expect(screen.getByTestId('ai-role-session-detach-status')).toHaveTextContent('已解除工作流附着'));
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/roles/sessions/session-1/actions/detach',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(screen.queryByTestId('ai-role-session-attachment')).not.toBeInTheDocument();
    const sessionOneAttachCalls = apiFetchMock.mock.calls.filter(
      ([path]) => path === '/v2/roles/sessions/session-1/actions/attach',
    );
    expect(sessionOneAttachCalls).toHaveLength(1);

    fireEvent.click(screen.getByTestId('ai-role-session-export'));

    await waitFor(() => expect(screen.getByTestId('ai-role-session-export-status')).toHaveTextContent('Run pm-run-1'));
    expect(screen.getByTestId('ai-role-session-export-status')).toHaveAttribute(
      'title',
      'pm-run-1 · artifacts=2 · messages=3',
    );
    const [, exportInit] = findApiCall('/v2/roles/sessions/session-1/actions/export-to-workflow');
    expect(JSON.parse(String(exportInit?.body))).toMatchObject({
      target: 'pm',
      export_kind: 'session_bundle',
      include_audit_log: true,
    });

    fireEvent.click(screen.getByTestId('ai-role-session-snapshot-toggle'));

    await waitFor(() => expect(screen.getByTestId('ai-role-session-snapshot-panel')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('ai-role-session-snapshot-preview')).toHaveTextContent('PM desktop session'));
    const [, snapshotJsonInit] = findApiCall('/v2/roles/sessions/session-1/actions/export');
    expect(JSON.parse(String(snapshotJsonInit?.body))).toMatchObject({
      include_messages: true,
      format: 'json',
    });
    fireEvent.click(screen.getByTestId('ai-role-session-snapshot-format-markdown'));
    await waitFor(() => expect(screen.getByTestId('ai-role-session-snapshot-preview')).toHaveTextContent('Persisted PM snapshot markdown'));
    expect(apiFetchMock.mock.calls.some(([path, init]) => (
      path === '/v2/roles/sessions/session-1/actions/export'
      && JSON.parse(String(init?.body)).format === 'markdown'
    ))).toBe(true);

    fireEvent.click(screen.getByTestId('ai-role-session-evidence-toggle'));

    await waitFor(() => expect(screen.getByTestId('role-session-evidence-panel')).toBeInTheDocument());
    expect(screen.queryByTestId('ai-role-session-evidence-panel')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('role-session-evidence-messages')).toHaveTextContent('assistant: Persisted PM evidence message'));
    expect(screen.getByTestId('role-session-evidence-artifacts')).toHaveTextContent('directive: artifact-1');
    expect(screen.getByTestId('role-session-evidence-audit')).toHaveTextContent('message_sent');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-1/messages?limit=5&offset=0');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-1/artifacts');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-1/audit?limit=5&offset=0');

    fireEvent.click(screen.getByTestId('ai-role-session-memory-toggle'));

    await waitFor(() => expect(screen.getByTestId('ai-role-session-memory-panel')).toBeInTheDocument());
    expect(screen.getByTestId('ai-role-session-memory-query')).toHaveValue('PM-1');
    await waitFor(() => expect(screen.getByTestId('ai-role-session-memory-row')).toHaveTextContent('Persisted PM planning memory'));
    expect(apiFetchMock.mock.calls.some(
      ([path]) => String(path).startsWith('/v2/roles/sessions/session-1/memory/search?'),
    )).toBe(true);
    fireEvent.click(screen.getByTestId('ai-role-session-memory-row'));
    await waitFor(() => expect(screen.getByTestId('ai-role-session-memory-detail')).toHaveTextContent('Persisted PM planning memory detail'));
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-1/memory/artifacts/memory-artifact-1');

    fireEvent.click(screen.getByTestId('ai-role-session-list'));

    await waitFor(() => expect(screen.getByTestId('ai-role-session-list-panel')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('ai-role-session-option-session-old'));

    await waitFor(() => expect(screen.getByTestId('ai-role-session-id')).toHaveAttribute('title', 'session-old'));
    await waitFor(() => expect(screen.getByTestId('ai-role-session-detail-chip')).toHaveTextContent('就绪'));
    expect(screen.getByTestId('ai-role-session-message-chip')).toHaveTextContent('2');
    expect(screen.getByText('old answer')).toBeInTheDocument();
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-old/messages?limit=100&offset=0');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-old');
    expect(onSessionChange).toHaveBeenCalledWith('session-old');

    fireEvent.click(screen.getByTestId('ai-role-session-new'));

    await waitFor(() => expect(screen.getByTestId('ai-role-session-id')).toHaveAttribute('title', 'session-2'));
    expect(onSessionChange).toHaveBeenCalledWith(null);
    expect(onSessionChange).toHaveBeenCalledWith('session-2');
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/roles/sessions/session-2/actions/attach',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('renders compact blocked notices without duplicating the full error block in dense sidebars', async () => {
    render(
      <AIDialoguePanel
        dialogueRole="pm"
        roleDisplayName="PM"
        workspace="C:/Temp/Product"
        welcomeMessage="PM blocked"
        interactionBlockedReason="PM LLM 未通过就绪检查"
        statusNoticeMode="compact"
      />,
    );

    const warning = await screen.findByTestId('ai-status-warning');
    expect(warning).toHaveTextContent('PM 当前被阻塞');
    expect(warning).toHaveTextContent('PM LLM 未通过就绪检查');
    expect(warning).toHaveClass('bg-amber-500/5');
    expect(warning).not.toHaveTextContent('错误:');
  });
});
