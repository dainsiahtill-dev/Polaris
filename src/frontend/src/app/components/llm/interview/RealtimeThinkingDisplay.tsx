import { useEffect, useRef, useState } from 'react';
import { Brain, MessageSquare, Terminal, Trash2 } from 'lucide-react';
import type { RealtimeThinkingEvent } from './useInterviewStream';

interface RealtimeThinkingDisplayProps {
  events: RealtimeThinkingEvent[];
  enabled?: boolean;
  isStreaming?: boolean;
  onClear?: () => void;
  className?: string;
}

const KIND_STYLES: Record<
  RealtimeThinkingEvent['kind'],
  { label: string; badge: string; icon: typeof Brain; border: string; bg: string }
> = {
  reasoning: {
    label: 'Reasoning',
    badge: 'bg-amber-500/20 text-amber-200 border-amber-500/30',
    icon: Brain,
    border: 'border-amber-500/20',
    bg: 'bg-amber-500/5',
  },
  command_execution: {
    label: 'Command',
    badge: 'bg-cyan-500/20 text-cyan-200 border-cyan-500/30',
    icon: Terminal,
    border: 'border-cyan-500/20',
    bg: 'bg-cyan-500/5',
  },
  agent_message: {
    label: 'Agent Message',
    badge: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
    icon: MessageSquare,
    border: 'border-emerald-500/20',
    bg: 'bg-emerald-500/5',
  },
};

const STATUS_LABELS: Record<string, string> = {
  in_progress: '执行中',
  completed: '已完成',
  failed: '失败',
};

const extractTaggedBlock = (text: string | undefined, tags: string[]): string | undefined => {
  if (!text) return undefined;
  for (const tag of tags) {
    const regex = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, 'i');
    const match = text.match(regex);
    if (match && match[1]) {
      return match[1].trim();
    }
  }
  return undefined;
};

// Strip XML tags from text for display
const stripXmlTags = (text: string | undefined): string => {
  if (!text) return '';
  return text.replace(/<[^>]+>/g, '').trim();
};

// Remove system prompt leakage (common patterns)
const cleanModelOutput = (text: string | undefined): string => {
  if (!text) return '';
  const patterns = [
    /The user is asking me to[\s\S]*?this approach demonstrates these competencies[\s\S]*?/gi,
    /According to my instructions:[\s\S]*?-\s*I must answer RIGHT NOW[\s\S]*?-\s*I cannot ask for clarification[\s\S]*?/gi,
    /ROLE: You are a job CANDIDATE[\s\S]*?/gi,
    /IMMEDIATE ACTION REQUIRED:[\s\S]*?/gi,
    /FORBIDDEN RESPONSES[\s\S]*?/gi,
  ];
  let cleaned = text;
  patterns.forEach(pattern => {
    cleaned = cleaned.replace(pattern, '');
  });
  return cleaned.trim();
};

