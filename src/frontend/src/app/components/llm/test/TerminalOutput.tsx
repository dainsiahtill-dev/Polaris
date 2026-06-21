import { useEffect, useRef, useState } from 'react';
import type { TestEvent, TestEventType } from './types';

const EVENT_STYLES: Record<TestEventType, { prefix: string; className: string }> = {
  command: { prefix: '$', className: 'text-accent-text' },
  stdout: { prefix: '>', className: 'text-status-success' },
  stderr: { prefix: '!', className: 'text-status-warning' },
  response: { prefix: '<', className: 'text-accent-text' },
  result: { prefix: '✓', className: 'text-status-success' },
  error: { prefix: '✗', className: 'text-status-error' }
};

interface TerminalOutputProps {
  events: TestEvent[];
  placeholder?: string;
  title?: string;
  heightClassName?: string;
  className?: string;
  showHeader?: boolean;
}

export function TerminalOutput({
  events,
  placeholder,
  title = '终端输出',
  heightClassName = 'h-80',
  className,
  showHeader = true
}: TerminalOutputProps) {
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

  return (
    <div className={`space-y-2 ${className || ''}`}>
      {showHeader ? (
        <div className="flex items-center justify-between text-[10px] text-text-dim">
          <span>{title}</span>
          <span className="text-[9px]">{autoScroll ? '自动滚动' : '已暂停滚动'}</span>
        </div>
      ) : null}
      <div
        ref={outputRef}
        className={`soft-inset rounded-lg p-3 font-mono text-[11px] text-text-main ${heightClassName} overflow-y-auto`}
      >
        {events.length === 0 ? (
          <div className="text-text-dim">
            {placeholder || '$ 准备就绪，点击"测试"按钮开始...'}
          </div>
        ) : (
          events.map((event, index) => {
            const style = EVENT_STYLES[event.type];
            return (
              <div key={`${event.timestamp}-${index}`} className="mb-1 whitespace-pre-wrap break-words">
                <span className="text-text-dim">[{new Date(event.timestamp).toLocaleTimeString()}]</span>{' '}
                <span className={style.className}>
                  {style.prefix} {event.content}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
