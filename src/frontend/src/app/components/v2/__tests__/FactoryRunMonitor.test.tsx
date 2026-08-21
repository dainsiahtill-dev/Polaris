import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FactoryRunMonitor } from '../FactoryRunMonitor';

const factoryRunsMock = vi.hoisted(() => ({
  fetchAuditBundle: vi.fn(),
  state: {} as Record<string, unknown>,
}));

vi.mock('@/app/hooks/useV2Api', () => ({
  useFactoryRuns: () => factoryRunsMock.state,
}));

vi.mock('@/app/hooks/useV2ApiError', () => ({
  useV2ApiError: () => ({ apiError: { hasError: false, error: null } }),
}));

describe('FactoryRunMonitor exact-run diagnosis', () => {
  beforeEach(() => {
    factoryRunsMock.fetchAuditBundle.mockReset();
    factoryRunsMock.state = {
    events: null,
    auditBundle: {
      run_id: 'factory_exact',
      status: 'failed',
      exact_run_causal_audit: {
        diagnosis_id: '0123456789abcdef01234567',
        current_status: 'CONTROL_PLANE_FAIL',
        root_cause_code: 'director.tasking.delivery_contract_scope_contradiction',
        responsible_cell: 'director.tasking',
        retry_boundary: 'same_contract_projection_only',
        evidence_completeness: { complete: true, missing_links: [] },
        next_action: {
          action: 'same_contract_projection_only',
          suspected_files: ['src/render/gardenCanvas.ts'],
        },
        repair_diagnosis: {
          status: 'coverage_matched_but_unplannable',
          residual_errors: ['TS2322: Timeout is not assignable to number'],
          covered_unplannable_source_tools: ['deterministic_typescript_strict_null_relaxation_repair'],
        },
        platform_residual_attribution: { primary_module_id: 'M07_factory_stage_chain' },
        historical_error_count: 18,
      },
    },
    loading: false,
    error: '',
    fetchEvents: vi.fn(),
    fetchAuditBundle: factoryRunsMock.fetchAuditBundle,
    };
  });

  it('separates the current root cause from historical error count', () => {
    render(<FactoryRunMonitor runId="factory_exact" />);

    expect(screen.getByTestId('factory-exact-run-audit')).toHaveTextContent('CONTROL_PLANE_FAIL');
    expect(screen.getByTestId('factory-exact-run-audit')).toHaveTextContent(
      'director.tasking.delivery_contract_scope_contradiction',
    );
    expect(screen.getByTestId('factory-exact-run-audit')).toHaveTextContent('same_contract_projection_only');
    expect(screen.getByTestId('factory-exact-run-audit')).toHaveTextContent('0123456789abcdef01234567');
    expect(screen.getByTestId('factory-exact-run-audit')).toHaveTextContent('M07_factory_stage_chain');
    expect(screen.getByTestId('factory-exact-run-audit')).toHaveTextContent(
      'coverage_matched_but_unplannable',
    );
    expect(screen.getByTestId('factory-exact-run-audit')).toHaveTextContent('src/render/gardenCanvas.ts');
    expect(screen.getByTestId('factory-exact-run-audit')).toHaveTextContent(
      'coverage matched, but no changed patch was planned',
    );
    expect(screen.getByTestId('factory-exact-run-audit')).toHaveTextContent('Evidencecomplete');
    expect(screen.getByText('18 (non-authoritative)')).toBeInTheDocument();
  });

  it('automatically requests causal audit when a pushed run event becomes failed', async () => {
    factoryRunsMock.state = {
      ...factoryRunsMock.state,
      events: {
        total: 1,
        events: [{ type: 'factory.failed', stage: 'quality_gate', event_id: 'event-1' }],
      },
      auditBundle: null,
    };

    render(<FactoryRunMonitor runId="factory_failed" />);

    await waitFor(() => {
      expect(factoryRunsMock.fetchAuditBundle).toHaveBeenCalledTimes(1);
      expect(factoryRunsMock.fetchAuditBundle).toHaveBeenCalledWith('factory_failed');
    });
  });
});
