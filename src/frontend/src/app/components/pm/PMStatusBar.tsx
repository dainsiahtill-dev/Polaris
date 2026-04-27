import {
  Activity,
  Clock,
  Cpu,
  HardDrive,
  CheckCircle2,
  AlertCircle,
  Loader2,
} from 'lucide-react';
import { cn } from '@/app/components/ui/utils';

interface PMStatusBarProps {
  pmRunning: boolean;
  taskCount: number;
  completedCount: number;
  iteration?: number;
}

export function PMStatusBar({
  pmRunning,
  taskCount,
  completedCount,
  iteration,
}: PMStatusBarProps) {
  const progress = taskCount > 0 ? Math.round((completedCount / taskCount) * 100) : 0;

  return (
    <footer className="h-8 flex items-center justify-between px-4 border-t border-white/10 bg-slate-950/80 backdrop-blur-sm text-[11px]">
      {/* Left Section - Status */}
      <div className="flex items-center gap-4">
        {/* PM Status */}
        <div className="flex items-center gap-2">
          {pmRunning ? (
            <>
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-emerald-400 font-medium">PM Running</span>
            </>
          ) : (
            <>
              <div className="w-1.5 h-1.5 rounded-full bg-slate-500" />
              <span className="text-slate-500">PM Stopped</span>
            </>
          )}
        </div>

        {iteration !== undefined && (
          <div className="flex items-center gap-1.5 text-slate-400">
            <Clock className="w-3 h-3" />
            <span>迭代 {iteration}</span>
          </div>
        )}

        <div className="w-px h-3 bg-white/10" />

        {/* Task Progress */}
        <div className="flex items-center gap-2">
          <CheckCircle2 className="w-3 h-3 text-slate-500" />
          <span className="text-slate-400">
            任务: <span className="text-amber-400 font-mono">{completedCount}/{taskCount}</span>
          </span>
          <div className="w-16 h-1 rounded-full bg-slate-800 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-amber-500 to-amber-400 transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="text-slate-500 font-mono">{progress}%</span>
        </div>
      </div>

      {/* Right Section - System Info */}
      <div className="flex items-center gap-4 text-slate-500">
        <div className="flex items-center gap-1.5">
          <Cpu className="w-3 h-3" />
          <span>PM Core</span>
        </div>
        <div className="flex items-center gap-1.5">
          <HardDrive className="w-3 h-3" />
          <span>Storage OK</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Activity className="w-3 h-3" />
          <span className="text-emerald-400/80">Connected</span>
        </div>
      </div>
    </footer>
  );
}

// Status Indicator Component
interface StatusIndicatorProps {
  status: 'running' | 'stopped' | 'error' | 'warning';
  label: string;
}

function StatusIndicator({ status, label }: StatusIndicatorProps) {
  const configs = {
    running: { color: 'bg-emerald-400', animate: true },
    stopped: { color: 'bg-slate-500', animate: false },
    error: { color: 'bg-red-400', animate: true },
    warning: { color: 'bg-amber-400', animate: true },
  };

  const config = configs[status];

  return (
    <div className="flex items-center gap-2">
      <div
        className={cn(
          'w-1.5 h-1.5 rounded-full',
          config.color,
          config.animate && 'animate-pulse'
        )}
      />
      <span
        className={cn(
          'font-medium',
          status === 'running' && 'text-emerald-400',
          status === 'stopped' && 'text-slate-500',
          status === 'error' && 'text-red-400',
          status === 'warning' && 'text-amber-400'
        )}
      >
        {label}
      </span>
    </div>
  );
}
