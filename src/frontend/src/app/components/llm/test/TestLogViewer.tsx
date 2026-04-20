import { AlertTriangle, CheckCircle2, Info, ArrowUpRight, ArrowDownLeft } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { TestLog } from './types';

const LOG_STYLES: Record<TestLog['type'], { icon: JSX.Element; className: string; label: string }> = {
  info: { icon: <Info className="size-3" />, className: 'text-blue-200', label: 'Info' },
  error: { icon: <AlertTriangle className="size-3" />, className: 'text-red-200', label: 'Error' },
  success: { icon: <CheckCircle2 className="size-3" />, className: 'text-emerald-200', label: 'Success' },
  request: { icon: <ArrowUpRight className="size-3" />, className: 'text-amber-200', label: 'Request' },
  response: { icon: <ArrowDownLeft className="size-3" />, className: 'text-cyan-200', label: 'Response' }
};

interface TestLogViewerProps {
  logs: TestLog[];
  className?: string;
}

const renderDetails = (details: unknown) => {
  if (details == null) return null;
  if (typeof details === 'string') return details;
  try {
    return JSON.stringify(details, null, 2);
  } catch {
    return String(details);
  }
};

export function TestLogViewer({ logs, className }: TestLogViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const rendered = useMemo(() => logs.slice(-200), [logs]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !autoScroll) return;
    el.scrollTop = el.scrollHeight;
  }, [rendered, autoScroll]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      setAutoScroll(nearBottom);
    };
    el.addEventListener('scroll', onScroll);
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <div className={className || ''}>
      <div className="flex items-center justify-between text-[10px] text-text-dim mb-2">
        <span>测试日志</span>
        <span className="text-[9px]">{autoScroll ? '自动滚动' : '已暂停滚动'}</span>
      </div>
      <div
        ref={containerRef}
        className="max-h-56 overflow-auto rounded-lg border border-white/10 bg-black/30 p-2 space-y-2"
      >
        {rendered.length === 0 ? (
          <div className="text-[11px] text-text-dim">暂无日志</div>
        ) : (
          rendered.map((log) => {
            const style = LOG_STYLES[log.type];
            const detailText = renderDetails(log.details);
            return (
              <div key={log.id} className="text-[11px] text-text-main">
                <div className="flex items-start gap-2">
                  <span className={`mt-0.5 ${style.className}`}>{style.icon}</span>
                  <div className="flex-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[9px] uppercase tracking-wider text-text-dim">{style.label}</span>
                      <span className="text-[9px] text-text-dim">
                        {new Date(log.timestamp).toLocaleTimeString()}
                      </span>
                    </div>
                    <div className="text-[11px] text-text-main mt-0.5 whitespace-pre-wrap break-words">
                      {log.message}
                    </div>
                    {detailText ? (
                      <details className="mt-1 text-[10px] text-text-dim">
                        <summary className="cursor-pointer">查看详情</summary>
                        <pre className="mt-1 whitespace-pre-wrap break-words text-[10px] text-text-muted font-mono">
                          {detailText}
                        </pre>
                      </details>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
