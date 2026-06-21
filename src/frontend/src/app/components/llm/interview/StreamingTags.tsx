import { useCallback, useEffect, useRef, useState } from 'react';
import { Brain, MessageSquare, Loader2 } from 'lucide-react';
import type { StreamingTagEvent, StreamingTagEventType } from './useInterviewStream';

interface StreamingTagsProps {
  events: StreamingTagEvent[];
  isStreaming?: boolean;
  onClear?: () => void;
  className?: string;
  dense?: boolean;
}

interface StreamingContentState {
  thinking: string;
  answer: string;
  isThinkingActive: boolean;
  isAnswerActive: boolean;
  lastUpdate: number;
}

const TAG_EVENT_TYPES: StreamingTagEventType[] = [
  'thinking_start',
  'thinking_chunk',
  'thinking_end',
  'answer_start',
  'answer_chunk',
  'answer_end',
];

const isTagEvent = (event: unknown): event is StreamingTagEvent => {
  if (!event || typeof event !== 'object') return false;
  const e = event as { type?: unknown };
  return typeof e.type === 'string' && TAG_EVENT_TYPES.includes(e.type as StreamingTagEventType);
};

export function StreamingTags({
  events,
  isStreaming = false,
  onClear,
  className,
  dense = false,
}: StreamingTagsProps) {
  const outputRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [contentState, setContentState] = useState<StreamingContentState>({
    thinking: '',
    answer: '',
    isThinkingActive: false,
    isAnswerActive: false,
    lastUpdate: Date.now(),
  });

  useEffect(() => {
    let newThinking = '';
    let newAnswer = '';
    let isThinkingActive = false;
    let isAnswerActive = false;

    for (const event of events) {
      if (!isTagEvent(event)) continue;

      switch (event.type) {
        case 'thinking_start':
          isThinkingActive = true;
          newThinking = '';
          break;
        case 'thinking_chunk':
          if (event.data.content) {
            newThinking += event.data.content;
          }
          break;
        case 'thinking_end':
          isThinkingActive = false;
          break;
        case 'answer_start':
          isAnswerActive = true;
          newAnswer = '';
          break;
        case 'answer_chunk':
          if (event.data.content) {
            newAnswer += event.data.content;
          }
          break;
        case 'answer_end':
          isAnswerActive = false;
          break;
      }
    }

    setContentState((prev) => {
      if (
        prev.thinking === newThinking &&
        prev.answer === newAnswer &&
        prev.isThinkingActive === isThinkingActive &&
        prev.isAnswerActive === isAnswerActive
      ) {
        return prev;
      }
      return {
        thinking: newThinking,
        answer: newAnswer,
        isThinkingActive,
        isAnswerActive,
        lastUpdate: Date.now(),
      };
    });
  }, [events]);

  useEffect(() => {
    const el = outputRef.current;
    if (!el || !autoScroll) return;
    el.scrollTop = el.scrollHeight;
  }, [contentState.lastUpdate, autoScroll]);

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

  const formatTimestamp = (timestamp: string) => {
    try {
      return new Date(timestamp).toLocaleTimeString();
    } catch {
      return '--:--:--';
    }
  };

  const renderThinkingSection = () => {
    if (!contentState.thinking && !contentState.isThinkingActive) {
      return null;
    }

    return (
      <div className={`${dense ? 'mb-2 p-2' : 'mb-3 p-3'} rounded border border-amber-500/20 bg-amber-500/5`}>
        <div className={`${dense ? 'mb-1 text-[10px]' : 'mb-2 text-xs'} flex items-center gap-2 text-amber-200`}>
          <Brain className="h-3 w-3" />
          <span>思考链</span>
          {contentState.isThinkingActive && (
            <Loader2 className="h-3 w-3 animate-spin" />
          )}
        </div>
        <div className={`${dense ? 'text-[10px]' : 'text-xs'} whitespace-pre-wrap break-words leading-relaxed text-amber-100/80`}>
          {contentState.thinking}
          {contentState.isThinkingActive && (
            <span className="ml-1 inline-block h-3 w-0.5 bg-amber-400/70 align-middle" />
          )}
        </div>
      </div>
    );
  };

  const renderAnswerSection = () => {
    if (!contentState.answer && !contentState.isAnswerActive) {
      return null;
    }

    return (
      <div className={`${dense ? 'p-2' : 'p-3'} rounded border border-emerald-500/20 bg-emerald-500/5`}>
        <div className={`${dense ? 'mb-1 text-[10px]' : 'mb-2 text-xs'} flex items-center gap-2 text-emerald-200`}>
          <MessageSquare className="h-3 w-3" />
          <span>作答</span>
          {contentState.isAnswerActive && (
            <Loader2 className="h-3 w-3 animate-spin" />
          )}
        </div>
        <div className={`${dense ? 'text-[10px]' : 'text-xs'} whitespace-pre-wrap break-words leading-relaxed text-emerald-100/80`}>
          {contentState.answer}
          {contentState.isAnswerActive && (
            <span className="ml-1 inline-block h-3 w-0.5 bg-emerald-400/70 align-middle" />
          )}
        </div>
      </div>
    );
  };

  const hasContent = contentState.thinking || contentState.answer || contentState.isThinkingActive || contentState.isAnswerActive;

  if (!hasContent && !isStreaming) {
    return (
      <div className={`flex min-h-0 flex-col rounded-lg border border-white/10 bg-black/40 ${dense ? 'p-2' : 'p-3'} ${className || ''}`}>
        <div className="flex shrink-0 items-center justify-between text-[10px] text-text-dim">
          <div className="flex items-center gap-2">
            <span className="uppercase tracking-wide">流式标签解析</span>
            <span className="text-[9px]">等待数据...</span>
          </div>
          {onClear && (
            <button
              type="button"
              onClick={onClear}
              className="flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[9px] hover:border-white/30"
            >
              Clear
            </button>
          )}
        </div>
        <div className={`${dense ? 'py-2' : 'py-4'} text-center text-[10px] text-text-dim`}>
          开启流式面试后可查看thinking和answer的实时解析
        </div>
      </div>
    );
  }

  return (
    <div className={`flex min-h-0 flex-col rounded-lg border border-white/10 bg-black/40 ${dense ? 'p-2' : 'p-3'} ${className || ''}`}>
      <div className={`${dense ? 'mb-1' : 'mb-2'} flex shrink-0 items-center justify-between text-[10px] text-text-dim`}>
        <div className="flex items-center gap-2">
          <span className="uppercase tracking-wide">流式标签解析</span>
          <span className="text-[9px]">
            {isStreaming ? 'streaming...' : autoScroll ? 'auto-scroll' : 'paused'}
          </span>
        </div>
        {onClear && (
          <button
            type="button"
            onClick={onClear}
            className="flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[9px] hover:border-white/30"
          >
            Clear
          </button>
        )}
      </div>

      <div ref={outputRef} className={`min-h-0 flex-1 overflow-y-auto pr-1 scrollbar-thin scrollbar-thumb-white/10 ${dense ? 'max-h-16 space-y-1' : 'max-h-96 space-y-2'}`}>
        {renderThinkingSection()}
        {renderAnswerSection()}
      </div>
    </div>
  );
}

export default StreamingTags;
