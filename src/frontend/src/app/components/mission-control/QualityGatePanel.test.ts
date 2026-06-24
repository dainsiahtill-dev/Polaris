import { describe, expect, it } from 'vitest';
import { evaluateDirectorGate, evaluatePMGate, evaluateQAGate } from './QualityGatePanel';
import type { ControlPlaneProjection } from '@/services/controlPlane';
import type { RoleState } from '@/runtime/v2';

const completedSummary = { completed: 3, failed: 0, total: 3 };
const completedPmSummary = { completed: 3, failed: 0, blocked: 0, total: 3 };

function ledgerProjection(overrides: Partial<ControlPlaneProjection> = {}): ControlPlaneProjection {
  return {
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
    projects: [
      {
        project_id: 'project-1',
        ok: true,
        integrity_ok: true,
        outcome_ok: true,
        gate_count: 1,
        failed_gate_count: 0,
        latest_token_id: 'job-token-1',
        detail: 'qa verified',
        missing: [],
      },
    ],
    detail: 'run ledger projection 1 project(s), 0 failed',
    ...overrides,
  };
}

describe('QualityGatePanel QA gate ledger authority', () => {
  it('does not mark PM green from completion rate without Run Ledger evidence', () => {
    const gate = evaluatePMGate(completedPmSummary, 'completed' as RoleState);

    expect(gate.status).toBe('pending');
    expect(gate.detail).toContain('Run Ledger');
  });

  it('marks PM green only when Run Ledger projection verifies success', () => {
    const gate = evaluatePMGate(
      completedPmSummary,
      'completed' as RoleState,
      ledgerProjection()
    );

    expect(gate.status).toBe('green');
    expect(gate.detail).toBe('Run Ledger verified 1/1');
  });

  it('does not mark Director green from role completion without Run Ledger evidence', () => {
    const gate = evaluateDirectorGate('completed' as RoleState, []);

    expect(gate.status).toBe('pending');
    expect(gate.detail).toContain('Run Ledger');
  });

  it('keeps Director execution as warning until ledger evidence arrives', () => {
    const gate = evaluateDirectorGate('executing' as RoleState, [], ledgerProjection());

    expect(gate.status).toBe('yellow');
    expect(gate.detail).toContain('执行中');
  });

  it('marks Director green only when completed and Run Ledger projection verifies success', () => {
    const gate = evaluateDirectorGate(
      'completed' as RoleState,
      [],
      ledgerProjection()
    );

    expect(gate.status).toBe('green');
    expect(gate.detail).toBe('Run Ledger verified 1/1');
  });

  it('does not mark QA green from role completion without Run Ledger evidence', () => {
    const gate = evaluateQAGate('completed' as RoleState, completedSummary);

    expect(gate.status).toBe('pending');
    expect(gate.detail).toContain('Run Ledger');
  });

  it('marks QA green only when Run Ledger projection verifies success', () => {
    const gate = evaluateQAGate('completed' as RoleState, completedSummary, ledgerProjection());

    expect(gate.status).toBe('green');
    expect(gate.detail).toBe('Run Ledger verified 1/1');
  });

  it('keeps QA pending while Run Ledger projection has no projected projects', () => {
    const gate = evaluateQAGate(
      'completed' as RoleState,
      completedSummary,
      ledgerProjection({
        ok: false,
        status: 'pending',
        total: 0,
        projected: 0,
        projects: [],
        detail: 'run ledger projection is pending',
      }),
    );

    expect(gate.status).toBe('pending');
    expect(gate.detail).toBe('run ledger projection is pending');
  });

  it('marks QA red when Run Ledger projection has failed gates', () => {
    const gate = evaluateQAGate(
      'completed' as RoleState,
      completedSummary,
      ledgerProjection({
        ok: false,
        status: 'failed',
        failed: 1,
        projects: [
          {
            project_id: 'project-1',
            ok: false,
            integrity_ok: true,
            outcome_ok: false,
            gate_count: 2,
            failed_gate_count: 1,
            latest_token_id: 'job-token-failed',
            detail: 'build gate failed',
            missing: [],
          },
        ],
        detail: 'run ledger projection 1 project(s), 1 failed',
      }),
    );

    expect(gate.status).toBe('red');
    expect(gate.detail).toBe('build gate failed');
  });
});
