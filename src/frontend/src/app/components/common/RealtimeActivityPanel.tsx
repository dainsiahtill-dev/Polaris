/** RealtimeActivityPanel - 角色实时活动面板
 *
 * 统一承载 PM、Chief Engineer 和 Director 的流式思考、工具调用、
 * 运行日志与文件活动，保持角色色仅用于状态识别。
 */
import { useState, useMemo, useEffect, useRef } from 'react';
import {
  Brain,
  Terminal,
  FileCode,
  Activity,
  ChevronRight,
  ChevronDown,
  Clock,
  Zap,
  Wrench,
  Play,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Search,
  Sparkles,
  Cpu,
  GitBranch,
  TerminalSquare,
  Layers,
  ScrollText,
} from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { filterExecutionActivityLogs } from '@/app/utils/appRuntime';
import type { LogEntry } from '@/types/log';

// 日志级别颜色
const LOG_LEVEL_COLORS = {
  thinking: { bg: 'bg-purple-500/20', border: 'border-purple-500/30', text: 'text-purple-400', icon: 'text-purple-400' },
  info: { bg: 'bg-blue-500/20', border: 'border-blue-500/30', text: 'text-blue-400', icon: 'text-blue-400' },
  success: { bg: 'bg-emerald-500/20', border: 'border-emerald-500/30', text: 'text-emerald-400', icon: 'text-emerald-400' },
  warning: { bg: 'bg-amber-500/20', border: 'border-amber-500/30', text: 'text-amber-400', icon: 'text-amber-400' },
  error: { bg: 'bg-red-500/20', border: 'border-red-500/30', text: 'text-red-400', icon: 'text-red-400' },
  tool: { bg: 'bg-cyan-500/20', border: 'border-cyan-500/30', text: 'text-cyan-400', icon: 'text-cyan-400' },
  exec: { bg: 'bg-orange-500/20', border: 'border-orange-500/30', text: 'text-orange-400', icon: 'text-orange-400' },
};

interface RealtimeActivityPanelProps {
  executionLogs?: LogEntry[];
  llmStreamEvents?: LogEntry[];
  processStreamEvents?: LogEntry[];
  currentPhase?: string;
  isRunning?: boolean;
  role?: RealtimeActivityRole;
}

type ActivityView = 'thinking' | 'tools' | 'logs' | 'files';
type RealtimeActivityRole = 'pm' | 'chief_engineer' | 'director';
type ActivityViewTone = 'purple' | 'cyan' | 'blue' | 'emerald';

const ROLE_ACTIVITY_THEMES: Record<RealtimeActivityRole, {
  label: string;
  primaryColor: string;
  runningDot: string;
  border: string;
  bg: string;
  tag: string;
}> = {
  pm: {
    label: 'PM',
    primaryColor: 'text-amber-100',
    runningDot: 'bg-amber-400',
    border: 'border-amber-500/30',
    bg: 'bg-amber-500/5',
    tag: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
  },
  chief_engineer: {
    label: 'Chief Engineer',
    primaryColor: 'text-cyan-100',
    runningDot: 'bg-cyan-400',
    border: 'border-cyan-500/30',
    bg: 'bg-cyan-500/5',
    tag: 'bg-cyan-500/10 text-cyan-300 border-cyan-500/20',
  },
  director: {
    label: 'Director',
    primaryColor: 'text-indigo-100',
    runningDot: 'bg-indigo-400',
    border: 'border-indigo-500/30',
    bg: 'bg-indigo-500/5',
    tag: 'bg-indigo-500/10 text-indigo-300 border-indigo-500/20',
  },
};

const VIEW_TAB_TONES: Record<ActivityViewTone, string> = {
  purple: 'bg-purple-500/20 text-purple-300 border border-purple-500/30',
  cyan: 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30',
  blue: 'bg-blue-500/20 text-blue-300 border border-blue-500/30',
  emerald: 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30',
};

