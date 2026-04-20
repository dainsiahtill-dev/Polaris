"use client";

import { useRef, useEffect } from 'react';
import { cn } from '@/app/components/ui/utils';
import type { LogEntry, LogLevel } from '@/types/log';
import {
  CheckCircle2,
  XCircle,
  Loader2,
  AlertTriangle,
  Brain,
  FileText,
  Zap,
  Clock,
  MessageSquare,
} from 'lucide-react';

export type { LogLevel } from '@/types/log';

interface ExecutionLogProps {
  logs: LogEntry[];
  maxHeight?: string;
  className?: string;
}

const levelIcons: Record<LogLevel, React.ReactNode> = {
  info: <Clock className="h-3.5 w-3.5 text-white/40" />,
  success: <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />,
  warning: <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />,
  error: <XCircle className="h-3.5 w-3.5 text-red-400" />,
  thinking: <Brain className="h-3.5 w-3.5 text-blue-400" />,
  tool: <Zap className="h-3.5 w-3.5 text-cyan-400" />,
  exec: <Loader2 className="h-3.5 w-3.5 text-orange-400" />,
};

const levelColors: Record<LogLevel, string> = {
  info: 'border-white/5 bg-white/[0.02]',
  success: 'border-emerald-500/20 bg-emerald-500/5',
  warning: 'border-amber-500/20 bg-amber-500/5',
  error: 'border-red-500/20 bg-red-500/5',
  thinking: 'border-blue-500/20 bg-blue-500/5',
  tool: 'border-cyan-500/20 bg-cyan-500/5',
  exec: 'border-orange-500/20 bg-orange-500/5',
};

const sourceIcons: Record<string, React.ReactNode> = {
  PM: <Brain className="h-3 w-3" />,
  Director: <Zap className="h-3 w-3" />,
  QA: <CheckCircle2 className="h-3 w-3" />,
  CE: <FileText className="h-3 w-3" />,
  System: <Clock className="h-3 w-3" />,
  AGENTS: <FileText className="h-3 w-3" />,
};

export function ExecutionLog({ logs, maxHeight = "200px", className }: ExecutionLogProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // 自动滚动到底部
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  const formatTime = (timestamp: string) => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch {
      return timestamp;
    }
  };

  return (
    <div className={cn("rounded-xl border border-white/10 bg-white/5", className)}>
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-white/5 px-3 py-2">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4 text-white/40" />
          <span className="text-xs font-medium text-white/60">执行日志</span>
        </div>
        <div className="text-[10px] text-white/30">{logs.length} 条</div>
      </div>

      {/* 日志列表 */}
      <div
        ref={scrollRef}
        className="space-y-1 overflow-y-auto p-2"
        style={{ maxHeight }}
      >
        {logs.length === 0 ? (
          <div className="py-4 text-center text-xs text-white/20">
            等待执行...
          </div>
        ) : (
          logs.map((log, index) => {
            const isLatest = index === logs.length - 1;
            return (
              <div
                key={log.id}
                className={cn(
                  "rounded-lg border p-2 text-[11px] transition-all",
                  levelColors[log.level],
                  isLatest && "ring-1 ring-white/10",
                )}
              >
                <div className="flex items-start gap-2">
                  {/* 图标 */}
                  <div className="mt-0.5 shrink-0">{levelIcons[log.level]}</div>

                  {/* 内容 */}
                  <div className="flex-1 min-w-0">
                    {/* 头部信息 */}
                    <div className="flex items-center gap-2 text-white/40">
                      <span className="font-mono text-[10px]">
                        {formatTime(log.timestamp)}
                      </span>
                      {log.source && (
                        <span className="flex items-center gap-1 rounded bg-white/5 px-1.5 py-0.5 text-[10px]">
                          {sourceIcons[log.source] || null}
                          {log.source}
                        </span>
                      )}
                    </div>

                    {/* 消息 */}
                    <div className="mt-0.5 text-white/70">{log.message}</div>

                    {/* 详情 */}
                    {log.details && (
                      <div className="mt-1 text-white/40">{log.details}</div>
                    )}

                    {/* 元数据 */}
                    {log.meta && Object.keys(log.meta).length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {Object.entries(log.meta).map(([key, value]) => (
                          <span
                            key={key}
                            className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-white/30"
                          >
                            {key}: {String(value)}
                          </span>
                        ))}
                      </div>
                    )}
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
