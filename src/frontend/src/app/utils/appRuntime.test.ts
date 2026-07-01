import { describe, expect, it } from 'vitest';
import type { LogEntry } from '@/types/log';
import {
  filterExecutionActivityLogs,
  getLatestExecutionActivityLog,
  getRuntimeProcessStreamKind,
  isArtifactProcessChannel,
  isExecutionProcessChannel,
  isProcessStreamChannel,
  readEngineRoleDetail,
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
  it('classifies only canonical runtime.v2 process channels', () => {
    expect(getRuntimeProcessStreamKind('system')).toBe('execution');
    expect(getRuntimeProcessStreamKind('PROCESS')).toBe('execution');
    expect(getRuntimeProcessStreamKind('pm_subprocess')).toBeNull();
    expect(getRuntimeProcessStreamKind('PM_REPORT')).toBeNull();
    expect(getRuntimeProcessStreamKind('unknown')).toBeNull();
    expect(isProcessStreamChannel('director_console')).toBe(false);
    expect(isExecutionProcessChannel('pm_log')).toBe(false);
    expect(isArtifactProcessChannel('planner')).toBe(false);
  });

  it('filters artifact logs out of realtime execution activity', () => {
    const logs = [
      createLogEntry({ id: 'exec', source: 'PM', message: 'tool call started' }, { channel: 'process' }),
      createLogEntry({ id: 'artifact', source: 'PM-Report', message: '## 2026-03-07 23:52:58 (iteration 1) - agents' }, { channel: 'pm_report' }),
      createLogEntry({ id: 'engine', source: 'Engine', message: 'phase executing' }, { channel: 'system' }),
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

  it('reads engine role details across role naming variants', () => {
    const roles = {
      PM: { detail: 'PM iteration failed' },
      ChiefEngineer: { detail: 'Blueprint generation skipped' },
      director: { detail: 'Director dispatch skipped' },
    };

    expect(readEngineRoleDetail(roles, ['Chief Engineer', 'chief_engineer'])).toBe('Blueprint generation skipped');
    expect(readEngineRoleDetail(roles, ['Director'])).toBe('Director dispatch skipped');
    expect(readEngineRoleDetail(roles, ['QA'])).toBe('');
  });
});
