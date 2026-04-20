import { useState } from 'react';
import {
  Brain,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Circle,
  Loader2,
  AlertCircle,
  Sparkles,
  XCircle,
} from 'lucide-react';
import { cn } from '@/app/components/ui/utils';

interface IntentData {
  current: string;
  target: string;
  progress: number;
}

interface PlanStep {
  step: number;
  label: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
}

interface Decision {
  content: string;
  reason: string;
}

interface ThinkingCardProps {
  intent?: IntentData;
  planSteps?: PlanStep[];
  toolStatus?: Record<string, 'pending' | 'running' | 'completed' | 'failed'>;
  decisions?: Decision[];
  thinking?: string;
  roleName?: string;
  theme?: {
    primary: string;
    secondary: string;
  };
}

export function ThinkingCard({
  intent,
  planSteps,
  toolStatus,
  decisions,
  thinking,
  roleName = 'AI',
  theme = { primary: 'indigo', secondary: 'indigo-400' },
}: ThinkingCardProps) {
  const [expanded, setExpanded] = useState(true);

  const hasThinkingContent = intent || planSteps?.length || toolStatus || decisions?.length;
  
  // 检查是否有正在运行的任务
  const isRunning = planSteps?.some(s => s.status === 'running') || 
                    Object.values(toolStatus || {}).some(s => s === 'running');

  if (!hasThinkingContent && !thinking) {
    return null;
  }

  const getStatusIcon = (status?: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle2 className="w-3 h-3 text-emerald-400" />;
      case 'running':
        return <Loader2 className="w-3 h-3 text-amber-400 animate-spin" />;
      case 'failed':
        return <XCircle className="w-3 h-3 text-red-400" />;
      default:
        return <Circle className="w-3 h-3 text-slate-500" />;
    }
  };

  return (
    <div className={cn(
      "mb-3 rounded-lg border border-white/10 bg-slate-900/50 overflow-hidden",
      // 运行状态时添加微妙的呼吸光效
      isRunning && "animate-pulse border-amber-500/30"
    )}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-white/5 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Brain className={cn(
            "w-4 h-4",
            isRunning ? "text-amber-400 animate-pulse" : "text-slate-400"
          )} />
          <span className="text-xs font-medium text-slate-300">
            {roleName} {isRunning ? '处理中' : '思考中'}
          </span>
          {intent?.progress !== undefined && (
            <span className={cn(
              "text-[10px]",
              intent.progress === 100 ? "text-emerald-400" : "text-slate-500"
            )}>
              [{intent.progress}%]
            </span>
          )}
        </div>
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-slate-500" />
        ) : (
          <ChevronRight className="w-4 h-4 text-slate-500" />
        )}
      </button>

      {/* Content */}
      {expanded && (
        <div className="px-3 pb-3 space-y-3">
          {/* Intent */}
          {intent && (
            <div className="flex items-center gap-2 text-xs">
              <Sparkles className="w-3 h-3 text-amber-400" />
              <span className="text-slate-400">意图:</span>
              <span className="text-slate-200">{intent.target}</span>
            </div>
          )}

          {/* Plan Steps */}
          {planSteps && planSteps.length > 0 && (
            <div>
              <div className="text-[10px] text-slate-500 mb-1">计划进度:</div>
              <div className="space-y-1">
                {planSteps.map((step, idx) => (
                  <div
                    key={idx}
                    className={cn(
                      'flex items-center gap-2 text-xs px-2 py-1 rounded',
                      step.status === 'running' && 'bg-amber-500/10',
                      step.status === 'completed' && 'bg-emerald-500/10',
                    )}
                  >
                    {getStatusIcon(step.status)}
                    <span
                      className={cn(
                        'text-slate-400',
                        step.status === 'completed' && 'text-slate-500 line-through',
                        step.status === 'running' && 'text-amber-400',
                      )}
                    >
                      {step.step}. {step.label}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tool Status */}
          {toolStatus && Object.keys(toolStatus).length > 0 && (
            <div>
              <div className="text-[10px] text-slate-500 mb-1">工具:</div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(toolStatus).map(([tool, status]) => (
                  <div
                    key={tool}
                    className={cn(
                      'flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border',
                      status === 'completed' && 'bg-emerald-500/10 border-emerald-500/20',
                      status === 'running' && 'bg-amber-500/10 border-amber-500/20',
                      status === 'failed' && 'bg-red-500/10 border-red-500/20',
                      status === 'pending' && 'bg-slate-800 border-slate-700',
                    )}
                  >
                    {getStatusIcon(status)}
                    <span className="text-slate-400">{tool}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Decisions */}
          {decisions && decisions.length > 0 && (
            <div className="space-y-1">
              {decisions.map((decision, idx) => (
                <div key={idx} className="text-[10px] text-slate-500">
                  <span className="text-amber-400">决策:</span>{' '}
                  <span className="text-slate-300">{decision.content}</span>
                  {decision.reason && (
                    <span className="text-slate-500">（{decision.reason}）</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Raw Thinking */}
          {thinking && (
            <div className="mt-2 p-2 rounded bg-slate-950/50 border border-white/5">
              <p className="text-[10px] text-slate-500 whitespace-pre-wrap">{thinking}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
