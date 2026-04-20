import { memo } from 'react';

interface GoalsListProps {
    goals: string[];
}

export const GoalsList = memo(function GoalsList({ goals }: GoalsListProps) {
    return (
        <div className="mt-4 rounded-2xl border border-white/5 bg-white/5 p-4 backdrop-blur-sm">
            <div className="flex items-center justify-between text-xs text-text-muted">
                <span className="font-medium uppercase tracking-wide">圣意总览</span>
                <span className="font-mono">{goals.length ? `${goals.length} 项` : '-'}</span>
            </div>
            <div className="mt-3 max-h-40 space-y-2 overflow-auto pr-1 text-xs text-text-main custom-scrollbar">
                {goals.length === 0 ? (
                    <div className="text-text-dim">暂无圣意条目</div>
                ) : (
                    goals.map((item, idx) => (
                        <div key={`${idx}-${item}`} className="flex items-start gap-2">
                            <span className="mt-0.5 text-accent font-mono text-[10px]">{idx + 1}.</span>
                            <span className="leading-relaxed">{item}</span>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
});
