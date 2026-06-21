import { memo } from 'react';

interface PlanPreviewProps {
    planText: string;
    planUpdated?: string;
}

export const PlanPreview = memo(function PlanPreview({ planText, planUpdated }: PlanPreviewProps) {
    return (
        <div className="mt-4 soft-panel-subtle rounded-xl p-4">
            <div className="flex items-center justify-between text-xs text-text-muted">
                <span className="font-medium uppercase tracking-wide">敕令总图 (contracts/plan.md)</span>
                {planUpdated ? (
                    <span className="text-text-dim font-mono">{planUpdated}</span>
                ) : (
                    <span className="text-text-dim">-</span>
                )}
            </div>
            <div className="mt-3 max-h-56 overflow-auto soft-inset rounded-xl px-3 py-2 text-xs text-text-code whitespace-pre-wrap leading-relaxed custom-scrollbar">
                {planText || '暂无敕令总图'}
            </div>
        </div>
    );
});
