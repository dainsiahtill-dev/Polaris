import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionInspector } from './SessionInspector';

const roleSessionMocks = vi.hoisted(() => ({
  detachRoleSession: vi.fn(),
  exportRoleSessionSnapshot: vi.fn(),
  getRoleCapabilities: vi.fn(),
  resolveRoleCapabilities: vi.fn((payload: { capabilities?: Record<string, string[]> }, hostKind: string) => (
    payload.capabilities?.[hostKind] || payload.capabilities?.default || []
  )),
}));

vi.mock('@/services/roleSessionService', () => roleSessionMocks);

describe('SessionInspector RoleSession service bridge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    roleSessionMocks.getRoleCapabilities.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        role: 'director',
        capabilities: {
          electron_workbench: ['read_files', 'write_files', 'execute_commands', 'view_metrics', 'inspect_audit'],
        },
      },
    });
    roleSessionMocks.detachRoleSession.mockResolvedValue({
      ok: true,
      data: { id: 'session-1', attachment_mode: 'isolated' },
    });
    roleSessionMocks.exportRoleSessionSnapshot.mockResolvedValue({
      ok: true,
      data: { session_id: 'session-1' },
    });
  });

  it('loads capability chips through the typed service', async () => {
    render(
      <SessionInspector
        sessionId="session-1"
        role="director"
        hostKind="electron_workbench"
      />,
    );

    await waitFor(() => expect(roleSessionMocks.getRoleCapabilities).toHaveBeenCalledWith('director', 'electron_workbench'));
    expect(roleSessionMocks.resolveRoleCapabilities).toHaveBeenCalledWith(
      expect.objectContaining({ role: 'director' }),
      'electron_workbench',
    );
    expect(await screen.findByText('read files')).toBeInTheDocument();
    expect(screen.getByText('+1')).toBeInTheDocument();
  });

  it('detaches and exports through the typed service', async () => {
    const onDetach = vi.fn();
    const onExport = vi.fn();

    render(
      <SessionInspector
        sessionId="session-1"
        role="pm"
        attachmentMode="attached_readonly"
        onDetach={onDetach}
        onExport={onExport}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '解除附着' }));

    await waitFor(() => expect(roleSessionMocks.detachRoleSession).toHaveBeenCalledWith('session-1'));
    expect(onDetach).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '导出会话' }));

    await waitFor(() => expect(roleSessionMocks.exportRoleSessionSnapshot).toHaveBeenCalledWith('session-1', {
      include_messages: true,
      format: 'json',
    }));
    expect(onExport).toHaveBeenCalledTimes(1);
  });
});
