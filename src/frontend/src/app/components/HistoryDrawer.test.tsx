import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HistoryDrawer } from './HistoryDrawer';
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
    throw new Error('HistoryDrawer must not read Factory history endpoints');
  }),
}));

const projection: ControlPlaneProjection = {
  schema_version: 1,
  source: 'run_ledger_projection',
  available: true,
  ok: true,
  status: 'ready',
  audit_path: 'runtime/control_plane/ledger/run-1.ndjson',
  compat_ledgers_included: false,
  total: 1,
  projected: 1,
  missing: 0,
  failed: 0,
  detail: 'run ledger projection 1 project(s), 0 failed',
  projects: [
    {
      project_id: 'project-alpha',
      ok: true,
      integrity_ok: true,
      outcome_ok: true,
      gate_count: 2,
      failed_gate_count: 0,
      latest_token_id: 'job-token-1',
      detail: 'physical evidence verified',
      missing: [],
      evidence_modalities: {
        command: {
          total: 2,
          present: 2,
          ok: 2,
          failed: 0,
          latest_detail: 'npm run test',
        },
      },
    },
  ],
};

describe('HistoryDrawer', () => {
  beforeEach(() => {
    vi.mocked(getControlPlaneProjection).mockReset();
  });

  it('loads formal history from Control Plane Run Ledger projection by default', async () => {
    vi.mocked(getControlPlaneProjection).mockResolvedValue({ ok: true, data: projection });

    render(
      <HistoryDrawer
        open={true}
        onOpenChange={() => undefined}
        workspace="/tmp/workspace"
        defaultLimit={25}
      />
    );

    await screen.findByText('project-alpha');
    expect(screen.getAllByText('Run Ledger 案卷').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('project-alpha')).toBeInTheDocument();
    expect(screen.getByText('job-token-1')).toBeInTheDocument();
    expect(screen.getByText('Run Ledger verified 1/1')).toBeInTheDocument();
    expect(screen.getByText('command: 2/2')).toBeInTheDocument();

    await waitFor(() => {
      expect(getControlPlaneProjection).toHaveBeenCalledWith({
        workspace: '/tmp/workspace',
        maxRuns: 25,
      });
    });

    expect(screen.queryByText('缺陷回流')).not.toBeInTheDocument();
    expect(screen.queryByText(/Factory 批次/)).not.toBeInTheDocument();
  });

  it('fails closed instead of falling back to Factory history when the ledger cannot load', async () => {
    vi.mocked(getControlPlaneProjection).mockResolvedValue({
      ok: false,
      error: 'Control Plane ledger unavailable',
    });

    render(<HistoryDrawer open={true} onOpenChange={() => undefined} workspace="/tmp/workspace" />);

    expect(await screen.findByText(/账本读取失败/)).toHaveTextContent(
      'Control Plane ledger unavailable'
    );
    expect(screen.queryByText('PolicyGate 拦截')).not.toBeInTheDocument();
  });

  it('marks compat ledgers as internal inputs when explicitly included by projection', async () => {
    vi.mocked(getControlPlaneProjection).mockResolvedValue({
      ok: true,
      data: {
        ...projection,
        compat_ledgers_included: true,
      },
    });

    render(<HistoryDrawer open={true} onOpenChange={() => undefined} workspace="/tmp/workspace" />);

    expect(await screen.findByText(/compat ledger included/)).toBeInTheDocument();
    expect(screen.getByText(/内部测试账本只作为平台投影输入/)).toBeInTheDocument();
  });
});
