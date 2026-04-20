import { describe, expect, it } from 'vitest';
import type { PmTask } from '@/types/task';
import { isDirectorAssignedTask, splitTaskQueues } from '../useLiveTaskQueues';

function createTask(overrides: Partial<PmTask> = {}): PmTask {
  return {
    id: overrides.id ?? 'task-1',
    title: overrides.title ?? 'Task',
    status: overrides.status ?? 'pending',
    done: overrides.done ?? false,
    priority: overrides.priority ?? 1,
    acceptance: overrides.acceptance ?? [],
    ...overrides,
  };
}

describe('isDirectorAssignedTask', () => {
  it('detects direct role assignment from metadata aliases', () => {
    const task = createTask({
      metadata: {
        assigned_to: 'Lead Director',
      },
    });

    expect(isDirectorAssignedTask(task)).toBe(true);
  });

  it('ignores PM-owned tasks', () => {
    const task = createTask({
      assigned_to: 'pm',
    });

    expect(isDirectorAssignedTask(task)).toBe(false);
  });
});

describe('splitTaskQueues', () => {
  it('falls back to snapshot director tasks until the realtime feed is ready', () => {
    const snapshotTasks = [
      createTask({ id: 'pm-1', title: 'Draft plan' }),
      createTask({ id: 'pm-2', title: 'Implement UI', assigned_to: 'director' }),
    ];

    const result = splitTaskQueues(snapshotTasks, {
      tasks: [],
      isConnected: true,
      runId: null,
    });

    expect(result.pmTasks.map((task) => task.id)).toEqual(['pm-1', 'pm-2']);
    expect(result.directorTasks.map((task) => task.id)).toEqual(['pm-2']);
    expect(result.directorTaskSource).toBe('snapshot');
    expect(result.isDirectorRealtimeConnected).toBe(true);
    expect(result.isDirectorRealtimeReady).toBe(false);
  });

  it('keeps snapshot tasks visible when realtime is connected but has not published queue items yet', () => {
    const snapshotTasks = [
      createTask({ id: 'pm-1', title: 'Draft plan' }),
      createTask({ id: 'pm-2', title: 'Implement API' }),
      createTask({ id: 'pm-3', title: 'Write tests' }),
    ];

    const result = splitTaskQueues(snapshotTasks, {
      tasks: [],
      isConnected: true,
      runId: 'director:workspace',
    });

    expect(result.pmTasks.map((task) => task.id)).toEqual(['pm-1', 'pm-2', 'pm-3']);
    expect(result.directorTasks.map((task) => task.id)).toEqual(['pm-1', 'pm-2', 'pm-3']);
    expect(result.directorTaskSource).toBe('snapshot');
    expect(result.isDirectorRealtimeReady).toBe(true);
  });

  it('uses realtime tasks once a director snapshot has arrived', () => {
    const snapshotTasks = [
      createTask({ id: 'pm-1', title: 'Draft plan' }),
      createTask({ id: 'pm-2', title: 'Legacy director task', assigned_to: 'director' }),
    ];

    const result = splitTaskQueues(snapshotTasks, {
      tasks: [
        createTask({ id: 'dir-1', title: 'Live director task', status: 'in_progress' }),
      ],
      isConnected: true,
      runId: 'director:workspace',
    });

    expect(result.pmTasks.map((task) => task.id)).toEqual(['pm-1', 'pm-2']);
    expect(result.directorTasks.map((task) => task.id)).toEqual(['dir-1']);
    expect(result.directorTaskSource).toBe('realtime');
    expect(result.isDirectorRealtimeReady).toBe(true);
  });

  it('keeps PM tasks visible when all snapshot tasks are assigned to Director', () => {
    const snapshotTasks = [
      createTask({ id: 'pm-1', title: 'Task A', assigned_to: 'director', status: 'done' }),
      createTask({ id: 'pm-2', title: 'Task B', assigned_to: 'director', status: 'todo' }),
    ];

    const result = splitTaskQueues(snapshotTasks, {
      tasks: [],
      isConnected: false,
      runId: null,
    });

    expect(result.pmTasks.map((task) => task.id)).toEqual(['pm-1', 'pm-2']);
    expect(result.pmTasks.map((task) => task.status)).toEqual(['completed', 'pending']);
    expect(result.pmTasks.map((task) => task.done)).toEqual([true, false]);
    expect(result.directorTasks.map((task) => task.id)).toEqual(['pm-1', 'pm-2']);
  });
});
