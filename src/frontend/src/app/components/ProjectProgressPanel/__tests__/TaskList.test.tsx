import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TaskList } from '../TaskList';
import type { PmTask } from '@/types/task';

describe('TaskList', () => {
    const clampText = (text: string) => text;
    const taskKey = (task: PmTask) => task.id;
    const isTaskDone = () => false;

    it('renders string-based acceptance entries from runtime payloads', () => {
        const tasks = [
            {
                id: 'TASK-001',
                title: 'Scaffold contracts',
                goal: 'Create the first project contract',
                status: 'pending',
                done: false,
                priority: 1,
                acceptance: [
                    'Create API skeleton',
                    'Add smoke verification',
                ] as unknown as PmTask['acceptance'],
            },
        ] as PmTask[];

        render(
            <TaskList
                tasks={tasks}
                completedSet={new Set<string>()}
                taskKey={taskKey}
                isTaskDone={isTaskDone}
                clampText={clampText}
            />,
        );

        expect(screen.getByText('Create API skeleton')).toBeInTheDocument();
        expect(screen.getByText('Add smoke verification')).toBeInTheDocument();
    });
});
