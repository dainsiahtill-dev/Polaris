import { describe, expect, it } from 'vitest';

import { getReadableBackendName } from './ProcessMonitorSidebar';

describe('getReadableBackendName', () => {
  it('keeps Director and Chief Engineer process labels distinct', () => {
    expect(getReadableBackendName('director').label).toBe('Director');
    expect(getReadableBackendName('chief_engineer').label).toBe('Chief Engineer');
  });

  it('normalizes spaced role mode names before lookup', () => {
    expect(getReadableBackendName('chief engineer').label).toBe('Chief Engineer');
  });
});
