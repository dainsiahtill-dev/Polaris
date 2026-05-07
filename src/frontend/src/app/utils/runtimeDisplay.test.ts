import { describe, expect, it } from 'vitest';
import {
  cleanRuntimeDisplayText,
  isStructuredRuntimeFragmentText,
  normalizeStartedAtSeconds,
} from './runtimeDisplay';

describe('runtimeDisplay', () => {
  it('filters structured JSON fragments from compact runtime labels', () => {
    expect(isStructuredRuntimeFragmentText('}')).toBe(true);
    expect(isStructuredRuntimeFragmentText('"summary": {}')).toBe(true);
    expect(cleanRuntimeDisplayText('}')).toBeNull();
    expect(cleanRuntimeDisplayText('"updated_at": "2026-05-07T07:16:25Z",')).toBeNull();
  });

  it('keeps human-readable runtime labels', () => {
    expect(cleanRuntimeDisplayText('调用工具: shell_command')).toBe('调用工具: shell_command');
    expect(cleanRuntimeDisplayText('  Director is applying patch  ')).toBe('Director is applying patch');
  });

  it('normalizes epoch seconds, epoch milliseconds, and ISO strings', () => {
    expect(normalizeStartedAtSeconds(1771594105)).toBe(1771594105);
    expect(normalizeStartedAtSeconds(1771594105000)).toBe(1771594105);
    expect(normalizeStartedAtSeconds('2026-02-20T13:28:25Z')).toBe(1771594105);
  });

  it('rejects accidental 1970 timestamps that create huge active durations', () => {
    expect(normalizeStartedAtSeconds(1771594)).toBeNull();
  });
});
