import { memo } from 'react';
import type { ProgressMode } from '../../types/project';
import { UI_TERMS } from '@/app/constants/uiTerminology';

interface ProgressBarProps {
    progress: number;
    progressHint: string;
    progressMode: ProgressMode;
    totalTasks: number;
    completedCount: number;
    successRate?: number | null;
}

export const ProgressBar = memo(function ProgressBar({
    progress,
    progressHint,
    progressMode,
    totalTasks,
    completedCount,
    successRate,
}: ProgressBarProps) {
    const progressPct = Math.round(progress * 100);

    return (
        <div className="soft-panel-subtle rounded-xl p-4 transition-all">
            <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">朝仪推进度</div>
                <div className="text-xs text-text-dim font-mono">{progressHint}</div>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/5">
                <div
                    className="h-full rounded-full soft-progress transition-all duration-500"
                    style={{ width: `${progressPct}%` }}
                />
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-text-muted">
                <span>{progressMode === 'done' ? '已完成' : progressMode === 'position' ? '进行中' : '估算'}</span>
                <span className="text-accent font-mono font-bold">{progressPct}%</span>
            </div>
            <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-text-dim">
                <span className="soft-chip rounded-full px-2 py-0.5 transition-colors">
                    总任务: {totalTasks || '-'}
                </span>
                <span className="soft-chip rounded-full px-2 py-0.5 transition-colors">
                    已完成: {totalTasks ? completedCount : '-'}
                </span>
                {typeof successRate === 'number' ? (
                    <span className="soft-chip rounded-full px-2 py-0.5 transition-colors">
                        {UI_TERMS.roles.director}勘验通过率: {Math.round(successRate * 100)}%
                    </span>
                ) : null}
            </div>
        </div>
    );
});
