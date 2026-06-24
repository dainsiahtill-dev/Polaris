import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceHistoryPanel } from './WorkspaceHistoryPanel';
import { getControlPlaneProjection, type ControlPlaneProjection } from '@/services/controlPlane';

vi.mock('@/services/controlPlane', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/controlPlane')>();
  return {
    ...actual,
    getControlPlaneProjection: vi.fn(),
  };
});

vi.mock('@/api', () => ({
  apiFetch: vi.fn(() => {
    throw new Error('WorkspaceHistoryPanel must not read Factory history endpoints');
  }),
}));

const projection: ControlPlaneProjection = {
  schema_version: 1,
  source: 'run_ledger_projection',
  available: true,
  ok: false,
  status: 'failed',
  audit_path: 'runtime/control_plane/ledger/run-2.ndjson',
  compat_ledgers_included: false,
  total: 1,
  projected: 1,
  missing: 1,
  failed: 1,
  detail: 'run ledger projection 1 project(s), 1 failed',
  projects: [
    {
      project_id: 'project-with-missing-receipt',
      ok: false,
      integrity_ok: false,
      outcome_ok: false,
      gate_count: 1,
      failed_gate_count: 1,
      latest_token_id: 'job-token-failed',
      detail: 'write receipt missing',
      missing: ['write_receipt', 'file_hash_delta'],
    },
  ],
};

describe('WorkspaceHistoryPanel', () => {
  beforeEach(() => {
    vi.mocked(getControlPlaneProjection).mockReset();
  });

  it('uses the platform Run Ledger projection instead of Factory history routes', async () => {
    vi.mocked(getControlPlaneProjection).mockResolvedValue({ ok: true, data: projection });

    render(
      <WorkspaceHistoryPanel
        workspace="/tmp/workspace"
        defaultLimit={10}
      />
    );

    expect(await screen.findByText('project-with-missing-receipt')).toBeInTheDocument();
    expect(screen.getByText('write receipt missing')).toBeInTheDocument();
    expect(screen.getByText('write_receipt, file_hash_delta')).toBeInTheDocument();

    await waitFor(() => {
      expect(getControlPlaneProjection).toHaveBeenCalledWith({
        workspace: '/tmp/workspace',
        maxRuns: 10,
      });
    });

    expect(screen.queryByText('工厂历史')).not.toBeInTheDocument();
    expect(screen.queryByText('缺陷回流')).not.toBeInTheDocument();
    expect(screen.queryByText('PolicyGate 拦截')).not.toBeInTheDocument();
  });
});
