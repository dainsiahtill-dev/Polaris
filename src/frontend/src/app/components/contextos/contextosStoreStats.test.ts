import { describe, expect, it } from 'vitest';

import {
  classifyStatus,
  deriveNextSweepAt,
  deriveOldestAgeSeconds,
  formatBytes,
  formatElapsedShort,
  formatRelativeSeconds,
  parseContextStoreStatsResponse,
  type ContextStoreStatsResponse,
} from './contextosStoreStats';

const NOW_MS = new Date('2026-06-19T12:00:00Z').getTime();

describe('formatBytes', () => {
  it('formats B/KB/MB/GB with sensible precision', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1024)).toBe('1.00 KB');
    expect(formatBytes(1536)).toBe('1.50 KB');
    expect(formatBytes(1024 * 1024)).toBe('1.00 MB');
    expect(formatBytes(524288000)).toBe('500 MB');
    expect(formatBytes(1024 ** 4)).toBe('1.00 TB');
  });

  it('handles null / non-finite / negative gracefully', () => {
    expect(formatBytes(null)).toBe('0 B');
    expect(formatBytes(Number.NaN)).toBe('0 B');
    expect(formatBytes(-1)).toBe('0 B');
  });
});

describe('formatElapsedShort', () => {
  it('formats ms / s / m / h thresholds', () => {
    expect(formatElapsedShort(250)).toBe('250ms');
    expect(formatElapsedShort(1500)).toBe('1.50s');
    expect(formatElapsedShort(15_000)).toBe('15.0s');
    expect(formatElapsedShort(90_000)).toBe('1.5m');
    expect(formatElapsedShort(7_200_000)).toBe('2h');
  });

  it('returns null for invalid input', () => {
    expect(formatElapsedShort(null)).toBeNull();
    expect(formatElapsedShort(Number.NaN)).toBeNull();
    expect(formatElapsedShort(-10)).toBeNull();
  });
});

describe('formatRelativeSeconds', () => {
  const nowSec = NOW_MS / 1000;

  it('formats s / m / h / d thresholds', () => {
    expect(formatRelativeSeconds(nowSec - 10, NOW_MS)).toBe('10s 前');
    expect(formatRelativeSeconds(nowSec - 90, NOW_MS)).toBe('1m 前');
    expect(formatRelativeSeconds(nowSec - 3700, NOW_MS)).toBe('1h 前');
    expect(formatRelativeSeconds(nowSec - 90_000, NOW_MS)).toBe('1d 前');
  });

  it('handles very recent / future as 刚刚', () => {
    expect(formatRelativeSeconds(nowSec, NOW_MS)).toBe('刚刚');
    expect(formatRelativeSeconds(nowSec + 100, NOW_MS)).toBe('刚刚');
  });

  it('returns null for null / NaN', () => {
    expect(formatRelativeSeconds(null, NOW_MS)).toBeNull();
    expect(formatRelativeSeconds(Number.NaN, NOW_MS)).toBeNull();
  });
});

describe('classifyStatus', () => {
  it('returns empty when no files and no bytes', () => {
    expect(classifyStatus({ file_count: 0, total_bytes: 0, max_files: 20000, max_total_bytes: 500_000_000, enabled: true })).toBe('empty');
  });

  it('returns disabled when enabled=false even with data', () => {
    expect(classifyStatus({ file_count: 1000, total_bytes: 50_000_000, max_files: 20000, max_total_bytes: 500_000_000, enabled: false })).toBe('disabled');
  });

  it('returns ok when both ratios are < 0.7', () => {
    expect(classifyStatus({ file_count: 1000, total_bytes: 10_000_000, max_files: 20000, max_total_bytes: 500_000_000, enabled: true })).toBe('ok');
  });

  it('returns warning when either ratio is >= 0.7 and < 0.95', () => {
    expect(classifyStatus({ file_count: 15_000, total_bytes: 10_000_000, max_files: 20000, max_total_bytes: 500_000_000, enabled: true })).toBe('warning');
  });

  it('returns critical when either ratio is >= 0.95', () => {
    expect(classifyStatus({ file_count: 19_500, total_bytes: 10_000_000, max_files: 20000, max_total_bytes: 500_000_000, enabled: true })).toBe('critical');
    expect(classifyStatus({ file_count: 1000, total_bytes: 480_000_000, max_files: 20000, max_total_bytes: 500_000_000, enabled: true })).toBe('critical');
  });

  it('treats null/zero caps as missing — falls back to empty', () => {
    expect(classifyStatus({ file_count: 5, total_bytes: 1000, max_files: null, max_total_bytes: null, enabled: true })).toBe('empty');
  });
});

