import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RoleSessionEvidencePanel } from './RoleSessionEvidencePanel';

const roleSessionMocks = vi.hoisted(() => ({
  listRoleSessionArtifactEvidence: vi.fn(),
  listRoleSessionAuditEvidence: vi.fn(),
  listRoleSessionMessageEvidence: vi.fn(),
}));

vi.mock('@/services/roleSessionService', () => roleSessionMocks);

describe('RoleSessionEvidencePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    roleSessionMocks.listRoleSessionMessageEvidence.mockResolvedValue({
      ok: true,
      data: {
        items: [{ id: 'message-1', role: 'assistant', content: 'Evidence message' }],
        total: 9,
      },
    });
    roleSessionMocks.listRoleSessionArtifactEvidence.mockResolvedValue({
      ok: true,
      data: {
        items: [{ id: 'artifact-1', type: 'directive' }],
        total: 1,
      },
    });
    roleSessionMocks.listRoleSessionAuditEvidence.mockResolvedValue({
      ok: true,
      data: {
        items: [{ id: 'audit-1', event_type: 'session_exported', timestamp: '2026-05-23T00:00:00Z' }],
        total: 12,
      },
    });
  });

  it('shows an empty evidence state before a session is selected', () => {
    render(<RoleSessionEvidencePanel sessionId={null} tone="amber" />);

    expect(screen.getByTestId('role-session-evidence-empty')).toHaveTextContent('等待会话');
    expect(screen.getByTestId('role-session-evidence-panel')).not.toHaveTextContent('/v2/roles/sessions/{session_id}');
    expect(screen.getByTestId('role-session-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/roles/sessions/{session_id}',
    );
    expect(roleSessionMocks.listRoleSessionMessageEvidence).not.toHaveBeenCalled();
  });

  it('loads messages, artifacts, and audit evidence for the active RoleSession', async () => {
    render(<RoleSessionEvidencePanel sessionId="role-session-1" tone="emerald" />);

    await waitFor(() => expect(roleSessionMocks.listRoleSessionMessageEvidence).toHaveBeenCalledWith('role-session-1', {
      limit: 5,
      offset: 0,
    }));
    expect(roleSessionMocks.listRoleSessionArtifactEvidence).toHaveBeenCalledWith('role-session-1');
    expect(roleSessionMocks.listRoleSessionAuditEvidence).toHaveBeenCalledWith('role-session-1', {
      limit: 5,
      offset: 0,
    });
    expect(screen.getByTestId('role-session-evidence-panel')).not.toHaveTextContent('/v2/roles/sessions/role-session-1');
    expect(screen.getByTestId('role-session-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/roles/sessions/role-session-1',
    );
    expect(screen.getByTestId('role-session-evidence-messages')).toHaveAttribute(
      'data-endpoint',
      '/v2/roles/sessions/role-session-1/messages',
    );
    expect(screen.getByTestId('role-session-evidence-messages')).toHaveTextContent('assistant: Evidence message');
    expect(screen.getByTestId('role-session-evidence-messages')).toHaveTextContent('9');
    expect(screen.getByTestId('role-session-evidence-messages')).toHaveTextContent('预览 1');
    expect(screen.getByTestId('role-session-evidence-artifacts')).toHaveTextContent('directive: artifact-1');
    expect(screen.getByTestId('role-session-evidence-audit')).toHaveTextContent('session_exported');

    fireEvent.click(screen.getByTestId('role-session-evidence-refresh'));
    await waitFor(() => expect(roleSessionMocks.listRoleSessionMessageEvidence).toHaveBeenCalledTimes(2));
  });

  it('surfaces partial evidence loading errors without hiding other evidence', async () => {
    roleSessionMocks.listRoleSessionMessageEvidence.mockResolvedValueOnce({
      ok: false,
      error: 'messages offline',
    });

    render(<RoleSessionEvidencePanel sessionId="role-session-2" tone="cyan" />);

    const messageError = await screen.findByTestId('role-session-evidence-messages-error');
    expect(messageError).toHaveTextContent('messages offline');
    expect(screen.getByTestId('role-session-evidence-panel')).not.toHaveTextContent('/v2/roles/sessions/role-session-2');
    expect(screen.getByTestId('role-session-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/roles/sessions/role-session-2',
    );
    expect(screen.getByTestId('role-session-evidence-artifacts')).toHaveTextContent('directive: artifact-1');
    expect(screen.getByTestId('role-session-evidence-audit')).toHaveTextContent('session_exported');
  });
});
