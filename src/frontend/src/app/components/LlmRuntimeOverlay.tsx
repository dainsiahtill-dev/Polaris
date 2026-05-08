import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Bot,
  ChevronDown,
  ChevronUp,
  Cpu,
  FileCode,
  GitBranch,
  Loader2,
  Radar,
  Sparkles,
  Terminal,
  Wifi,
  WifiOff,
  Zap,
} from 'lucide-react';
import { StatusBadge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import { filterExecutionActivityLogs } from '@/app/utils/appRuntime';
import type { LogEntry, QualityGateData } from '@/app/components/pm';
import type { FileEditEvent } from '@/app/hooks/useRuntime';

type ActiveView = 'main' | 'pm' | 'chief_engineer' | 'director' | 'factory' | 'agi' | 'diagnostics';

function viewLabel(activeView: ActiveView): string {
  if (activeView === 'agi') {
    return 'AGI';
  }
  if (activeView === 'chief_engineer') {
    return 'CE';
  }
  if (activeView === 'diagnostics') {
    return 'DIAG';
  }
  return activeView.toUpperCase();
}

interface LlmRuntimeOverlayProps {
  activeView: ActiveView;
  websocketLive: boolean;
  websocketReconnecting: boolean;
  websocketAttemptCount: number;
  pmRunning: boolean;
  directorRunning: boolean;
  llmState: string;
  llmBlockedRoles: string[];
  llmRequiredRoles: string[];
  llmLastUpdated?: string | null;
  currentPhase: string;
  qualityGate: QualityGateData | null;
  executionLogs: LogEntry[];
  llmStreamEvents: LogEntry[];
  processStreamEvents: LogEntry[];
  fileEditEvents?: FileEditEvent[];
}

const PHASE_LABELS: Record<string, string> = {
  idle: '空闲',
  agents: 'AGENTS 审核',
  planning: 'Planning',
  analyzing: '任务分析',
  executing: 'Executing',
  llm_calling: 'LLM 推理',
  tool_running: '工具执行',
  verification: '验证中',
  chief_engineer: 'Chief Engineer Design',
  director: 'Director 执行',
  qa: 'QA 验收',
  completed: '已完成',
  complete: '已完成',
  failed: '执行失败',
  error: '执行失败',
};

function normalizeStateToken(value: string): 'ready' | 'blocked' | 'unknown' {
  const token = String(value || '').trim().toLowerCase();
  if (token === 'ready') return 'ready';
  if (token === 'blocked') return 'blocked';
  return 'unknown';
}

function isActiveRuntimePhase(value: string): boolean {
  const token = String(value || '').trim().toLowerCase();
  return Boolean(token && !['idle', 'unknown', 'none'].includes(token));
}

function toRelativeTime(value?: string | null): string {
  if (!value) return '未更新';
  const epoch = Date.parse(value);
  if (!Number.isFinite(epoch)) return '未更新';
  const seconds = Math.max(0, Math.floor((Date.now() - epoch) / 1000));
  if (seconds < 60) return `${seconds}s 前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m 前`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h 前`;
}

function toEpoch(value?: string | null): number {
  const parsed = Date.parse(String(value || '').trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function isLowSignalLog(log: LogEntry): boolean {
  const text = `${log.source} ${log.message} ${log.details || ''}`.toLowerCase();
  if (isStructuredRuntimeFragment(log)) return true;
  if (text.includes('initialized docs via onboarding wizard')) return true;
  if (/\[history\]\s*archived round/.test(text)) return true;
  if (/\[runtime\]\s*workspace=/.test(text)) return true;
  return false;
}

function isStructuredRuntimeFragment(log: LogEntry): boolean {
  const source = String(log.source || '').trim().toLowerCase();
  if (!/(engine|runtime|system)/.test(source)) return false;

  const message = String(log.message || '').trim();
  if (!message) return true;
  if (/^[{}\[\],]+$/.test(message)) return true;
  if (/^["']?[}\]],?$/.test(message)) return true;
  if (/^:\d{2}(?:\.\d+)?z["']?,?$/i.test(message)) return true;

  return /^["']?[a-z0-9_.-]+["']?\s*:\s*(?:$|["'{\[\]\d]|true\b|false\b|null\b)/i.test(message);
}

function isStructuredRuntimeText(value?: string): boolean {
  return isStructuredRuntimeFragment({
    id: 'inline',
    timestamp: new Date(0).toISOString(),
    source: 'Engine',
    level: 'info',
    message: value || '',
  });
}

function logPriority(log: LogEntry): number {
  const streamEvent = getStreamEvent(log);
  const text = `${log.source} ${log.message} ${log.details || ''}`.toLowerCase();
  let score = 0;
  if (/llm|invoke|tool|质量|qa|director|pm/.test(text)) score += 3;
  if (streamEvent === 'tool_call' || streamEvent === 'tool_result') score += 4;
  if (streamEvent === 'thinking_chunk' || streamEvent === 'content_chunk') score += 2;
  if (log.level === 'thinking') score += 3;
  if (log.level === 'warning') score += 2;
  if (log.level === 'error') score += 4;
  if (isLowSignalLog(log)) score -= 5;
  return score;
}

function getStreamEvent(log: LogEntry): string {
  return String((log.meta as Record<string, unknown> | undefined)?.streamEvent || '').toLowerCase();
}

function streamEventLabel(log: LogEntry): string {
  const streamEvent = getStreamEvent(log);
  if (streamEvent === 'thinking_chunk') return '思';
  if (streamEvent === 'content_chunk') return '输出';
  if (streamEvent === 'tool_call') return '工具';
  if (streamEvent === 'tool_result') return '结果';
  return '';
}

function streamEventIcon(log: LogEntry): React.ReactNode {
  const streamEvent = getStreamEvent(log);
  if (streamEvent === 'thinking_chunk') return <Zap className="size-3 text-amber-400" />;
  if (streamEvent === 'content_chunk') return <Bot className="size-3 text-cyan-400" />;
  if (streamEvent === 'tool_call') return <Terminal className="size-3 text-green-400" />;
  if (streamEvent === 'tool_result') return <GitBranch className="size-3 text-emerald-400" />;
  return <Cpu className="size-3 text-white/40" />;
}

function streamEventStyle(log: LogEntry): string {
  const streamEvent = getStreamEvent(log);
  if (streamEvent === 'thinking_chunk') return 'border-amber-400/30 bg-amber-500/10';
  if (streamEvent === 'content_chunk') return 'border-cyan-400/30 bg-cyan-500/10';
  if (streamEvent === 'tool_call') return 'border-green-400/30 bg-green-500/10';
  if (streamEvent === 'tool_result') return 'border-emerald-400/30 bg-emerald-500/10';
  return 'border-white/10 bg-white/[0.02]';
}

function isTypingStreamEvent(log: LogEntry): boolean {
  const streamEvent = getStreamEvent(log);
  return streamEvent === 'thinking_chunk' || streamEvent === 'content_chunk';
}

function TypingMessage({
  text,
  animate,
}: {
  text: string;
  animate: boolean;
}) {
  const [visibleChars, setVisibleChars] = useState<number>(() => text.length);

  useEffect(() => {
    if (!animate) {
      setVisibleChars(text.length);
      return;
    }
    setVisibleChars((current) => {
      // Reset animation when message shrinks (e.g. truncation/rotation), otherwise continue.
      if (text.length < current) return 0;
      return Math.min(current, text.length);
    });
  }, [animate, text]);

  useEffect(() => {
    if (!animate) return;
    if (visibleChars >= text.length) return;

    const timer = window.setInterval(() => {
      setVisibleChars((current) => {
        if (current >= text.length) return current;
        // Reveal in small batches for smoother but still "token-like" animation.
        return Math.min(text.length, current + 2);
      });
    }, 16);

    return () => {
      window.clearInterval(timer);
    };
  }, [animate, text, visibleChars]);

  const rendered = animate ? text.slice(0, visibleChars) : text;
  const showCursor = animate && visibleChars < text.length;

  return (
    <div className="text-[10px] text-white/70">
      <span>{rendered}</span>
      {showCursor && <span className="ml-[1px] inline-block animate-pulse text-cyan-300">▋</span>}
    </div>
  );
}

function pickHeadline(
  active: boolean,
  latestLog: LogEntry | null,
  currentPhase: string,
): string {
  if (latestLog) return latestLog.message;
  if (active) return `正在执行 ${PHASE_LABELS[currentPhase] || currentPhase || '流程'}...`;
  return '系统待命';
}

export function LlmRuntimeOverlay({
  activeView,
  websocketLive,
  websocketReconnecting,
  websocketAttemptCount,
  pmRunning,
  directorRunning,
  llmState,
  llmBlockedRoles,
  llmRequiredRoles,
  llmLastUpdated,
  currentPhase,
  qualityGate,
  executionLogs,
  llmStreamEvents,
  processStreamEvents,
  fileEditEvents = [],
}: LlmRuntimeOverlayProps) {
  const [expanded, setExpanded] = useState(false);
  const running = pmRunning || directorRunning;
  const llmStateToken = normalizeStateToken(llmState);
  const runtimeActive = running || isActiveRuntimePhase(currentPhase);
  const blockedRoleForView =
    (activeView === 'pm' && llmBlockedRoles.includes('pm')) ||
    (activeView === 'director' && llmBlockedRoles.includes('director')) ||
    (activeView === 'chief_engineer' && llmBlockedRoles.includes('chief_engineer')) ||
    (activeView === 'factory' && llmBlockedRoles.some((role) => ['pm', 'director', 'qa'].includes(role)));
  const isLlmBlocked = llmStateToken === 'blocked' && (runtimeActive || blockedRoleForView);
  const phaseLabel = (
    PHASE_LABELS[currentPhase] ||
    (pmRunning && !directorRunning ? 'PM Running' : '') ||
    (directorRunning ? 'Director 执行中' : '') ||
    currentPhase ||
    '等待中'
  );

  useEffect(() => {
    if (running || websocketReconnecting || isLlmBlocked) {
      setExpanded(true);
    }
  }, [running, websocketReconnecting, isLlmBlocked]);

  const recentSteps = useMemo(() => {
    const now = Date.now();
    const freshnessWindowMs = running ? 20 * 60 * 1000 : 24 * 60 * 60 * 1000;
    const processExecutionLogs = filterExecutionActivityLogs(processStreamEvents);

    const ordered = [...llmStreamEvents, ...processExecutionLogs, ...executionLogs]
      .filter((entry) => Boolean(String(entry.message || '').trim()))
      .sort((a, b) => toEpoch(a.timestamp) - toEpoch(b.timestamp));

    const fresh = ordered.filter((entry) => {
      const ts = toEpoch(entry.timestamp);
      return ts > 0 && now - ts <= freshnessWindowMs;
    });

    const candidates = fresh.length > 0 ? fresh : running ? [] : ordered.slice(-32);
    const filtered = candidates.filter((entry) => !isLowSignalLog(entry));
    const hasStructuredFragments = candidates.some(isStructuredRuntimeFragment);
    const pool = filtered.length > 0 || hasStructuredFragments ? filtered : candidates;

    const ranked = [...pool].sort((a, b) => {
      const tsDiff = toEpoch(b.timestamp) - toEpoch(a.timestamp);
      if (tsDiff !== 0) return tsDiff;
      return logPriority(b) - logPriority(a);
    });

    const deduped: LogEntry[] = [];
    const seen = new Set<string>();
    for (const entry of ranked) {
      const key = `${entry.source}|${entry.message}`;
      if (seen.has(key)) continue;
      seen.add(key);
      deduped.push(entry);
      if (deduped.length >= 6) break;
    }
    return deduped;
  }, [executionLogs, llmStreamEvents, processStreamEvents, running]);

  const latestFileEdit = useMemo(() => {
    return [...fileEditEvents]
      .filter((event) => Boolean(event.filePath))
      .sort((a, b) => toEpoch(b.timestamp) - toEpoch(a.timestamp))[0] || null;
  }, [fileEditEvents]);

  const latestStep = recentSteps[0] ?? null;
  const headline = pickHeadline(running, latestStep, currentPhase);
  const effectiveUpdateTime = latestStep?.timestamp || llmLastUpdated || null;
  const visibleRequiredRoles = running || isLlmBlocked ? llmRequiredRoles : [];
  const visibleBlockedRoles = isLlmBlocked ? llmBlockedRoles : [];

  const llmBadgeColor =
    llmStateToken === 'ready' ? 'success' : isLlmBlocked ? 'error' : running ? 'warning' : 'default';
  const llmBadgeLabel =
    llmStateToken === 'ready' ? 'LLM READY' : isLlmBlocked ? 'LLM BLOCKED' : running ? 'LLM WAIT' : 'LLM IDLE';
  const socketBadgeColor = websocketLive ? 'success' : websocketReconnecting ? 'warning' : 'error';

  return (
    <div data-testid="llm-runtime-overlay" className="pointer-events-none fixed bottom-16 right-3 z-40 w-[min(94vw,420px)] sm:bottom-6 sm:right-4 sm:w-[400px]">
      {/* Cyberpunk + Han/Tang fusion container */}
      <div className="pointer-events-auto rounded-2xl border border-amber-400/20 bg-gradient-to-br from-[#0a0f1a] via-[#0d1525] to-[#0a0f1a] shadow-[0_18px_40px_rgba(0,0,0,0.5),0_0_30px_rgba(200,160,60,0.05)] backdrop-blur-xl">
        {/* Glow accent line */}
        <div className="h-px w-full bg-gradient-to-r from-transparent via-amber-400/30 to-transparent" />

        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className="group flex w-full items-center gap-2 rounded-t-xl px-3 py-2.5 text-left transition-all hover:bg-amber-500/5"
        >
          <div className="relative flex items-center justify-center rounded-lg border border-amber-400/30 bg-amber-500/10 p-1.5 shadow-[0_0_12px_rgba(200,160,60,0.15)]">
            {(running || websocketReconnecting) ? (
              <Loader2 className="size-4 animate-spin text-amber-300" />
            ) : (
              <Activity className="size-4 text-amber-300" />
            )}
            {/* Pulse indicator */}
            {(running || websocketReconnecting) && (
              <span className="absolute -right-0.5 -top-0.5 flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-400" />
              </span>
            )}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold tracking-wider text-amber-200">LLM Runtime</span>
              <span className="rounded-full border border-amber-400/20 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-100/80">
                {viewLabel(activeView)}
              </span>
              {/* Role status badges */}
              {pmRunning && (
                <span className="rounded border border-green-500/30 bg-green-500/10 px-1 py-0.5 text-[8px] font-bold text-green-300 animate-pulse">
                  PM ACTIVE
                </span>
              )}
              {directorRunning && (
                <span className="rounded border border-cyan-500/30 bg-cyan-500/10 px-1 py-0.5 text-[8px] font-bold text-cyan-300 animate-pulse">
                  DIR ACTIVE
                </span>
              )}
            </div>
            <div className="truncate text-[11px] font-medium text-white/65">{headline}</div>
          </div>
          {expanded ? (
            <ChevronUp className="size-4 text-amber-400/70 transition-colors group-hover:text-amber-400" />
          ) : (
            <ChevronDown className="size-4 text-white/50 transition-colors group-hover:text-white/80" />
          )}
        </button>

        <div
          className={cn(
            'grid transition-all duration-300',
            expanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
          )}
        >
          <div className="overflow-hidden">
            <div className="border-t border-white/10 px-3 py-3">
              {/* Status badges row - Cyberpunk style */}
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <StatusBadge color={pmRunning ? 'success' : 'default'} variant="dot" pulse={pmRunning}>
                  <span className="font-mono text-[10px]">{pmRunning ? 'PM RUN' : 'PM IDLE'}</span>
                </StatusBadge>
                <StatusBadge color={directorRunning ? 'info' : 'default'} variant="dot" pulse={directorRunning}>
                  <span className="font-mono text-[10px]">{directorRunning ? 'DIR RUN' : 'DIR IDLE'}</span>
                </StatusBadge>
                <StatusBadge color={llmBadgeColor} variant="dot" pulse={llmStateToken === 'ready'}>
                  <span className="font-mono text-[10px]">{llmBadgeLabel}</span>
                </StatusBadge>
                <StatusBadge color={socketBadgeColor} variant="dot" pulse={websocketLive}>
                  <span className="font-mono text-[9px]">
                    {websocketLive ? 'WS LIVE' : websocketReconnecting ? 'WS RECONNECT' : 'WS OFFLINE'}
                  </span>
                </StatusBadge>
              </div>

              <div className="mb-3 rounded-xl border border-amber-400/15 bg-gradient-to-r from-amber-500/5 to-cyan-500/5 px-3 py-2.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Radar className="size-4 text-amber-400" />
                    <span className="text-xs font-bold tracking-wider text-amber-200">当前阶段</span>
                  </div>
                  <span className="rounded border border-amber-400/30 bg-amber-500/10 px-2 py-0.5 text-xs font-bold text-amber-100">
                    {phaseLabel}
                  </span>
                </div>
                <div className="mt-2 flex items-center justify-between text-[10px] text-white/50">
                  <span className="flex items-center gap-1">
                    <Bot className="size-3 text-cyan-400" />
                    LLM 更新
                  </span>
                  <span className="font-mono">{toRelativeTime(effectiveUpdateTime)}</span>
                </div>
                {qualityGate && (
                  <div className="mt-1.5 flex items-center justify-between text-[10px]">
                    <span className="flex items-center gap-1 text-white/60">
                      <Sparkles className="size-3 text-purple-400" />
                      质量门控
                    </span>
                    <span className={cn(
                      'font-mono font-bold',
                      qualityGate.passed ? 'text-emerald-300' : 'text-amber-300'
                    )}>
                      {qualityGate.score}/100
                    </span>
                  </div>
                )}
                {latestFileEdit && (
                  <div className="mt-1.5 flex items-center justify-between gap-2 text-[10px]" data-testid="llm-runtime-file-edit">
                    <span className="flex min-w-0 items-center gap-1 text-white/60">
                      <FileCode className="size-3 text-emerald-300" />
                      <span className="truncate">{latestFileEdit.filePath}</span>
                    </span>
                    <span className="shrink-0 font-mono text-emerald-300">
                      {latestFileEdit.operation}
                    </span>
                  </div>
                )}
              </div>

              {visibleRequiredRoles.length > 0 && (
                <div className="mb-2 flex items-center gap-2 text-[10px] text-white/50">
                  <Bot className="size-3.5 text-cyan-300" />
                  <span className="truncate">
                    required: {visibleRequiredRoles.join(', ')}
                  </span>
                </div>
              )}
              {visibleBlockedRoles.length > 0 && (
                <div className="mb-2 rounded-lg border border-red-500/30 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
                  blocked: {visibleBlockedRoles.join(', ')}
                </div>
              )}

              <div className="rounded-xl border border-white/10 bg-black/25 p-2">
                <div className="mb-2 flex items-center justify-between text-[11px] text-white/70">
                  <span className="flex items-center gap-1.5">
                    {websocketLive ? <Wifi className="size-3.5 text-emerald-300" /> : <WifiOff className="size-3.5 text-amber-300" />}
                    实时推理流
                  </span>
                  <span className="text-white/40">{recentSteps.length} events</span>
                </div>

                {/* Claude/Codex-style reasoning chain */}
                <div className="space-y-1.5">
                  {recentSteps.length === 0 && (
                    <div className="rounded-lg border border-white/5 bg-white/[0.02] px-2 py-1.5 text-[10px] text-white/35 italic">
                      等待 LLM 事件流...
                    </div>
                  )}
                  {recentSteps.map((step, idx) => {
                    const isLatest = idx === 0;
                    const isThinking = getStreamEvent(step) === 'thinking_chunk';
                    const isToolCall = getStreamEvent(step) === 'tool_call';
                    return (
                      <div
                        key={step.id}
                        className={cn(
                          'relative rounded-lg border px-2 py-1.5 transition-all',
                          streamEventStyle(step),
                          isLatest && 'ring-1 ring-cyan-400/30',
                        )}
                      >
                        {/* Timeline connector */}
                        {idx < recentSteps.length - 1 && (
                          <div className="absolute -bottom-3 left-3 top-1/2 h-4 w-px bg-gradient-to-b from-cyan-500/40 to-transparent" />
                        )}

                        <div className="mb-0.5 flex items-center justify-between gap-2">
                          <div className="flex min-w-0 items-center gap-1.5">
                            {streamEventIcon(step)}
                            <span className={cn(
                              'truncate text-[10px] font-medium',
                              isThinking ? 'text-amber-300' : isToolCall ? 'text-green-300' : 'text-cyan-100/90'
                            )}>
                              {step.source}
                            </span>
                            {streamEventLabel(step) && (
                              <span className={cn(
                                'shrink-0 rounded border px-1 py-0.5 text-[8px] font-bold tracking-wider',
                                getStreamEvent(step) === 'thinking_chunk' && 'border-amber-400/40 bg-amber-500/20 text-amber-200',
                                getStreamEvent(step) === 'content_chunk' && 'border-cyan-400/40 bg-cyan-500/20 text-cyan-200',
                                getStreamEvent(step) === 'tool_call' && 'border-green-400/40 bg-green-500/20 text-green-200',
                                getStreamEvent(step) === 'tool_result' && 'border-emerald-400/40 bg-emerald-500/20 text-emerald-200',
                              )}>
                                {streamEventLabel(step)}
                              </span>
                            )}
                          </div>
                          <span className="shrink-0 text-[9px] text-white/35">{toRelativeTime(step.timestamp)}</span>
                        </div>
                        <TypingMessage text={step.message} animate={isTypingStreamEvent(step)} />
                        {step.details && !isStructuredRuntimeText(step.details) && (
                          <div className="mt-0.5 font-mono text-[9px] text-white/45">{step.details}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Glow accent line */}
        <div className="h-px w-full bg-gradient-to-r from-transparent via-amber-400/30 to-transparent" />
      </div>
    </div>
  );
}
