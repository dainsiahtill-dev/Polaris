import { memo } from 'react';
import type { TaskQueueItem } from '../../types/project';

interface TaskQueueProps {
    queueItems: TaskQueueItem[];
}

export const TaskQueue = memo(function TaskQueue({ queueItems }: TaskQueueProps) {
    return (
        <div className="mt-4 border-t border-white/5 pt-3">
            <div className="flex items-center justify-between text-xs text-text-muted">
                <span className="font-medium uppercase tracking-wide">尚书省 → 工部 章奏队列</span>
                <span className="font-mono">{queueItems.length ? `${queueItems.length} 项` : '-'}</span>
            </div>
            <div className="mt-2 max-h-40 space-y-1 overflow-auto pr-1 custom-scrollbar">
                {queueItems.length === 0 ? (
                    <div className="text-xs text-text-dim">暂无待派章奏</div>
                ) : (
                    queueItems.map((item, idx) => (
                        <div
                            key={`${item.key}-${idx}`}
                            className={`flex items-center justify-between gap-2 rounded-md px-2 py-1 text-xs transition-colors ${item.isCurrent
                                ? 'bg-accent/20 text-accent border border-accent/20'
                                : item.isCompleted
                                    ? 'bg-status-success/10 text-status-success/80'
                                    : 'bg-white/5 text-text-dim hover:bg-white/10'
                                }`}
                        >
                            <div className="min-w-0 flex-1 truncate">
                                <span className="text-text-dim/50 mr-2 font-mono">#{idx + 1}</span>
                                {item.title}
                            </div>
                            <span className="shrink-0 text-[10px] opacity-70">
                                {item.isCompleted ? '已完成' : item.isCurrent ? '进行中' : '待开始'}
                            </span>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
});
