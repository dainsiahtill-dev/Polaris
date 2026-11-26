import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getFixedResearchPlan, getResearchRuns } from './engineeringResearchService';
import { apiGet } from './apiClient';

vi.mock('./apiClient', () => ({ apiGet: vi.fn() }));
const get = vi.mocked(apiGet);
const workspace = '/work/front & back';
const identity = { project: '校准', lab: 'measure', plan_sha256: 'b'.repeat(64) };
const plan = { schema: 'engineering.research-plan.v1', project: identity.project, experiments: [{ lab: 'measure', id: 'first', scope: '有限域' }] };
const row = { session_id: 'r1', status: 'completed', verification: 'passed', lab: 'measure', plan_sha256: identity.plan_sha256, created_at: 12 };
beforeEach(() => get.mockReset());

describe('workspace-bound research reads', () => {
  it('rejects identities that would resolve to a different HTTP resource', async () => {
    expect((await getResearchRuns(workspace, '..', null)).ok).toBe(false);
    expect((await getFixedResearchPlan(workspace, '../other', identity)).ok).toBe(false);
    expect(get).not.toHaveBeenCalled();
  });
  it('requests exact project, laboratory and pagination, and rejects foreign rows or workspaces', async () => {
    const response = { workspace, project: identity.project, lab: identity.lab, runs: [row], next_offset: null };
    get.mockResolvedValue({ ok: true, data: response });
    expect((await getResearchRuns(workspace, identity.project, identity.lab)).data?.rows).toEqual([row]);
    const url = new URL(get.mock.calls[0][0], 'http://localhost');
    expect(decodeURIComponent(url.pathname)).toBe('/v2/engineering/research/校准/laboratories');
    expect(Object.fromEntries(url.searchParams)).toEqual({ workspace, lab: 'measure', offset: '0', limit: '20' });
    for (const altered of [{ workspace: '/other' }, { project: 'other' }, { lab: 'other' },
      { runs: [{ ...row, lab: 'other' }] }, { next_offset: 0 }]) {
      get.mockResolvedValue({ ok: true, data: { ...response, ...altered } });
      expect((await getResearchRuns(workspace, identity.project, identity.lab)).ok).toBe(false);
    }
  });

  it('retrieves the exact saved run plan and rejects a changed version, session, workspace or lab', async () => {
    const response = { workspace, session_id: 'historical', research: { lab: identity.lab, plan_sha256: identity.plan_sha256, plan } };
    get.mockResolvedValue({ ok: true, data: response });
    expect((await getFixedResearchPlan(workspace, 'historical', identity)).data).toEqual(plan);
    expect(get.mock.calls[0][0]).toBe('/v2/engineering/runs/historical/research-plan?workspace=%2Fwork%2Ffront+%26+back');
    for (const altered of [{ workspace: '/other' }, { session_id: 'later' },
      { research: { ...response.research, plan_sha256: 'a'.repeat(64) } },
      { research: { ...response.research, lab: 'other' } }, { research: { ...response.research, plan: { ...plan, project: 'other' } } }]) {
      get.mockResolvedValue({ ok: true, data: { ...response, ...altered } });
      expect((await getFixedResearchPlan(workspace, 'historical', identity)).ok).toBe(false);
    }
  });
});
