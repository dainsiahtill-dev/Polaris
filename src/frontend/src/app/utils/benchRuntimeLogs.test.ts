import { describe, expect, it } from 'vitest';
import type { FactoryBenchEvent } from '@/services/benchService';
import type { LogEntry } from '@/types/log';
import { benchEventToProcessLog, mergeProcessAndBenchLogs } from './benchRuntimeLogs';

function benchEvent(overrides: Partial<FactoryBenchEvent> = {}): FactoryBenchEvent {
  return {
    seq: 7,
    type: 'factory_bench.project.completed',
    name: 'factory_bench.project.completed',
    actor: 'factory_bench',
    summary: 'L2 project completed',
    ok: true,
    ts: '2026-06-18T14:00:00.000Z',
    session_id: 'bench-l1-l2',
    meta: {
      project_id: 'L2-01',
      level: 2,
      completed: 1,
      failed: 0,
    },
    ...overrides,
  };
}

function processLog(overrides: Partial<LogEntry> = {}): LogEntry {
  return {
    id: 'process-1',
    timestamp: '2026-06-18T13:59:00.000Z',
    level: 'info',
    source: 'PM',
    message: 'PM started',
    ...overrides,
  };
}

describe('benchRuntimeLogs', () => {
  it('maps bench events to platform process logs without losing audit metadata', () => {
    const log = benchEventToProcessLog(benchEvent());

    expect(log).toMatchObject({
      id: 'bench-bench-l1-l2-7',
      timestamp: '2026-06-18T14:00:00.000Z',
      level: 'success',
      source: 'Factory Bench',
      title: 'factory_bench.project.completed',
      message: 'L2 project completed',
      tags: ['bench', 'L2-01', 'L2'],
      meta: expect.objectContaining({
        project_id: 'L2-01',
        level: 2,
        bench_session_id: 'bench-l1-l2',
        bench_event_type: 'factory_bench.project.completed',
        bench_seq: 7,
      }),
    });
    expect(log.details).toContain('"session_id":"bench-l1-l2"');
  });

  it('marks failed and cancelled bench events with visible severity', () => {
    expect(benchEventToProcessLog(benchEvent({
      type: 'factory_bench.project.failed',
      ok: false,
    })).level).toBe('error');

    expect(benchEventToProcessLog(benchEvent({
      type: 'factory_bench.cancelled',
      ok: null,
    })).level).toBe('warning');
  });

  it('merges process logs and bench events in timestamp order with de-duplication and limit', () => {
    const processLogs = [
      processLog({ id: 'process-old', timestamp: '2026-06-18T13:58:00.000Z' }),
      processLog({ id: 'process-new', timestamp: '2026-06-18T14:02:00.000Z' }),
    ];
    const event = benchEvent({ seq: 9, ts: '2026-06-18T14:01:00.000Z' });
    const logs = mergeProcessAndBenchLogs(processLogs, [event, event], 2);

    expect(logs.map((log) => log.id)).toEqual([
      'bench-bench-l1-l2-9',
      'process-new',
    ]);
  });
});