describe('parseContextStoreStatsResponse', () => {
  it('parses a fully-populated response', () => {
    const result = parseContextStoreStatsResponse({
      workspace: '/repo',
      contexts_root: '/repo/runtime/contexts',
      file_count: 1500,
      total_bytes: 1024 * 1024 * 100,
      oldest_mtime: 1718000000.5,
      newest_mtime: 1718500000.5,
      config: {
        ttl_seconds: 604800,
        max_total_bytes: 524288000,
        max_files: 20000,
        sweep_min_interval_seconds: 300,
        enabled: true,
      },
      last_sweep_at: 1718400000.0,
      last_sweep_report: {
        scanned_files: 1500,
        removed_files: 250,
        removed_bytes: 1024 * 1024 * 10,
        kept_files: 1250,
        total_bytes_after: 1024 * 1024 * 90,
        elapsed_ms: 42,
        triggers: ['ttl', 'max_total_bytes'],
      },
    });
    expect(result).not.toBeNull();
    expect(result?.workspace).toBe('/repo');
    expect(result?.file_count).toBe(1500);
    expect(result?.total_bytes).toBe(104857600);
    expect(result?.oldest_mtime).toBeCloseTo(1718000000.5, 5);
    expect(result?.config.ttl_seconds).toBe(604800);
    expect(result?.config.enabled).toBe(true);
    expect(result?.last_sweep_report?.removed_files).toBe(250);
    expect(result?.last_sweep_report?.triggers).toEqual(['ttl', 'max_total_bytes']);
  });

  it('tolerates missing optional fields (returns nulls, never throws)', () => {
    const result = parseContextStoreStatsResponse({
      workspace: '/repo',
      contexts_root: '/repo/runtime/contexts',
      file_count: 0,
      total_bytes: 0,
      config: { enabled: true },
      last_sweep_at: 0,
    });
    expect(result).not.toBeNull();
    expect(result?.oldest_mtime).toBeNull();
    expect(result?.newest_mtime).toBeNull();
    expect(result?.config.ttl_seconds).toBeNull();
    expect(result?.config.max_files).toBeNull();
    expect(result?.config.max_total_bytes).toBeNull();
    expect(result?.config.sweep_min_interval_seconds).toBeNull();
    expect(result?.last_sweep_report).toBeNull();
  });

  it('returns null for non-object input', () => {
    expect(parseContextStoreStatsResponse(null)).toBeNull();
    expect(parseContextStoreStatsResponse(undefined)).toBeNull();
    expect(parseContextStoreStatsResponse('string')).toBeNull();
    expect(parseContextStoreStatsResponse(42)).toBeNull();
  });

  it('coerces numeric strings safely', () => {
    const result = parseContextStoreStatsResponse({
      workspace: '/repo',
      contexts_root: '/repo/runtime/contexts',
      file_count: '12',
      total_bytes: '345',
      config: {},
      last_sweep_at: '0',
    });
    expect(result?.file_count).toBe(12);
    expect(result?.total_bytes).toBe(345);
  });

  it('keeps triggers array non-string entries filtered out', () => {
    const result = parseContextStoreStatsResponse({
      workspace: '/repo',
      contexts_root: '/repo/runtime/contexts',
      file_count: 0,
      total_bytes: 0,
      config: {},
      last_sweep_at: 0,
      last_sweep_report: {
        scanned_files: 0,
        removed_files: 0,
        removed_bytes: 0,
        kept_files: 0,
        total_bytes_after: 0,
        elapsed_ms: 0,
        triggers: ['ttl', 1, null, 'max_files'],
      },
    });
    expect(result?.last_sweep_report?.triggers).toEqual(['ttl', 'max_files']);
  });
});

describe('deriveNextSweepAt', () => {
  it('returns last_sweep_at + interval when both present', () => {
    const stats: ContextStoreStatsResponse = {
      workspace: '/repo',
      contexts_root: '',
      file_count: 0,
      total_bytes: 0,
      oldest_mtime: null,
      newest_mtime: null,
      config: {
        ttl_seconds: null,
        max_total_bytes: null,
        max_files: null,
        sweep_min_interval_seconds: 300,
        enabled: true,
      },
      last_sweep_at: 1000,
      last_sweep_report: null,
    };
    expect(deriveNextSweepAt(stats)).toBe(1300);
  });

  it('returns null when interval or last_sweep_at is missing', () => {
    const stats: ContextStoreStatsResponse = {
      workspace: '/repo',
      contexts_root: '',
      file_count: 0,
      total_bytes: 0,
      oldest_mtime: null,
      newest_mtime: null,
      config: {
        ttl_seconds: null,
        max_total_bytes: null,
        max_files: null,
        sweep_min_interval_seconds: null,
        enabled: true,
      },
      last_sweep_at: 1000,
      last_sweep_report: null,
    };
    expect(deriveNextSweepAt(stats)).toBeNull();
  });
});

describe('deriveOldestAgeSeconds', () => {
  it('returns floor seconds between now and oldest_mtime', () => {
    const nowSec = NOW_MS / 1000;
    const stats: ContextStoreStatsResponse = {
      workspace: '/repo',
      contexts_root: '',
      file_count: 0,
      total_bytes: 0,
      oldest_mtime: nowSec - 90,
      newest_mtime: null,
      config: {
        ttl_seconds: null,
        max_total_bytes: null,
        max_files: null,
        sweep_min_interval_seconds: null,
        enabled: true,
      },
      last_sweep_at: 0,
      last_sweep_report: null,
    };
    expect(deriveOldestAgeSeconds(stats, NOW_MS)).toBe(90);
  });

  it('returns null when oldest_mtime missing', () => {
    const stats: ContextStoreStatsResponse = {
      workspace: '/repo',
      contexts_root: '',
      file_count: 0,
      total_bytes: 0,
      oldest_mtime: null,
      newest_mtime: null,
      config: {
        ttl_seconds: null,
        max_total_bytes: null,
        max_files: null,
        sweep_min_interval_seconds: null,
        enabled: true,
      },
      last_sweep_at: 0,
      last_sweep_report: null,
    };
    expect(deriveOldestAgeSeconds(stats, NOW_MS)).toBeNull();
  });

  it('returns 0 when oldest_mtime is in the future (clock skew safe)', () => {
    const nowSec = NOW_MS / 1000;
    const stats: ContextStoreStatsResponse = {
      workspace: '/repo',
      contexts_root: '',
      file_count: 0,
      total_bytes: 0,
      oldest_mtime: nowSec + 100,
      newest_mtime: null,
      config: {
        ttl_seconds: null,
        max_total_bytes: null,
        max_files: null,
        sweep_min_interval_seconds: null,
        enabled: true,
      },
      last_sweep_at: 0,
      last_sweep_report: null,
    };
    expect(deriveOldestAgeSeconds(stats, NOW_MS)).toBe(0);
  });
});