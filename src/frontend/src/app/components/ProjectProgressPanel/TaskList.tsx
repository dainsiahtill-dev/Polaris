import { memo } from 'react';
import { ArrowRight, CheckCircle, Clock } from 'lucide-react';
import type { PmTask } from '../../types/project';

interface TaskListProps {
    tasks: PmTask[];
    completedSet: Set<string>;
    currentTaskKey?: string;
    taskKey: (task: PmTask) => string;
    isTaskDone: (task: PmTask) => boolean;
    clampText: (text: unknown, maxLen: number) => string;
}

const toDisplayText = (value: unknown): string => {
    if (typeof value === 'string') return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean') return String(value).trim();
    return '';
};

const isReadableTaskTitle = (value: unknown): boolean => {
    const text = toDisplayText(value);
    if (!text) return false;
    return !/^\d+$/.test(text);
};

const pickTaskTitle = (task: PmTask, fallback: string): string => {
    const record = task as PmTask & Record<string, unknown>;
    const candidates = [record.subject, task.title, task.goal, record.summary, record.description];
    for (const candidate of candidates) {
        if (isReadableTaskTitle(candidate)) return toDisplayText(candidate);
    }
    return fallback;
};

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
                    const idText = toDisplayText(task.id);
                    const title = pickTaskTitle(task, idText || `Task ${index + 1}`);
                    const goalText = toDisplayText(task.goal);
                    const goal = goalText && goalText !== title ? goalText : '';
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
                            data-task-id={idText}
                            className={`rounded-xl border p-3 transition-all duration-300 ${isCurrent
                                ? 'soft-raised border-white/[0.15]'
                                : isCompleted
                                    ? 'border-status-success/30 bg-status-success/5 opacity-80'
                                    : 'border-white/5 bg-white/5 hover:border-white/10 hover:bg-white/10'
                                }`}
                        >
                            <div className="flex flex-wrap items-start justify-between gap-3">
                                <div className="min-w-0 flex-1">
                                    <div data-testid="project-task-title" className="text-sm font-semibold leading-6 text-text-main">{clampText(title, 120)}</div>
                                    {goal ? <div data-testid="project-task-goal" className="mt-2 text-xs text-text-muted">{clampText(goal, 180)}</div> : null}
                                    <div
                                        data-testid="project-task-metadata"
                                        className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-text-dim"
                                    >
                                        <span className="font-mono">任务 #{index + 1}</span>
                                        {idText ? (
                                            <span className="font-mono">ID {idText}</span>
                                        ) : null}
                                        {task.priority !== undefined ? (
                                            <span>优先级 {task.priority}</span>
                                        ) : null}
                                    </div>
                                </div>
                                <div data-testid="project-task-status" className="flex items-center gap-2 text-xs text-text-dim">
                                    {isCompleted ? (
                                        <CheckCircle className="size-4 text-status-success" />
                                    ) : isCurrent ? (
                                        <ArrowRight className="size-4 text-accent" />
                                    ) : (
                                        <Clock className="size-4 text-text-dim" />
                                    )}
                                            <span className="soft-chip rounded-full px-2 py-0.5">
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
