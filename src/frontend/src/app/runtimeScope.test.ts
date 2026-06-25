import { describe, expect, it } from 'vitest';
import { shouldEnableGlobalBenchObserver } from './runtimeScope';

describe('runtime scope helpers', () => {
  it('keeps the global bench observer enabled on the unpinned main workspace', () => {
    expect(shouldEnableGlobalBenchObserver(true, '')).toBe(true);
  });

  it('disables the global bench observer when a launcher URL pins the workspace', () => {
    expect(shouldEnableGlobalBenchObserver(true, '/tmp/factory-bench-l1-10-r02/L1-10')).toBe(false);
  });

  it('stays disabled when internal bench mode itself is disabled', () => {
    expect(shouldEnableGlobalBenchObserver(false, '')).toBe(false);
  });
});
