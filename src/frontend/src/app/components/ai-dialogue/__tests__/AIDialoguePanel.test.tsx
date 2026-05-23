import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AIDialoguePanel } from '../AIDialoguePanel';

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

function findApiCall(path: string): [string, RequestInit | undefined] {
  const call = apiFetchMock.mock.calls.find(([candidate]) => candidate === path);
  expect(call).toBeTruthy();
  return call as [string, RequestInit | undefined];
}

describe('AIDialoguePanel RoleSession visibility', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let sessionIndex = 0;
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/v2/role/pm/chat/status') {
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
      if (path === '/v2/roles/sessions/session-1/audit?limit=20&offset=0') {
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
    await waitFor(() => expect(screen.getByTestId('ai-role-session-id')).toHaveTextContent('session-1'));
    expect(screen.getByTestId('ai-role-session-attachment')).toHaveTextContent('Task PM-1');
    await waitFor(() => expect(screen.getByTestId('ai-role-capability-chip')).toHaveTextContent('cap 3'));
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/capabilities/pm?host_kind=electron_workbench');
    await waitFor(() => expect(screen.getByTestId('ai-role-session-detail-chip')).toHaveTextContent('active · 4 msg'));
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

    await waitFor(() => expect(screen.getByTestId('ai-role-session-evidence-panel')).toBeInTheDocument());
    expect(screen.getByTestId('ai-role-session-evidence-counts')).toHaveTextContent('1 artifacts / 1 audit');
    expect(screen.getByTestId('ai-role-session-artifact-row')).toHaveTextContent('directive');
    expect(screen.getByTestId('ai-role-session-artifact-row')).toHaveTextContent('Use persisted PM directive');
    expect(screen.getByTestId('ai-role-session-audit-row')).toHaveTextContent('message_sent');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-1/artifacts');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-1/audit?limit=20&offset=0');

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

    await waitFor(() => expect(screen.getByTestId('ai-role-session-id')).toHaveTextContent('session-old'));
    await waitFor(() => expect(screen.getByTestId('ai-role-session-detail-chip')).toHaveTextContent('active · 2 msg'));
    expect(screen.getByText('old answer')).toBeInTheDocument();
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-old/messages?limit=100&offset=0');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/roles/sessions/session-old');
    expect(onSessionChange).toHaveBeenCalledWith('session-old');

    fireEvent.click(screen.getByTestId('ai-role-session-new'));

    await waitFor(() => expect(screen.getByTestId('ai-role-session-id')).toHaveTextContent('session-2'));
    expect(onSessionChange).toHaveBeenCalledWith(null);
    expect(onSessionChange).toHaveBeenCalledWith('session-2');
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/roles/sessions/session-2/actions/attach',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
