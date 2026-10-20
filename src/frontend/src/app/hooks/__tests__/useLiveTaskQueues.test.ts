import { describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { PmTask } from '@/types/task';
import { isDirectorAssignedTask, splitTaskQueues, useLiveTaskQueues } from '../useLiveTaskQueues';

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
  it('keeps Director queue empty until the realtime feed publishes tasks', () => {
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
    expect(result.directorTasks).toEqual([]);
    expect(result.directorTaskSource).toBe('realtime');
    expect(result.isDirectorRealtimeConnected).toBe(true);
    expect(result.isDirectorRealtimeReady).toBe(false);
  });

  it('does not substitute snapshot tasks when realtime is connected but has not published queue items yet', () => {
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
    expect(result.directorTasks).toEqual([]);
    expect(result.directorTaskSource).toBe('realtime');
    expect(result.isDirectorRealtimeReady).toBe(true);
  });

  it('uses realtime tasks once a director snapshot has arrived', () => {
    const snapshotTasks = [
      createTask({ id: 'pm-1', title: 'Draft plan' }),
      createTask({ id: 'pm-2', title: 'Legacy director task', assigned_to: 'director' }),
    ];

    const result = splitTaskQueues(snapshotTasks, {
      tasks: [
        createTask({ id: 'dir-1', title: 'Live director task', assigned_to: 'director', status: 'in_progress' }),
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
    expect(result.directorTasks).toEqual([]);
  });
});


describe('Director projection from the shared TaskRuntime feed', () => {
  it('keeps CE work out of the Director queue and updates when only CE work remains', () => {
    const ce = createTask({ id: 'ce', title: 'Chief Engineer portfolio review', assignee: 'chief_engineer', status: 'in_progress', metadata: { role: 'chief_engineer' } });
    const director = createTask({ id: 'director', title: 'Implement blueprint', assignee: 'director', status: 'in_progress' });
    const snapshotTasks = [ce, director];
    const { result, rerender } = renderHook(({ tasks }) => useLiveTaskQueues({
      snapshotTasks,
      directorRealtime: { tasks, isConnected: true, runId: 'factory:workspace' },
    }), { initialProps: { tasks: [ce, director] } });

    expect(result.current.directorTasks.map((task) => task.id)).toEqual(['director']);
    expect(result.current.pmTasks.map((task) => task.id)).toEqual(['ce', 'director']);
    rerender({ tasks: [ce] });
    expect(result.current.directorTasks).toEqual([]);
  });

  it.each([
    ['top-level Director role', { role: 'director' }, true],
    ['top-level CE role', { role: 'chief_engineer' }, false],
    ['unassigned Director execution', { assignee: '', metadata: { runtime_execution: { role_id: 'director' } } }, true],
    ['Director execution conflicts with CE hint', { metadata: { role: 'chief_engineer', runtime_execution: { role_id: 'director' } } }, false],
    ['metadata role', { metadata: { role: 'director' } }, true],
    ['execution identity', { metadata: { runtime_execution: { role_id: 'director' } } }, true],
    ['legacy metadata assignment', { metadata: { assigned_to: 'Lead Director' } }, true],
    ['CE role overrides legacy Director assignment', { assigned_to: 'director', metadata: { role: 'chief_engineer' } }, false],
    ['CE execution identity overrides legacy assignment', { assigned_to: 'director', metadata: { runtime_execution: { role_id: 'chief_engineer' } } }, false],
    ['conflicting explicit identities', { metadata: { role: 'director', runtime_execution: { role_id: 'qa' } } }, false],
    ['CE assignee conflicts with legacy Director assignment', { assigned_to: 'director', assignee: 'chief_engineer' }, false],
    ['metadata CE assignee conflicts with Director assignment', { assigned_to: 'director', metadata: { assignee: 'chief_engineer' } }, false],
    ['Director identity conflicts with CE assignment', { assignee: 'chief_engineer', metadata: { runtime_execution: { role_id: 'director' } } }, false],
    ['PM assignment', { assignee: 'pm' }, false],
    ['QA role', { metadata: { role: 'qa' } }, false],
    ['unknown role', { metadata: { role: 'unknown' } }, false],
    ['missing identity', {}, false],
    ['misleading assignment substring', { assignee: 'not_director' }, false],
  ])('uses safe role evidence for %s', (_label, fields, expected) => {
    const result = splitTaskQueues([], {
      tasks: [createTask(fields as Partial<PmTask>)], isConnected: true, runId: 'director:workspace',
    });
    expect(result.directorTasks.map((task) => task.id)).toEqual(expected ? ['task-1'] : []);
  });
});


describe('Director hook rejects malformed runtime identity values', () => {
  const locations = [
    ['row role', (value: unknown) => ({ role: value, assignee: 'director' })],
    ['row role_id', (value: unknown) => ({ role_id: value, assignee: 'director' })],
    ['metadata role', (value: unknown) => ({ assignee: 'director', metadata: { role: value } })],
    ['metadata role_id', (value: unknown) => ({ assignee: 'director', metadata: { role_id: value } })],
    ['execution role', (value: unknown) => ({ assignee: 'director', metadata: { runtime_execution: { role: value } } })],
    ['execution role_id', (value: unknown) => ({ assignee: 'director', metadata: { runtime_execution: { role_id: value } } })],
    ['row assigned_to', (value: unknown) => ({ assigned_to: value, metadata: { role: 'director' } })],
    ['row assignedTo', (value: unknown) => ({ assignedTo: value, metadata: { role: 'director' } })],
    ['row assignee', (value: unknown) => ({ assignee: value, metadata: { role: 'director' } })],
    ['metadata assigned_to', (value: unknown) => ({ assignee: 'director', metadata: { assigned_to: value } })],
    ['metadata assignedTo', (value: unknown) => ({ assignee: 'director', metadata: { assignedTo: value } })],
    ['metadata assignee', (value: unknown) => ({ assignee: 'director', metadata: { assignee: value } })],
  ] as const;
  const malformed = [
    ['Director array', ['director']],
    ['empty array', []],
    ['object', { role: 'director' }],
  ] as const;

  it.each(locations.flatMap(([location, fields]) => malformed.map(([shape, value]) => [
    `${location}: ${shape}`, fields(value),
  ] as const)))('excludes %s even with another valid Director hint', (_label, fields) => {
    const { result } = renderHook(() => useLiveTaskQueues({
      snapshotTasks: [],
      directorRealtime: {
        tasks: [createTask(fields as unknown as Partial<PmTask>)],
        isConnected: true,
        runId: 'director:workspace',
      },
    }));
    expect(result.current.directorTasks).toEqual([]);
  });

  it.each([
    ['null optional roles', { role: null, role_id: null, assignee: 'director', metadata: { role: null, role_id: null, runtime_execution: { role: null, role_id: null } } }],
    ['empty optional roles', { role: '', role_id: ' ', assignee: 'director', metadata: { role: ' ', role_id: '', runtime_execution: { role: '', role_id: ' ' } } }],
    ['null optional assignments', { assigned_to: null, assignedTo: null, assignee: 'Director', metadata: { assigned_to: null, assignedTo: null, assignee: null } }],
    ['empty optional assignments', { assigned_to: '', assignedTo: ' ', assignee: 'Lead Director', metadata: { assigned_to: '', assignedTo: ' ', assignee: '' } }],
  ] as const)('retains string Director evidence with %s', (_label, fields) => {
    const { result } = renderHook(() => useLiveTaskQueues({
      snapshotTasks: [],
      directorRealtime: {
        tasks: [createTask(fields as unknown as Partial<PmTask>)],
        isConnected: true,
        runId: 'director:workspace',
      },
    }));
    expect(result.current.directorTasks.map((task) => task.id)).toEqual(['task-1']);
  });
});
