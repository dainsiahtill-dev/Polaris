import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetchMock = vi.fn();

vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

vi.mock('@/app/utils/devLogger', () => ({
  devLogger: {
    warn: vi.fn(),
  },
}));

import { residentService } from '../api';

describe('residentService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the Resident AGI audit pack from the read-only endpoint', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          schema_version: 'resident.agi_audit_pack.v1',
          role_id: 'resident_agi',
        }),
        { status: 200 },
      ),
    );

    const result = await residentService.getAgiAuditPack('/tmp/polaris-demo', 12);

    expect(result.ok).toBe(true);
    expect(result.data?.schema_version).toBe('resident.agi_audit_pack.v1');
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/resident/agi/audit-pack?workspace=%2Ftmp%2Fpolaris-demo&decision_limit=12',
    );
  });
});