function streamEventToken(log: LogEntry): string {
  const meta = log.meta && typeof log.meta === 'object' ? (log.meta as Record<string, unknown>) : null;
  return String(meta?.streamEvent || '').trim().toLowerCase();
}

function isThinkingStreamEvent(token: string): boolean {
  return token === 'thinking_chunk' || token === 'content_chunk';
}

function isToolStreamEvent(token: string): boolean {
  return token === 'tool_call' || token === 'tool_result';
}

function activityLogMatchesView(log: LogEntry, view: ActivityView): boolean {
  if (view === 'files') return true;
  const token = streamEventToken(log);
  if (view === 'thinking') {
    return isThinkingStreamEvent(token) || log.level === 'thinking';
  }
  if (view === 'tools') {
    return isToolStreamEvent(token) || log.level === 'tool' || log.level === 'exec';
  }
  if (view === 'logs') {
    if (isThinkingStreamEvent(token) || isToolStreamEvent(token)) return false;
    return log.level === 'info' || log.level === 'warning';
  }
  return false;
}

function filterLogsForView(logs: LogEntry[], view: ActivityView): LogEntry[] {
  if (view === 'files') return logs;
  return logs.filter((log) => activityLogMatchesView(log, view));
}

function firstNonEmptyActivityView(logs: LogEntry[]): ActivityView | null {
  for (const view of ['logs', 'tools', 'thinking'] as const) {
    if (logs.some((log) => activityLogMatchesView(log, view))) {
      return view;
    }
  }
  return logs.length > 0 ? 'files' : null;
}

// 状态描述映射
const PHASE_DESCRIPTIONS: Record<string, { text: string; icon: React.ReactNode; color: string }> = {
  'idle': { text: '等待指令', icon: <Clock className="w-4 h-4" />, color: 'text-slate-400' },
  'planning': { text: '规划中...', icon: <Brain className="w-4 h-4" />, color: 'text-purple-400' },
  'analyzing': { text: '分析中...', icon: <Search className="w-4 h-4" />, color: 'text-blue-400' },
  'executing': { text: '执行中...', icon: <Zap className="w-4 h-4" />, color: 'text-amber-400' },
  'llm_calling': { text: '调用 LLM...', icon: <Cpu className="w-4 h-4" />, color: 'text-cyan-400' },
  'tool_running': { text: '工具执行中...', icon: <Wrench className="w-4 h-4" />, color: 'text-orange-400' },
  'completed': { text: '已完成', icon: <CheckCircle2 className="w-4 h-4" />, color: 'text-emerald-400' },
  'error': { text: '出错', icon: <AlertCircle className="w-4 h-4" />, color: 'text-red-400' },
};

