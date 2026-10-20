import { describe, expect, it } from 'vitest';
import { applyEngineeringEvent, hydrateEngineeringRun, newEngineeringProjection } from './engineeringRunProjection';
import type { EngineeringEvent, EngineeringRunSnapshot } from '@/services/api.types';
export function snapshot(last_seq = 0): EngineeringRunSnapshot {
  return { session_id: 'r1', run_id: 'r1', engine_version: 'engineering_v2', workspace: '/work', workflow: 'direct', role: 'director', status: 'running', active_roles: [], roles: { pm: 'not_called', chief_engineer: 'not_called', director: 'not_called', qa: 'not_called' }, text: '', verification: 'not_run', checks: [], required_checks: [], permission_profile_id: 'none', resource_url: '/v2/engineering/runs/r1', last_seq, events: [] };
}
function event(seq: number, kind: string, payload: Record<string, unknown> = {}, role = 'director'): EngineeringEvent {
  return { session_id: 'r1', workspace: '/work', seq, kind, role, payload };
}
function base() { return hydrateEngineeringRun(newEngineeringProjection('/work', 'r1'), snapshot()); }
describe('engineeringRunProjection', () => {
  it('requests one owner snapshot for an oversized state marker without partially folding checks', () => {
    const state = applyEngineeringEvent(base(), event(2, 'run_updated', { snapshot_required: true, payload_omitted: true, checks_count: 2000, reason: 'required_projection_exceeds_transport_budget' }));
    expect(state.snapshotRequiredSeq).toBe(2);
    expect(state.snapshot?.status).toBe('running'); expect(state.snapshot?.checks).toHaveLength(0);
    const checks = Array.from({ length: 20 }, (_, index) => ({ call_id: `check-${index}`, tool: 'execute_command', ok: false, content: '输出'.repeat(2504), exit_code: 1, interrupted: false }));
    const full = hydrateEngineeringRun(state, { ...snapshot(2), status: 'failed', verification: 'failed', checks });
    expect(full.snapshotRequiredSeq).toBeNull(); expect(full.snapshot?.checks).toHaveLength(20); expect(full.snapshot?.status).toBe('failed');
  });
  it('updates actual role and tool facts without passing untouched roles or verification', () => {
    let state = applyEngineeringEvent(base(), event(1, 'role_started'));
    state = applyEngineeringEvent(state, event(2, 'tool_started', { call_id: 'c1', tool: 'read_file', arguments: { file: 'a.py' } }));
    state = applyEngineeringEvent(state, event(3, 'tool_observed', { call_id: 'c1', tool: 'read_file', ok: false, content: 'denied', exit_code: null, interrupted: false }));
    state = applyEngineeringEvent(state, event(4, 'role_finished', { status: 'completed', text: 'All tests passed' }));
    expect(state.snapshot?.roles.director).toBe('completed');
    expect(state.snapshot?.roles.pm).toBe('not_called');
    expect(state.snapshot?.roles.chief_engineer).toBe('not_called');
    expect(state.snapshot?.roles.qa).toBe('not_called');
    expect(state.snapshot?.verification).toBe('not_run');
    expect(state.events.find((row) => row.kind === 'tool_observed')?.payload.content).toBe('denied');
  });
  it('keeps verification independent from completion text and uses authoritative run updates', () => {
    const state = applyEngineeringEvent(base(), event(2, 'run_updated', { ...snapshot(), status: 'completed', text: 'passed', verification: 'failed' }));
    expect(state.snapshot?.status).toBe('completed');
    expect(state.snapshot?.verification).toBe('failed');
  });
  it('rejects duplicate seq and foreign workspace/session without changing state', () => {
    const state = applyEngineeringEvent(base(), event(1, 'role_started'));
    expect(applyEngineeringEvent(state, event(1, 'role_finished', { status: 'completed' }))).toBe(state);
    expect(applyEngineeringEvent(state, { ...event(2, 'role_finished'), workspace: '/other' })).toBe(state);
    expect(applyEngineeringEvent(state, { ...event(2, 'role_finished'), session_id: 'old' })).toBe(state);
    expect(state.lastSeq).toBe(1);
  });
  it('buffers native events while a snapshot is pending and replays only beyond its SQL watermark', () => {
    let state = newEngineeringProjection('/work', 'r1');
    state = applyEngineeringEvent(state, event(9, 'role_finished', { status: 'completed' }));
    state = applyEngineeringEvent(state, event(8, 'role_started'));
    const saved = { ...snapshot(8), roles: { ...snapshot().roles, director: 'running' as const }, active_roles: ['director'] };
    state = hydrateEngineeringRun(state, saved);
    expect(state.snapshot?.roles.director).toBe('completed');
    expect(state.lastSeq).toBe(9);
    expect(state.events.map((row) => row.seq)).toEqual([9]);
  });
  it('retains out-of-order unseen events and folds them in SQL order', () => {
    let state = applyEngineeringEvent(base(), event(3, 'role_finished', { status: 'completed' }));
    state = applyEngineeringEvent(state, event(2, 'role_started'));
    expect(state.events.map((row) => row.seq)).toEqual([2, 3]);
    expect(state.snapshot?.roles.director).toBe('completed');
  });
  it('ignores stale snapshots and keeps global transport cursors out of SQL sequencing', () => {
    const state = hydrateEngineeringRun(base(), snapshot(10));
    expect(hydrateEngineeringRun(state, snapshot(8))).toBe(state);
    expect(applyEngineeringEvent(state, event(11, 'role_started')).lastSeq).toBe(11);
  });
  it('keeps long live histories bounded and exposes malformed authoritative updates', () => {
    let state = base();
    for (let seq = 1; seq <= 350; seq += 1)
      state = applyEngineeringEvent(state, event(seq, 'role_started'));
    expect(state.events).toHaveLength(200);
    expect(state.buffered.length).toBeLessThanOrEqual(200);
    expect(state.snapshot?.roles.director).toBe('running');
    expect(state.lastSeq).toBe(350);
    const invalid = applyEngineeringEvent(state, event(351, 'run_updated', { verification: 'invented_green' }));
    expect(invalid.error).toContain('Unsupported');
  });
  it('retains bounded history as a visible window and fails closed on unsupported engines', () => {
    const saved = { ...snapshot(250), events: [event(249, 'role_started'), event(250, 'role_finished', { status: 'completed' })] };
    expect(hydrateEngineeringRun(base(), saved).historyWindowed).toBe(true);
    const wrong = { ...saved, engine_version: 'old' } as unknown as EngineeringRunSnapshot;
    expect(hydrateEngineeringRun(base(), wrong).error).toContain('engine');
  });
});