export function RealtimeThinkingDisplay({
  events,
  enabled = false,
  isStreaming = false,
  onClear,
  className,
}: RealtimeThinkingDisplayProps) {
  const outputRef = useRef<HTMLDivElement | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    const el = outputRef.current;
    if (!el || !autoScroll) return;
    el.scrollTop = el.scrollHeight;
  }, [events, autoScroll]);

  useEffect(() => {
    const el = outputRef.current;
    if (!el) return;
    const onScroll = () => {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      setAutoScroll(nearBottom);
    };
    el.addEventListener('scroll', onScroll);
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const emptyText = enabled
    ? '等待思考过程输出...'
    : '开启 Debug 模式 + 实时流式 后可查看思考过程。';

  return (
    <div className={`rounded-lg border border-white/10 bg-black/40 p-3 space-y-2 ${className || ''}`}>
      <div className="flex items-center justify-between text-[10px] text-text-dim">
        <div className="flex items-center gap-2">
          <span className="uppercase tracking-wide">实时思考过程</span>
          <span className="text-[9px]">{isStreaming ? 'streaming...' : autoScroll ? '自动滚动' : '已暂停滚动'}</span>
        </div>
        {onClear ? (
          <button
            type="button"
            onClick={onClear}
            className="flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[9px] hover:border-white/30"
          >
            <Trash2 className="size-3" />
            清空
          </button>
        ) : null}
      </div>

      <div
        ref={outputRef}
        className="max-h-56 overflow-y-auto space-y-2 pr-1"
      >
        {events.length === 0 ? (
          <div className="text-[11px] text-text-dim italic">{emptyText}</div>
        ) : (
          events.map((event) => {
            const styles = KIND_STYLES[event.kind];
            const Icon = styles.icon;
            const time = new Date(event.timestamp).toLocaleTimeString();
            const statusLabel = event.status ? STATUS_LABELS[event.status] || event.status : '';
            const derivedThinking =
              event.thinking || extractTaggedBlock(event.raw, ['thinking', 'think', 'reasoning', 'analysis']);
            const derivedAnswer =
              event.answer || extractTaggedBlock(event.raw, ['answer', 'final', 'response']);
            return (
              <div
                key={`${event.id}-${event.timestamp}`}
                className={`rounded-md border ${styles.border} ${styles.bg} p-3 text-[11px] space-y-2`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 text-xs font-semibold text-text-main">
                    <Icon className="size-3" />
                    <span>{styles.label}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {statusLabel ? (
                      <span className={`text-[9px] uppercase tracking-wide px-2 py-0.5 rounded border ${styles.badge}`}>
                        {statusLabel}
                      </span>
                    ) : null}
                    <span className="text-[9px] text-text-dim">{time}</span>
                  </div>
                </div>

                {event.kind === 'reasoning' ? (
                  <div className="space-y-2">
                    {derivedThinking ? (
                      <div className="rounded border border-amber-500/20 bg-amber-500/5 p-2">
                        <div className="text-[9px] uppercase tracking-wide text-amber-300 mb-1">思考链</div>
                        <div className="text-text-main whitespace-pre-wrap">{cleanModelOutput(derivedThinking)}</div>
                      </div>
                    ) : null}
                    {derivedAnswer ? (
                      <div className="rounded border border-emerald-500/20 bg-emerald-500/5 p-2">
                        <div className="text-[9px] uppercase tracking-wide text-emerald-300 mb-1">作答</div>
                        <div className="text-text-main whitespace-pre-wrap">{derivedAnswer}</div>
                      </div>
                    ) : event.text ? (
                      <div className="text-text-main whitespace-pre-wrap">{cleanModelOutput(stripXmlTags(event.text))}</div>
                    ) : null}
                  </div>
                ) : null}

                {event.kind === 'command_execution' ? (
                  <div className="space-y-2">
                    {event.command ? (
                      <pre className="text-[10px] text-cyan-100 bg-black/40 rounded p-2 border border-white/10 whitespace-pre-wrap font-mono">
                        {event.command}
                      </pre>
                    ) : null}
                    {event.output ? (
                      <pre className="text-[10px] text-text-main bg-black/30 rounded p-2 border border-white/5 whitespace-pre-wrap font-mono max-h-32 overflow-auto">
                        {event.output}
                      </pre>
                    ) : null}
                    {typeof event.exitCode === 'number' ? (
                      <div className="text-[10px] text-text-dim">Exit code: {event.exitCode}</div>
                    ) : null}
                  </div>
                ) : null}

                {event.kind === 'agent_message' ? (
                  <div className="space-y-2">
                    {derivedThinking ? (
                      <div className="rounded border border-amber-500/20 bg-amber-500/5 p-2">
                        <div className="text-[9px] uppercase tracking-wide text-amber-300 mb-1">思考链</div>
                        <div className="text-text-main whitespace-pre-wrap">{cleanModelOutput(derivedThinking)}</div>
                      </div>
                    ) : null}
                    {derivedAnswer ? (
                      <div className="rounded border border-emerald-500/20 bg-emerald-500/5 p-2">
                        <div className="text-[9px] uppercase tracking-wide text-emerald-300 mb-1">作答</div>
                        <div className="text-text-main whitespace-pre-wrap">{derivedAnswer}</div>
                      </div>
                    ) : event.raw ? (
                      <div className="text-text-main whitespace-pre-wrap">{cleanModelOutput(stripXmlTags(event.raw))}</div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
