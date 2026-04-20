import { describe, expect, it } from 'vitest';
import type { LogEntry } from '@/types/log';
import {
  filterExecutionActivityLogs,
  getLatestExecutionActivityLog,
  getRuntimeProcessStreamKind,
  isArtifactProcessChannel,
  isExecutionProcessChannel,
  isProcessStreamChannel,
} from '@/app/utils/appRuntime';

function createLogEntry(
  overrides: Partial<LogEntry> = {},
  meta: Record<string, unknown> = {},
): LogEntry {
  return {
    id: overrides.id || 'log-1',
    timestamp: overrides.timestamp || '2026-03-07T15:52:58.000Z',
    level: overrides.level || 'info',
    source: overrides.source || 'Process',
    message: overrides.message || 'sample message',
    details: overrides.details,
    meta: Object.keys(meta).length > 0 ? meta : overrides.meta,
  };
}

describe('appRuntime execution stream filtering', () => {
  it('classifies execution and artifact channels separately', () => {
    expect(getRuntimeProcessStreamKind('pm_subprocess')).toBe('execution');
    expect(getRuntimeProcessStreamKind('PM_REPORT')).toBe('artifact');
    expect(getRuntimeProcessStreamKind('unknown')).toBeNull();
    expect(isProcessStreamChannel('director_console')).toBe(true);
    expect(isExecutionProcessChannel('pm_log')).toBe(true);
    expect(isArtifactProcessChannel('planner')).toBe(true);
  });

  it('filters artifact logs out of realtime execution activity', () => {
    const logs = [
      createLogEntry({ id: 'exec', source: 'PM', message: 'tool call started' }, { channel: 'pm_subprocess' }),
      createLogEntry({ id: 'artifact', source: 'PM-Report', message: '## 2026-03-07 23:52:58 (iteration 1) - agents' }, { channel: 'pm_report' }),
      createLogEntry({ id: 'engine', source: 'Engine', message: 'phase executing' }, { channel: 'engine_status' }),
    ];

    expect(filterExecutionActivityLogs(logs).map((log) => log.id)).toEqual(['exec', 'engine']);
  });

  it('falls back to source when older logs have no channel metadata', () => {
    const logs = [
      createLogEntry({ id: 'artifact', source: 'PM-Report', message: 'Status: AGENTS.md auto-adopted.' }),
      createLogEntry({ id: 'exec', source: 'PM', message: '执行任务合同校验' }),
    ];

    expect(getLatestExecutionActivityLog(logs)?.id).toBe('exec');
  });
});
