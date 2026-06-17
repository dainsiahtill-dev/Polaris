import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChiefEngineerGovernancePanel } from './ChiefEngineerGovernancePanel';

const serviceMocks = vi.hoisted(() => ({
  listChiefEngineerRisks: vi.fn(),
  listChiefEngineerTechDebt: vi.fn(),
  listChiefEngineerADRs: vi.fn(),
}));

vi.mock('@/services/chiefEngineerService', () => serviceMocks);

describe('ChiefEngineerGovernancePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders risks and tech debt from the governance services', async () => {
    serviceMocks.listChiefEngineerRisks.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        total: 1,
        risks: [
          {
            risk_id: 'risk_1',
            task_id: 'task-1',
            title: 'schema migration may drop rows',
            severity: 'blocker',
            owner: 'ce',
            mitigation: 'dual-write',
            status: 'open',
            detected_at: '2026-06-17T00:00:00Z',
            links: [],
            supersedes: null,
            history: [],
          },
        ],
        summary: {},
      },
    });
    serviceMocks.listChiefEngineerTechDebt.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        total: 1,
        tech_debt: [
          {
            debt_id: 'debt_1',
            title: 'manual sql escaping',
            description: 'bypasses ORM',
            severity: 'severe',
            surface: 'src/db.py',
            owner: 'ce',
            evidence: [],
            status: 'registered',
            registered_at: '2026-06-17T00:00:00Z',
            history: [],
          },
        ],
        summary: {},
      },
    });
    serviceMocks.listChiefEngineerADRs.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        total: 1,
        adrs: [
          {
            adr_id: 'adr_1',
            title: 'adopt single transaction kernel',
            status: 'accepted',
            context: 'partial writes',
            decision: 'one commit point',
            consequences: 'simpler rollback',
            owner: 'ce',
            decided_at: '2026-06-17T00:00:00Z',
            alternatives: [],
            related_task_ids: [],
            supersedes: null,
            history: [],
          },
        ],
        summary: {},
      },
    });

    render(<ChiefEngineerGovernancePanel workspace="/repo" />);

    await waitFor(() => {
      expect(screen.getByText('schema migration may drop rows')).toBeInTheDocument();
    });
    expect(screen.getByText('manual sql escaping')).toBeInTheDocument();
    expect(screen.getByText('adopt single transaction kernel')).toBeInTheDocument();
    expect(serviceMocks.listChiefEngineerRisks).toHaveBeenCalledWith({}, '/repo');
    expect(serviceMocks.listChiefEngineerTechDebt).toHaveBeenCalledWith({}, '/repo');
    expect(serviceMocks.listChiefEngineerADRs).toHaveBeenCalledWith({}, '/repo');
  });

  it('shows empty states when there is no governance data', async () => {
    serviceMocks.listChiefEngineerRisks.mockResolvedValue({
      ok: true,
      data: { ok: true, total: 0, risks: [], summary: {} },
    });
    serviceMocks.listChiefEngineerTechDebt.mockResolvedValue({
      ok: true,
      data: { ok: true, total: 0, tech_debt: [], summary: {} },
    });
    serviceMocks.listChiefEngineerADRs.mockResolvedValue({
      ok: true,
      data: { ok: true, total: 0, adrs: [], summary: {} },
    });

    render(<ChiefEngineerGovernancePanel workspace="/repo" />);

    await waitFor(() => {
      expect(screen.getByTestId('ce-risks-empty')).toBeInTheDocument();
    });
    expect(screen.getByTestId('ce-tech-debt-empty')).toBeInTheDocument();
    expect(screen.getByTestId('ce-adrs-empty')).toBeInTheDocument();
  });

  it('surfaces a service error', async () => {
    serviceMocks.listChiefEngineerRisks.mockResolvedValue({
      ok: false,
      error: 'backend unreachable',
    });
    serviceMocks.listChiefEngineerTechDebt.mockResolvedValue({
      ok: true,
      data: { ok: true, total: 0, tech_debt: [], summary: {} },
    });
    serviceMocks.listChiefEngineerADRs.mockResolvedValue({
      ok: true,
      data: { ok: true, total: 0, adrs: [], summary: {} },
    });

    render(<ChiefEngineerGovernancePanel workspace="/repo" />);

    await waitFor(() => {
      expect(screen.getByTestId('ce-governance-error')).toHaveTextContent('backend unreachable');
    });
  });
});
