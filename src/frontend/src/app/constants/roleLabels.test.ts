import { describe, expect, it } from 'vitest';

import { getRoleDisplayLabel } from './roleLabels';

describe('getRoleDisplayLabel', () => {
  it('keeps Director and Chief Engineer as separate role labels', () => {
    expect(getRoleDisplayLabel('pm')).toBe('PM');
    expect(getRoleDisplayLabel('director')).toBe('Director');
    expect(getRoleDisplayLabel('chief_engineer')).toBe('Chief Engineer');
  });

  it('keeps docs mapped to Architect for legacy LLM config compatibility', () => {
    expect(getRoleDisplayLabel('docs')).toBe('Architect');
    expect(getRoleDisplayLabel('custom_role')).toBe('custom_role');
  });
});