export function RealtimeActivityPanel({
  executionLogs = [],
  llmStreamEvents = [],
  processStreamEvents = [],
  currentPhase = 'idle',
  isRunning = false,
  role = 'pm',
}: RealtimeActivityPanelProps) {
  const [activeView, setActiveView] = useState<ActivityView>('thinking');
  const [manualViewSelected, setManualViewSelected] = useState(false);
  const [expandedLogs, setExpandedLogs] = useState<Set<string>>(new Set());
  const logsEndRef = useRef<HTMLDivElement>(null);

  // 合并所有日志
  const allLogs = useMemo(() => {
    const processExecutionLogs = filterExecutionActivityLogs(processStreamEvents);
    const logs = [
      ...llmStreamEvents,
      ...executionLogs.map(l => ({ ...l, source: l.source || 'EXEC' })),
      ...processExecutionLogs.map(l => ({ ...l, source: 'PROC' })),
    ];
    return logs.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  }, [executionLogs, llmStreamEvents, processStreamEvents]);

  // 自动滚动到底部
  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [allLogs.length]);

  // 过滤日志
  const filteredLogs = useMemo(() => {
    return filterLogsForView(allLogs, activeView);
  }, [allLogs, activeView]);

  useEffect(() => {
    if (manualViewSelected || allLogs.length === 0 || filteredLogs.length > 0) return;
    const nextView = firstNonEmptyActivityView(allLogs);
    if (nextView && nextView !== activeView) {
      setActiveView(nextView);
    }
  }, [activeView, allLogs, filteredLogs.length, manualViewSelected]);

  const selectActivityView = (view: ActivityView) => {
    setManualViewSelected(true);
    setActiveView(view);
  };

  // 获取当前状态描述
  const currentStatus = PHASE_DESCRIPTIONS[currentPhase] || PHASE_DESCRIPTIONS['idle'];

  // 角色主题色
  const theme = ROLE_ACTIVITY_THEMES[role];

  const toggleLogExpand = (id: string) => {
    setExpandedLogs(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <div data-testid="realtime-activity-panel" className="h-full flex flex-col bg-slate-950">
      {/* Header - 状态指示器 */}
      <div className={cn(
        'flex h-16 items-center justify-between gap-3 border-b px-4',
        isRunning ? theme.border + ' ' + theme.bg : 'border-white/10'
      )}>
        <div className="flex min-w-0 items-center gap-3">
          {/* 脉冲动画状态灯 */}
          <div className="relative">
            <div className={cn(
              'w-3 h-3 rounded-full',
              isRunning ? `${theme.runningDot} animate-pulse` : 'bg-slate-500'
            )} />
            {isRunning && (
              <div className={cn(
                'absolute inset-0 w-3 h-3 rounded-full',
                `${theme.runningDot} animate-ping opacity-75`
              )} />
            )}
          </div>

          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className={cn('truncate text-sm font-semibold', isRunning ? theme.primaryColor : 'text-slate-400')}>
                {currentStatus.text}
              </span>
              {isRunning && (
                <Loader2 className={cn('w-3.5 h-3.5 animate-spin', currentStatus.color)} />
              )}
            </div>
            <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-slate-500">
              <Activity className="h-3 w-3 shrink-0" />
              <span className="truncate">实时活动</span>
              <span className="text-slate-600">•</span>
              <span className="shrink-0">{allLogs.length} 条记录</span>
            </div>
          </div>
        </div>

        {/* 视图切换标签 */}
        <div className="flex shrink-0 items-center gap-1 rounded-lg border border-white/10 bg-white/5 p-1">
          <ViewTab
            icon={<Brain className="w-3.5 h-3.5" />}
            label="思考"
            active={activeView === 'thinking'}
            onClick={() => selectActivityView('thinking')}
            color="purple"
          />
          <ViewTab
            icon={<Wrench className="w-3.5 h-3.5" />}
            label="工具"
            active={activeView === 'tools'}
            onClick={() => selectActivityView('tools')}
            color="cyan"
          />
          <ViewTab
            icon={<ScrollText className="w-3.5 h-3.5" />}
            label="日志"
            active={activeView === 'logs'}
            onClick={() => selectActivityView('logs')}
            color="blue"
          />
          <ViewTab
            icon={<FileCode className="w-3.5 h-3.5" />}
            label="文件"
            active={activeView === 'files'}
            onClick={() => selectActivityView('files')}
            color="emerald"
          />
        </div>
      </div>

      {/* Content - 日志流 */}
      <div className="flex-1 overflow-hidden">
        <div className="h-full overflow-y-auto p-4 space-y-2 custom-scrollbar">
          {filteredLogs.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-slate-500">
              <Sparkles className="w-8 h-8 mb-2 opacity-50" />
              <p className="text-sm">暂无{activeView === 'thinking' ? '思考' : activeView === 'tools' ? '工具' : activeView === 'logs' ? '日志' : '文件'}记录</p>
            </div>
          )}

          {filteredLogs.map((log, index) => (
            <LogItem
              key={`${log.source || log.level || 'log'}-${log.id || 'no-id'}-${index}`}
              log={log}
              isExpanded={expandedLogs.has(log.id)}
              onToggle={() => toggleLogExpand(log.id)}
              role={role}
            />
          ))}
          <div ref={logsEndRef} />
        </div>
      </div>

      {/* Footer - 控制条 */}
      <div className="h-12 flex items-center justify-between px-4 border-t border-white/10 bg-white/5">
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <TerminalSquare className="w-3.5 h-3.5" />
          <span>{theme.label} 监控</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <Layers className="w-3.5 h-3.5" />
          <span>{filteredLogs.length} / {allLogs.length}</span>
        </div>
      </div>
    </div>
  );
}

