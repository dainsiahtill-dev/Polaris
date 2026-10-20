import { beforeEach, describe, expect, it, vi } from 'vitest';
const post = vi.fn();
const get = vi.fn();
vi.mock('./apiClient', () => ({ apiPost: (...args: unknown[]) => post(...args), apiGet: (...args: unknown[]) => get(...args) }));
import { cancelEngineeringRun, getEngineeringRun, resumeEngineeringRun, startEngineeringRun } from './engineeringRunService';
const saved = { session_id: 'r1', run_id: 'r1', engine_version: 'engineering_v2', workspace: '/work', workflow: 'direct', role: 'director', status: 'running', active_roles: [], roles: { pm: 'not_called', chief_engineer: 'not_called', director: 'running', qa: 'not_called' }, text: '', verification: 'not_run', checks: [], required_checks: [], permission_profile_id: null, resource_url: '/v2/engineering/runs/r1', last_seq: 1, events: [] };
beforeEach(() => { vi.clearAllMocks(); post.mockResolvedValue({ ok: true, data: saved }); get.mockResolvedValue({ ok: true, data: saved }); });
describe('engineering API client', () => {
  it('uses only the canonical new-run resources and host profile identity', async () => {
    await startEngineeringRun({ instruction: 'Review', workspace: '/work', permission_profile_id: 'none', request_key: 'one' });
    expect(post.mock.calls[0][0]).toBe('/v2/engineering/runs');
    expect(post.mock.calls[0][1]).toEqual({ instruction: 'Review', workspace: '/work', permission_profile_id: 'none', request_key: 'one' });
    await getEngineeringRun('r1', '/work');
    await cancelEngineeringRun('r1', '/work');
    await resumeEngineeringRun('r1', '/work');
    expect(get.mock.calls[0][0]).toBe('/v2/engineering/runs/r1?workspace=%2Fwork');
    expect(post.mock.calls.slice(1).map((row) => row[0])).toEqual(['/v2/engineering/runs/r1/cancel', '/v2/engineering/runs/r1/resume']);
  });
  it('rejects malformed journal and verifier evidence at the API boundary', async () => {
    get.mockResolvedValue({ ok: true, data: { ...saved, events: [null] } });
    expect((await getEngineeringRun('r1', '/work')).ok).toBe(false);
    get.mockResolvedValue({ ok: true, data: { ...saved, checks: [{ ok: true }] } });
    expect((await getEngineeringRun('r1', '/work')).ok).toBe(false);
  });
  it('rejects legacy or malformed snapshots instead of manufacturing a new DTO', async () => {
    get.mockResolvedValue({ ok: true, data: { ...saved, engine_version: 'legacy' } });
    expect((await getEngineeringRun('old', '/work')).ok).toBe(false);
    get.mockResolvedValue({ ok: true, data: { ...saved, last_seq: undefined } });
    expect((await getEngineeringRun('r1', '/work')).ok).toBe(false);
  });
});
