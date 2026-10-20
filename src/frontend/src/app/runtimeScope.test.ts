import { describe, expect, it } from 'vitest';
import { shouldEnableGlobalBenchObserver } from './runtimeScope';

describe('runtime scope helpers', () => {
  it('keeps the global observer disabled without explicit opt-in', () => {
    expect(shouldEnableGlobalBenchObserver(true, '')).toBe(false);
  });

  it('enables an explicitly requested internal unpinned observer', () => {
    expect(shouldEnableGlobalBenchObserver(true, '', true)).toBe(true);
  });

  it('denies an explicitly requested observer with a workspace pin', () => {
    expect(shouldEnableGlobalBenchObserver(true, '/workspace/scoped', true)).toBe(false);
  });

  it('denies an explicitly requested observer with an instance pin', () => {
    expect(shouldEnableGlobalBenchObserver(true, '', true, 'scoped')).toBe(false);
  });

  it('stays disabled when internal bench mode is disabled', () => {
    expect(shouldEnableGlobalBenchObserver(false, '', true)).toBe(false);
  });
});
