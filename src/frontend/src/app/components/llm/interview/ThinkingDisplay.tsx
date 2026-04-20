import { Brain } from 'lucide-react';

interface ThinkingDisplayProps {
  thinking?: string | null;
  confidence?: number | null;
  format?: string | null;
  title?: string;
}

export function ThinkingDisplay({
  thinking,
  confidence,
  format,
  title = 'Thinking Trace'
}: ThinkingDisplayProps) {
  const hasThinking = Boolean(thinking && thinking.trim().length > 0);
  const confidencePct =
    typeof confidence === 'number' && !Number.isNaN(confidence)
      ? `${Math.round(confidence * 100)}%`
      : 'n/a';
  const formatLabel = format ? format.toUpperCase() : 'UNKNOWN';

  return (
    <div className="rounded-lg border border-white/10 bg-black/20 p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-text-main">
          <Brain className="size-4 text-amber-300" />
          <span>{title}</span>
        </div>
        <div className="text-[10px] text-text-dim uppercase tracking-wide">
          {confidencePct} • {formatLabel}
        </div>
      </div>
      {hasThinking ? (
        <pre className="text-[11px] text-text-main whitespace-pre-wrap font-mono bg-black/40 rounded p-2 border border-white/5 max-h-40 overflow-auto">
          {thinking}
        </pre>
      ) : (
        <div className="text-[11px] text-text-dim italic">No thinking trace detected.</div>
      )}
    </div>
  );
}