interface ViewTabProps {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  color: ActivityViewTone;
}

function ViewTab({ icon, label, active, onClick, color }: ViewTabProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`查看${label}记录`}
      title={`查看${label}记录`}
      className={cn(
        'flex h-7 w-7 shrink-0 items-center justify-center rounded text-xs font-medium transition-colors',
        active
          ? VIEW_TAB_TONES[color]
          : 'text-slate-500 hover:text-slate-300'
      )}
    >
      {icon}
      <span className="sr-only">{label}</span>
    </button>
  );
}

interface LogItemProps {
  log: LogEntry & { source?: string };
  isExpanded: boolean;
  onToggle: () => void;
  role: RealtimeActivityRole;
}

function LogItem({ log, isExpanded, onToggle, role }: LogItemProps) {
  const level = log.level || 'info';
  const streamToken = streamEventToken(log);
  const colors = LOG_LEVEL_COLORS[level] || LOG_LEVEL_COLORS.info;
  const time = new Date(log.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const eventBadgeLabel =
    streamToken === 'thinking_chunk'
      ? '思考流'
      : streamToken === 'content_chunk'
      ? '输出流'
      : streamToken === 'tool_call'
      ? '工具调用'
      : streamToken === 'tool_result'
      ? '工具结果'
      : '';

  return (
    <div className={cn(
      'rounded-lg border backdrop-blur-sm transition-all',
      colors.bg,
      colors.border,
      isExpanded ? 'shadow-lg' : 'hover:shadow-md'
    )}>
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-start gap-3 p-3 text-left"
      >
        <div className={cn('mt-0.5', colors.icon)}>
          {streamToken === 'thinking_chunk' && <Brain className="w-4 h-4" />}
          {streamToken === 'content_chunk' && <Terminal className="w-4 h-4" />}
          {streamToken === 'tool_call' && <Wrench className="w-4 h-4" />}
          {streamToken === 'tool_result' && <CheckCircle2 className="w-4 h-4" />}
          {!streamToken && level === 'thinking' && <Brain className="w-4 h-4" />}
          {!streamToken && level === 'tool' && <Wrench className="w-4 h-4" />}
          {level === 'exec' && <Play className="w-4 h-4" />}
          {level === 'success' && <CheckCircle2 className="w-4 h-4" />}
          {level === 'warning' && <AlertCircle className="w-4 h-4" />}
          {level === 'error' && <AlertCircle className="w-4 h-4" />}
          {level === 'info' && <Activity className="w-4 h-4" />}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className={cn('text-xs font-semibold', colors.text)}>
                {log.source || level.toUpperCase()}
              </span>
              {eventBadgeLabel && (
                <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-200">
                  {eventBadgeLabel}
                </span>
              )}
              {log.title && (
                <span className="text-xs text-slate-400 truncate">
                  {log.title}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 text-[10px] text-slate-500">
              <Clock className="w-3 h-3" />
              <span>{time}</span>
              {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            </div>
          </div>

          <div className={cn(
            'mt-1 text-xs text-slate-200',
            !isExpanded && 'line-clamp-2'
          )}>
            {log.message}
          </div>

          {log.tags && log.tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {log.tags.map((tag, i) => (
                <span
                  key={i}
                  className={cn('px-1.5 py-0.5 text-[10px] rounded border', ROLE_ACTIVITY_THEMES[role].tag)}
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      </button>

      {isExpanded && log.details && (
        <div className="px-4 pb-3">
          <div className="text-xs text-slate-400 bg-black/20 rounded p-2 font-mono whitespace-pre-wrap">
            {log.details}
          </div>
        </div>
      )}
    </div>
  );
}
