import { memo } from 'react';
import { ArrowRight, CheckCircle, Clock } from 'lucide-react';
import type { PmTask } from '../../types/project';

interface TaskListProps {
    tasks: PmTask[];
    completedSet: Set<string>;
    currentTaskKey?: string;
    taskKey: (task: PmTask) => string;
    isTaskDone: (task: PmTask) => boolean;
    clampText: (text: string, maxLen: number) => string;
}

function TaskListComponent({
    tasks,
    completedSet,
    currentTaskKey,
    taskKey,
    isTaskDone,
    clampText,
}: TaskListProps) {
    const toAcceptanceText = (item: unknown): string => {
        if (typeof item === 'string') {
            return item.trim();
        }
        if (typeof item === 'object' && item && 'description' in item) {
            return String((item as { description?: unknown }).description || '').trim();
        }
        return '';
    };

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto pr-1 custom-scrollbar">
            {tasks.length === 0 ? (
                <div className="col-span-full rounded-xl border border-dashed border-white/10 bg-white/5 p-6 text-center text-sm text-text-dim">
                    待PM Office出具Task清单...
                </div>
            ) : (
                tasks.map((task, index) => {
                    const key = taskKey(task);
                    const isCompleted = completedSet.has(key) || isTaskDone(task);
                    const isCurrent = currentTaskKey === key;
                    const title = task.title || task.goal || task.id || `Task ${index + 1}`;
                    const goal = task.goal && task.goal !== title ? task.goal : '';
                    const acceptance = Array.isArray(task.acceptance)
                        ? task.acceptance
                            .map((item) => toAcceptanceText(item))
                            .filter((item) => item.length > 0)
                            .slice(0, 3)
                        : [];

                    return (
                        <div
                            key={`${key || title}-${index}`}
                            data-testid="project-task-item"
                            data-task-id={task.id || ''}
                            className={`rounded-xl border p-3 transition-all duration-300 ${isCurrent
                                ? 'border-accent/50 bg-accent/5 shadow-[0_0_15px_rgba(124,58,237,0.1)]'
                                : isCompleted
                                    ? 'border-status-success/30 bg-status-success/5 opacity-80'
                                    : 'border-white/5 bg-white/5 hover:border-white/10 hover:bg-white/10'
                                }`}
                        >
                            <div className="flex flex-wrap items-start justify-between gap-3">
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 text-xs text-text-dim font-mono">
                                        <span>#{index + 1}</span>
                                        {task.id ? (
                                            <span className="rounded-full border border-white/10 px-2 py-0.5">ID: {task.id}</span>
                                        ) : null}
                                        {task.priority !== undefined ? (
                                            <span className="rounded-full border border-white/10 px-2 py-0.5">P{task.priority}</span>
                                        ) : null}
                                    </div>
                                    <div data-testid="project-task-title" className="mt-1 text-sm font-semibold text-text-main">{clampText(title, 120)}</div>
                                    {goal ? <div data-testid="project-task-goal" className="mt-2 text-xs text-text-muted">{clampText(goal, 180)}</div> : null}
                                </div>
                                <div data-testid="project-task-status" className="flex items-center gap-2 text-xs text-text-dim">
                                    {isCompleted ? (
                                        <CheckCircle className="size-4 text-status-success" />
                                    ) : isCurrent ? (
                                        <ArrowRight className="size-4 text-accent animate-pulse" />
                                    ) : (
                                        <Clock className="size-4 text-text-dim" />
                                    )}
                                    <span className="rounded-full border border-white/10 px-2 py-0.5 backdrop-blur-sm">
                                        {isCompleted ? '已完成' : isCurrent ? '进行中' : '待开始'}
                                    </span>
                                </div>
                            </div>
                            {acceptance.length > 0 ? (
                                <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-text-muted">
                                    {acceptance.map((item, idx) => (
                                        <span
                                            key={`${key}-acc-${idx}`}
                                            data-testid="project-task-acceptance"
                                            className="rounded-full bg-bg-surface/50 px-2 py-0.5 border border-white/5"
                                        >
                                            {clampText(item, 80)}
                                        </span>
                                    ))}
                                </div>
                            ) : null}
                        </div>
                    );
                })
            )}
        </div>
    );
}

export const TaskList = memo(TaskListComponent, (prevProps, nextProps) => {
    // Custom comparison - only re-render if tasks or completedSet changes
    return (
        prevProps.tasks === nextProps.tasks &&
        prevProps.completedSet === nextProps.completedSet &&
        prevProps.currentTaskKey === nextProps.currentTaskKey
    );
});
