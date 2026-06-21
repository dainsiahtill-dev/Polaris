import { Loader2 } from 'lucide-react';

interface TestProgressBarProps {
  progress: number;
  running?: boolean;
}

export function TestProgressBar({ progress, running }: TestProgressBarProps) {
  const safe = Math.max(0, Math.min(progress, 100));
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-[10px] text-text-dim">
        <span>进度</span>
        <div className="flex items-center gap-1">
          {running ? <Loader2 className="size-3 animate-spin" /> : null}
          <span>{safe}%</span>
        </div>
      </div>
      <div className="soft-inset h-2 overflow-hidden rounded-full">
        <div
          className="soft-progress h-full transition-all duration-300"
          style={{ width: `${safe}%` }}
        />
      </div>
    </div>
  );
}
