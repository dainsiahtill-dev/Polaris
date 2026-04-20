import { isLancedbExplicitlyBlocked } from '../lancedbGate';

describe('isLancedbExplicitlyBlocked', () => {
  it('does not block when status is unknown', () => {
    expect(isLancedbExplicitlyBlocked(null)).toBe(false);
    expect(isLancedbExplicitlyBlocked(undefined)).toBe(false);
  });

  it('does not block when LanceDB is healthy', () => {
    expect(isLancedbExplicitlyBlocked({ ok: true })).toBe(false);
  });

  it('blocks only on explicit unhealthy status', () => {
    expect(isLancedbExplicitlyBlocked({ ok: false, error: 'missing' })).toBe(true);
  });
});
