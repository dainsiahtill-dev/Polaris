/** ManusStyleStatusIndicator - Manus 风格实时状态指示器
 *
 * 特性：
 * - 即时状态反馈
 * - 实时显示 LLM 思考过程
 * - 工具调用进度
 * - 动画效果增强感知速度
 */
import { useState, useEffect, useRef } from 'react';
import {
  Brain,
  Loader2,
  Zap,
  CheckCircle2,
  AlertCircle,
  Terminal,
  ChevronRight,
  Cpu,
  Sparkles,
  Activity,
} from 'lucide-react';
import { cn } from '@/app/components/ui/utils';

export type StatusPhase = 
  | 'idle'
  | 'thinking'
  | 'executing'
  | 'tool_running'
  | 'completed'
  | 'error';

interface ManusStyleStatusIndicatorProps {
  phase: StatusPhase;
  message?: string;
  thinking?: string;
  toolName?: string;
  progress?: number;
  isVisible?: boolean;
  theme?: 'indigo' | 'amber' | 'cyan' | 'emerald';
}

const PHASE_CONFIG: Record<StatusPhase, {
  icon: React.ReactNode;
  label: string;
  color: string;
  bgColor: string;
  borderColor: string;
  animation: string;
}> = {
  idle: {
    icon: <Sparkles className="w-4 h-4" />,
    label: '就绪',
    color: 'text-slate-400',
    bgColor: 'bg-slate-500/10',
    borderColor: 'border-slate-500/20',
    animation: '',
  },
  thinking: {
    icon: <Brain className="w-4 h-4" />,
    label: '思考中',
    color: 'text-purple-400',
    bgColor: 'bg-purple-500/10',
    borderColor: 'border-purple-500/30',
    animation: 'animate-pulse',
  },
  executing: {
    icon: <Zap className="w-4 h-4" />,
    label: '执行中',
    color: 'text-amber-400',
    bgColor: 'bg-amber-500/10',
    borderColor: 'border-amber-500/30',
    animation: 'animate-pulse',
  },
  tool_running: {
    icon: <Terminal className="w-4 h-4" />,
    label: '工具运行',
    color: 'text-cyan-400',
    bgColor: 'bg-cyan-500/10',
    borderColor: 'border-cyan-500/30',
    animation: 'animate-pulse',
  },
  completed: {
    icon: <CheckCircle2 className="w-4 h-4" />,
    label: '已完成',
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-500/10',
    borderColor: 'border-emerald-500/30',
    animation: '',
  },
  error: {
    icon: <AlertCircle className="w-4 h-4" />,
    label: '出错',
    color: 'text-red-400',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
    animation: '',
  },
};

const THEME_COLORS = {
  indigo: {
    primary: 'indigo-400',
    gradient: 'from-indigo-500/20 to-purple-500/20',
    glow: 'shadow-indigo-500/20',
  },
  amber: {
    primary: 'amber-400',
    gradient: 'from-amber-500/20 to-orange-500/20',
    glow: 'shadow-amber-500/20',
  },
  cyan: {
    primary: 'cyan-400',
    gradient: 'from-cyan-500/20 to-blue-500/20',
    glow: 'shadow-cyan-500/20',
  },
  emerald: {
    primary: 'emerald-400',
    gradient: 'from-emerald-500/20 to-teal-500/20',
    glow: 'shadow-emerald-500/20',
  },
};

