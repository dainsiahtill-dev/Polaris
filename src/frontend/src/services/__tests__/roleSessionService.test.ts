import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockApiGet = vi.fn();
const mockApiPost = vi.fn();

vi.mock('@/services/apiClient', () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
}));

import {
  attachRoleSession,
  createRoleSession,
  detachRoleSession,
  exportRoleSessionSnapshot,
  exportRoleSessionToWorkflow,
  getRoleCapabilities,
  getRoleSession,
  listRoleSessionArtifactEvidence,
  listRoleSessionArtifacts,
  listRoleSessionAuditEvidence,
  listRoleSessionAuditEvents,
  listRoleSessionMessageEvidence,
  listRoleSessionMessages,
  listRoleSessions,
  readRoleSessionMemoryArtifact,
  readRoleSessionMemoryEpisode,
  readRoleSessionMemoryState,
  resolveRoleCapabilities,
  searchRoleSessionMemory,
} from '../roleSessionService';

describe('roleSessionService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('resolves host-scoped role capabilities from backend maps', () => {
    expect(resolveRoleCapabilities({
      ok: true,
      role: 'pm',
      capabilities: {
        electron_workbench: ['read_files', 'export_workflow'],
        workflow: ['read_files'],
      },
    }, 'electron_workbench')).toEqual(['read_files', 'export_workflow']);
  });

  it('loads role capabilities through the canonical role endpoint', async () => {
    mockApiGet.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, role: 'chief_engineer', capabilities: ['read_files'] },
    });

    const result = await getRoleCapabilities('chief_engineer', 'electron_workbench');

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/roles/capabilities/chief_engineer?host_kind=electron_workbench',
      'Failed to load role capabilities',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.role).toBe('chief_engineer');
  });

  it('creates and unwraps a RoleSession response', async () => {
    mockApiPost.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        session: { id: 'session-1', role: 'director', host_kind: 'electron_workbench' },
      },
    });

    const payload = {
      role: 'director',
      host_kind: 'electron_workbench',
      workspace: 'C:/Temp/Product',
      attachment_mode: 'attached_readonly',
      context_config: { selected_task_id: 'PM-1' },
    };
    const result = await createRoleSession(payload);

    expect(mockApiPost).toHaveBeenCalledWith('/v2/roles/sessions', payload, 'Failed to create RoleSession');
    expect(result.ok).toBe(true);
    expect(result.data?.id).toBe('session-1');
  });

  it('gets one RoleSession detail by id', async () => {
    mockApiGet.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        session: { id: 'session-1', state: 'active', message_count: 4 },
      },
    });

    const result = await getRoleSession('session-1');

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/roles/sessions/session-1',
      'Failed to load RoleSession',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.message_count).toBe(4);
  });

  it('lists role sessions with role, host, workspace, and limit filters', async () => {
    mockApiGet.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        sessions: [
          { id: 'session-1', title: 'Current' },
          { title: 'invalid row without id' },
        ],
        total: 2,
      },
    });

    const result = await listRoleSessions({
      role: 'pm',
      hostKind: 'electron_workbench',
      workspace: 'C:/Temp/Product',
      limit: 20,
    });

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/roles/sessions?role=pm&host_kind=electron_workbench&limit=20&workspace=C%3A%2FTemp%2FProduct',
      'Failed to list RoleSessions',
    );
    expect(result.ok).toBe(true);
    expect(result.data).toEqual([{ id: 'session-1', title: 'Current' }]);
  });

  it('lists RoleSession messages with pagination', async () => {
    mockApiGet.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        messages: [{ id: 'msg-1', role: 'assistant', content: 'answer' }],
        session: { id: 'session-1', message_count: 8 },
        total: 8,
      },
    });

    const evidence = await listRoleSessionMessageEvidence('session-1', { limit: 100, offset: 0 });

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/roles/sessions/session-1/messages?limit=100&offset=0',
      'Failed to list RoleSession messages',
    );
    expect(evidence.ok).toBe(true);
    expect(evidence.data?.total).toBe(8);
    expect(evidence.data?.items[0].content).toBe('answer');

    mockApiGet.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        messages: [{ id: 'msg-1', role: 'assistant', content: 'answer' }],
        total: 8,
      },
    });
    const result = await listRoleSessionMessages('session-1', { limit: 100, offset: 0 });
    expect(result.ok).toBe(true);
    expect(result.data?.[0].content).toBe('answer');
  });

  it('attaches a RoleSession to workflow context', async () => {
    const payload = {
      run_id: null,
      task_id: 'PM-1',
      mode: 'attached_readonly',
      note: 'Director desktop dialogue attachment',
    };
    mockApiPost.mockResolvedValueOnce({
      ok: true,
      data: { ok: true, attachment: { id: 'attach-1' } },
    });

    const result = await attachRoleSession('session-1', payload);

    expect(mockApiPost).toHaveBeenCalledWith(
      '/v2/roles/sessions/session-1/actions/attach',
      payload,
      'Failed to attach RoleSession',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.attachment?.id).toBe('attach-1');
  });

  it('detaches and unwraps the updated RoleSession detail', async () => {
    mockApiPost.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        session: { id: 'session-1', attachment_mode: 'isolated' },
      },
    });

    const result = await detachRoleSession('session-1');

    expect(mockApiPost).toHaveBeenCalledWith(
      '/v2/roles/sessions/session-1/actions/detach',
      {},
      'Failed to detach RoleSession',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.attachment_mode).toBe('isolated');
  });

  it('loads artifacts and audit evidence through RoleSession endpoints', async () => {
    mockApiGet
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          artifacts: [{ id: 'artifact-1', type: 'directive' }, { type: 'invalid' }],
          total: 7,
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          audit_events: [{ id: 'audit-1', event_type: 'message_sent' }],
          total: 11,
        },
      });

    const artifacts = await listRoleSessionArtifactEvidence('session-1');
    const audit = await listRoleSessionAuditEvidence('session-1', { limit: 20, offset: 0 });

    expect(mockApiGet).toHaveBeenNthCalledWith(
      1,
      '/v2/roles/sessions/session-1/artifacts',
      'Failed to list RoleSession artifacts',
    );
    expect(mockApiGet).toHaveBeenNthCalledWith(
      2,
      '/v2/roles/sessions/session-1/audit?limit=20&offset=0',
      'Failed to list RoleSession audit events',
    );
    expect(artifacts.ok).toBe(true);
    expect(artifacts.data?.items).toEqual([{ id: 'artifact-1', type: 'directive' }]);
    expect(artifacts.data?.total).toBe(7);
    expect(audit.ok).toBe(true);
    expect(audit.data?.items[0].event_type).toBe('message_sent');
    expect(audit.data?.total).toBe(11);

    mockApiGet
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          artifacts: [{ id: 'artifact-1', type: 'directive' }],
          total: 1,
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          audit_events: [{ id: 'audit-1', event_type: 'message_sent' }],
          total: 1,
        },
      });
    const artifactItems = await listRoleSessionArtifacts('session-1');
    const auditItems = await listRoleSessionAuditEvents('session-1', { limit: 20, offset: 0 });
    expect(artifactItems.data).toEqual([{ id: 'artifact-1', type: 'directive' }]);
    expect(auditItems.data?.[0].event_type).toBe('message_sent');
  });

  it('searches and reads RoleSession memory payloads', async () => {
    mockApiGet
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          items: [{ id: 'memory-artifact-1', kind: 'artifact', text: 'persisted memory' }],
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          artifact: { artifact_id: 'memory-artifact-1', content: 'artifact detail' },
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          episode: { episode_id: 'episode-1', content: 'episode detail' },
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ok: true,
          value: { state: 'ready' },
        },
      });

    const items = await searchRoleSessionMemory('session-1', 'PM-1', { limit: 8 });
    const artifact = await readRoleSessionMemoryArtifact('session-1', 'memory-artifact-1');
    const episode = await readRoleSessionMemoryEpisode('session-1', 'episode-1');
    const state = await readRoleSessionMemoryState('session-1', 'workflow/current');

    expect(mockApiGet).toHaveBeenNthCalledWith(
      1,
      '/v2/roles/sessions/session-1/memory/search?q=PM-1&limit=8',
      'Failed to search RoleSession memory',
    );
    expect(mockApiGet).toHaveBeenNthCalledWith(
      2,
      '/v2/roles/sessions/session-1/memory/artifacts/memory-artifact-1',
      'Failed to read RoleSession memory artifact',
    );
    expect(mockApiGet).toHaveBeenNthCalledWith(
      3,
      '/v2/roles/sessions/session-1/memory/episodes/episode-1',
      'Failed to read RoleSession memory episode',
    );
    expect(mockApiGet).toHaveBeenNthCalledWith(
      4,
      '/v2/roles/sessions/session-1/memory/state?path=workflow%2Fcurrent',
      'Failed to read RoleSession memory state',
    );
    expect(items.data?.[0].text).toBe('persisted memory');
    expect(artifact.data).toEqual({ artifact_id: 'memory-artifact-1', content: 'artifact detail' });
    expect(episode.data).toEqual({ episode_id: 'episode-1', content: 'episode detail' });
    expect(state.data).toEqual({ state: 'ready' });
  });

  it('exports snapshots and workflow bundles', async () => {
    mockApiPost
      .mockResolvedValueOnce({
        ok: true,
        data: { ok: true, export: { id: 'session-1', messages: [] } },
      })
      .mockResolvedValueOnce({
        ok: true,
        data: { ok: true, run_id: 'pm-run-1', artifact_count: 2, message_count: 3 },
      });

    const snapshotPayload = { include_messages: true, format: 'json' as const };
    const workflowPayload = {
      target: 'pm' as const,
      export_kind: 'session_bundle' as const,
      include_audit_log: true,
    };
    const snapshot = await exportRoleSessionSnapshot('session-1', snapshotPayload);
    const workflow = await exportRoleSessionToWorkflow('session-1', workflowPayload);

    expect(mockApiPost).toHaveBeenNthCalledWith(
      1,
      '/v2/roles/sessions/session-1/actions/export',
      snapshotPayload,
      'Failed to export RoleSession snapshot',
    );
    expect(mockApiPost).toHaveBeenNthCalledWith(
      2,
      '/v2/roles/sessions/session-1/actions/export-to-workflow',
      workflowPayload,
      'Failed to export RoleSession to workflow',
    );
    expect(snapshot.data).toEqual({ id: 'session-1', messages: [] });
    expect(workflow.data?.run_id).toBe('pm-run-1');
    expect(workflow.data?.message_count).toBe(3);
  });
});
