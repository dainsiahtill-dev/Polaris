import { memo } from 'react';

interface PlanPreviewProps {
    planText: string;
    planUpdated?: string;
}

export const PlanPreview = memo(function PlanPreview({ planText, planUpdated }: PlanPreviewProps) {
    return (
        <div className="mt-4 rounded-2xl border border-white/5 bg-white/5 p-4 backdrop-blur-sm">
            <div className="flex items-center justify-between text-xs text-text-muted">
                <span className="font-medium uppercase tracking-wide">敕令总图 (contracts/plan.md)</span>
                {planUpdated ? (
                    <span className="text-text-dim font-mono">{planUpdated}</span>
                ) : (
                    <span className="text-text-dim">-</span>
                )}
            </div>
            <div className="mt-3 max-h-56 overflow-auto rounded-xl border border-white/5 bg-bg-panel/50 px-3 py-2 text-xs text-text-code whitespace-pre-wrap leading-relaxed custom-scrollbar shadow-inner">
                {planText || '暂无敕令总图'}
            </div>
        </div>
    );
});