export function ManusStyleStatusIndicator({
  phase,
  message,
  thinking,
  toolName,
  progress,
  isVisible = true,
  theme = 'indigo',
}: ManusStyleStatusIndicatorProps) {
  const [displayThinking, setDisplayThinking] = useState('');
  const [showThinking, setShowThinking] = useState(true);
  const thinkingRef = useRef<HTMLDivElement>(null);

  const config = PHASE_CONFIG[phase];
  const themeConfig = THEME_COLORS[theme];

  // 打字机效果显示思考内容
  useEffect(() => {
    if (thinking) {
      setDisplayThinking('');
      let index = 0;
      const timer = setInterval(() => {
        if (index < thinking.length) {
          setDisplayThinking(thinking.slice(0, index + 1));
          index++;
        } else {
          clearInterval(timer);
        }
      }, 30);
      return () => clearInterval(timer);
    }
  }, [thinking]);

  // 自动滚动到底部
  useEffect(() => {
    if (thinkingRef.current) {
      thinkingRef.current.scrollTop = thinkingRef.current.scrollHeight;
    }
  }, [displayThinking]);

  if (!isVisible || phase === 'idle') {
    return null;
  }

  return (
    <div className={cn(
      'rounded-lg border backdrop-blur-sm transition-all duration-300',
      config.bgColor,
      config.borderColor,
      'overflow-hidden'
    )}>
      {/* Header - 状态行 */}
      <div className={cn(
        'flex items-center justify-between px-3 py-2',
        'bg-gradient-to-r ' + themeConfig.gradient
      )}>
        <div className="flex items-center gap-2">
          <div className={cn('animate-spin-slow', phase === 'thinking' && 'animate-spin')}>
            {config.icon}
          </div>
          <span className={cn('text-sm font-medium', config.color)}>
            {config.label}
          </span>
          {message && (
            <>
              <ChevronRight className="w-3 h-3 text-slate-500" />
              <span className="text-xs text-slate-400 truncate max-w-[200px]">
                {message}
              </span>
            </>
          )}
        </div>

        {/* 进度条（如果有） */}
        {progress !== undefined && (
          <div className="flex items-center gap-2">
            <div className="w-24 h-1.5 bg-slate-700 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${progress}%`,
                  backgroundColor: themeConfig.primary === 'indigo-400' ? '#818cf8' :
                                themeConfig.primary === 'amber-400' ? '#fbbf24' :
                                themeConfig.primary === 'cyan-400' ? '#22d3ee' :
                                themeConfig.primary === 'emerald-400' ? '#34d399' :
                                themeConfig.primary === 'purple-400' ? '#a78bfa' :
                                themeConfig.primary === 'rose-400' ? '#fb7185' : '#94a3b8',
                }}
              />
            </div>
            <span className="text-[10px] text-slate-500">{progress}%</span>
          </div>
        )}
      </div>

      {/* 思考过程（仅在 thinking 阶段显示） */}
      {(phase === 'thinking' || phase === 'tool_running') && displayThinking && (
        <div className="border-t border-white/5">
          <button
            onClick={() => setShowThinking(!showThinking)}
            className="w-full flex items-center justify-between px-3 py-1.5 text-[10px] text-slate-500 hover:text-slate-400 transition-colors"
          >
            <span className="flex items-center gap-1">
              <Brain className="w-3 h-3" />
              思考过程
            </span>
            <span>{showThinking ? '▼' : '▶'}</span>
          </button>
          
          {showThinking && (
            <div 
              ref={thinkingRef}
              className="px-3 pb-3 max-h-32 overflow-auto"
            >
              <pre className="text-[11px] text-slate-400 font-mono whitespace-pre-wrap">
                {displayThinking}
                <span className="animate-pulse">▋</span>
              </pre>
            </div>
          )}
        </div>
      )}

      {/* 工具运行信息 */}
      {phase === 'tool_running' && toolName && (
        <div className="border-t border-white/5 px-3 py-2">
          <div className="flex items-center gap-2">
            <Terminal className="w-3 h-3 text-cyan-400" />
            <span className="text-xs text-cyan-300">正在执行: {toolName}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// 简化版 - 用于嵌入在其他组件中
export function MiniStatusBadge({
  phase,
  theme = 'indigo',
}: {
  phase: StatusPhase;
  theme?: 'indigo' | 'amber' | 'cyan' | 'emerald';
}) {
  const config = PHASE_CONFIG[phase];
  const themeConfig = THEME_COLORS[theme];

  return (
    <div className={cn(
      'inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-medium border',
      config.bgColor,
      config.borderColor,
      config.color,
      config.animation
    )}>
      {phase === 'thinking' && <Loader2 className="w-3 h-3 animate-spin" />}
      {phase === 'executing' && <Zap className="w-3 h-3" />}
      {phase === 'tool_running' && <Cpu className="w-3 h-3" />}
      {phase === 'completed' && <CheckCircle2 className="w-3 h-3" />}
      {phase === 'error' && <AlertCircle className="w-3 h-3" />}
      {phase === 'idle' && <Activity className="w-3 h-3" />}
      {config.label}
    </div>
  );
}
