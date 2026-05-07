import { describe, expect, it } from 'vitest';
import type { PmTask } from '@/types/task';
import type { FileEditEvent } from '@/app/hooks/useRuntime';
import {
  buildTaskRealtimeTelemetry,
  computePatchLineStats,
  resolveTaskExecutionStatus,
} from '../taskSelectors';

describe('resolveTaskExecutionStatus', () => {
  it('maps claimed tasks to running', () => {
    const status = resolveTaskExecutionStatus({
      rawStatus: 'CLAIMED',
      done: false,
      completed: false,
      directorRunning: true,
      isCurrent: false,
    });
    expect(status).toBe('running');
  });

  it('maps cancelled tasks to blocked', () => {
    const status = resolveTaskExecutionStatus({
      rawStatus: 'cancelled',
      done: false,
      completed: false,
      directorRunning: true,
      isCurrent: false,
    });
    expect(status).toBe('blocked');
  });

  it('prefers completed flag over raw status', () => {
    const status = resolveTaskExecutionStatus({
      rawStatus: 'pending',
      done: true,
      completed: false,
      directorRunning: true,
      isCurrent: true,
    });
    expect(status).toBe('completed');
  });

  it('falls back to current task as running when status is missing', () => {
    const status = resolveTaskExecutionStatus({
      rawStatus: '',
      done: false,
      completed: false,
      directorRunning: true,
      isCurrent: true,
    });
    expect(status).toBe('running');
  });
});

describe('computePatchLineStats', () => {
  it('calculates added/deleted/modified counts from unified diff', () => {
    const stats = computePatchLineStats(
      [
        '--- a/src/app.ts',
        '+++ b/src/app.ts',
        '@@ -1,3 +1,4 @@',
        '-const a = 1;',
        '+const a = 2;',
        ' const b = 3;',
        '+const c = 4;',
      ].join('\n'),
      'modify',
    );

    expect(stats).toEqual({ added: 1, deleted: 0, modified: 1 });
  });
});

describe('buildTaskRealtimeTelemetry', () => {
  function makeTask(id: string): PmTask {
    return {
      id,
      title: id,
      status: 'pending',
      done: false,
      priority: 0,
      acceptance: [],
    };
  }

  it('aggregates file edit telemetry for each task', () => {
    const tasks: PmTask[] = [makeTask('PM-1')];
    const events: FileEditEvent[] = [
      {
        id: 'evt-1',
        filePath: 'src/a.ts',
        operation: 'modify',
        contentSize: 10,
        taskId: 'PM-1',
        timestamp: '2026-03-05T10:00:00.000Z',
        patch: [
          '--- a/src/a.ts',
          '+++ b/src/a.ts',
          '@@ -1,1 +1,2 @@',
          '-const x = 1;',
          '+const x = 2;',
          '+const y = 3;',
        ].join('\n'),
      },
      {
        id: 'evt-2',
        filePath: 'src/b.ts',
        operation: 'create',
        contentSize: 12,
        taskId: 'PM-1',
        timestamp: '2026-03-05T10:00:01.000Z',
        addedLines: 3,
      },
      {
        id: 'evt-3',
        filePath: 'src/c.ts',
        operation: 'delete',
        contentSize: 0,
        taskId: 'PM-1',
        timestamp: '2026-03-05T10:00:02.000Z',
        deletedLines: 2,
      },
    ];

    const telemetry = buildTaskRealtimeTelemetry(tasks, events);
    const payload = telemetry.get('PM-1');

    expect(payload).toBeTruthy();
    expect(payload?.filesTouchedCount).toBe(3);
    expect(payload?.currentFilePath).toBe('src/c.ts');
    expect(payload?.lineStats).toEqual({ added: 4, deleted: 2, modified: 1 });
    expect(payload?.operationStats).toEqual({ create: 1, modify: 1, delete: 1 });
  });

  it('maps file edit telemetry by PM identity aliases from Director task rows', () => {
    const tasks: PmTask[] = [
      {
        ...makeTask('director-row-1'),
        subject: 'PM-Contract-1',
      },
    ];
    const events: FileEditEvent[] = [
      {
        id: 'evt-subject',
        filePath: 'src/task-board.tsx',
        operation: 'modify',
        contentSize: 20,
        taskId: 'PM-Contract-1',
        timestamp: '2026-05-07T10:00:00.000Z',
        modifiedLines: 2,
      },
    ];

    const telemetry = buildTaskRealtimeTelemetry(tasks, events);

    expect(telemetry.get('director-row-1')?.currentFilePath).toBe('src/task-board.tsx');
    expect(telemetry.get('director-row-1')?.lineStats.modified).toBe(2);
  });
});
