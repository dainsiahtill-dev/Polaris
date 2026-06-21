import { memo } from 'react';
import { ArrowRight } from 'lucide-react';
import { UI_TERMS } from '@/app/constants/uiTerminology';

interface CurrentTaskCardProps {
    currentSummary: string;
    lastTaskId?: string;
}

export const CurrentTaskCard = memo(function CurrentTaskCard({ currentSummary, lastTaskId }: CurrentTaskCardProps) {
    return (
        <div className="soft-panel-subtle rounded-xl p-4 transition-all">
            <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">当前Director Task</div>
                <ArrowRight className="size-4 text-accent" />
            </div>
            {currentSummary ? (
                <div className="mt-3 flex items-start gap-3">
                    <div className="mt-1 flex size-8 items-center justify-center soft-raised rounded-full text-accent">
                        <ArrowRight className="size-4" />
                    </div>
                    <div className="min-w-0">
                        <div className="text-sm font-semibold text-text-main">{currentSummary}</div>
                        <div className="mt-1 text-xs text-text-dim font-mono">
                            {lastTaskId ? <span>ID: {lastTaskId}</span> : <span>待{UI_TERMS.roles.pm}分派Task</span>}
                        </div>
                    </div>
                </div>
            ) : (
                <div className="mt-3 text-sm text-text-dim">暂无当前差事案卷</div>
            )}
        </div>
    );
});
