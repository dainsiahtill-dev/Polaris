/**
 * ContextViewerModal — 只读弹窗，按需拉取并展示完整 LLM 上下文。
 *
 * 核心原则：
 * - 事件流只传 hash（context_snapshot_ref），完整内容通过 GET /v2/context/{hash} 按需拉取。
 * - 结构化展示：按 role 分组（system / user / assistant / tool），支持折叠/展开。
 * - 无 hash 时显示占位提示；fetch 失败时显示错误状态。
 * - 严格 TypeScript，公共接口无 any。
 */

import { useState, useCallback, useEffect } from 'react';
import { X, Loader2, AlertCircle, MessageSquare, Bot, Wrench, User, Hash, Clock, FileText } from 'lucide-react';
import { apiFetch } from '@/api';
import { cn } from '@/app/components/ui/utils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ContextMessage {
  role: string;
  content: string | null;
  name?: string;
  tool_call_id?: string;
  tool_calls?: Array<{
    id?: string;
    type?: string;
    function?: { name?: string; arguments?: string };
  }>;
}

interface ContextPayload {
  schema_version: number;
  hash: string;
  trace_id: string | null;
  call_id: string | null;
  messages: ContextMessage[];
  stored_at: string | null;
  message_count: number;
  total_chars: number;
}

export interface ContextViewerModalProps {
  contextSnapshotRef: string | null;
  roleId: string;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function roleIcon(role: string) {
  switch (role) {
    case 'system':
      return <FileText className="h-3.5 w-3.5" />;
    case 'user':
      return <User className="h-3.5 w-3.5" />;
    case 'assistant':
      return <Bot className="h-3.5 w-3.5" />;
    case 'tool':
      return <Wrench className="h-3.5 w-3.5" />;
    default:
      return <MessageSquare className="h-3.5 w-3.5" />;
  }
}

function roleLabel(role: string): string {
  switch (role) {
    case 'system':
      return '系统提示';
    case 'user':
      return '用户';
    case 'assistant':
      return '助手';
    case 'tool':
      return '工具结果';
    default:
      return role;
  }
}

function roleColorClass(role: string): string {
  switch (role) {
    case 'system':
      return 'bg-accent-secondary/10 text-accent-secondary border-accent-secondary/20';
    case 'user':
      return 'bg-accent/10 text-accent border-accent/20';
    case 'assistant':
      return 'bg-gold/10 text-gold border-gold/20';
    case 'tool':
      return 'bg-status-info/10 text-status-info border-status-info/20';
    default:
      return 'bg-white/[0.04] text-text-muted border-white/[0.06]';
  }
}

function formatStoredAt(raw: string | null): string {
  if (!raw) return '—';
  try {
    const d = new Date(raw);
    return d.toLocaleString('zh-CN', { hour12: false });
  } catch {
    return raw;
  }
}

function truncateContent(content: string | null, maxLen = 800): string {
  if (!content) return '';
  if (content.length <= maxLen) return content;
  return content.slice(0, maxLen) + '\n…（内容已截断，共 ' + content.length + ' 字符）';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function MessageCard({ message, index }: { message: ContextMessage; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const content = message.content ?? '';
  const needsTruncate = content.length > 800;
  const displayContent = expanded ? content : truncateContent(content, 800);

  return (
    <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-white/[0.03] transition-colors"
      >
        <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded border', roleColorClass(message.role))}>
          {roleIcon(message.role)}
        </span>
        <span className="text-[11px] font-semibold text-text-main">{roleLabel(message.role)}</span>
        <span className="ml-auto font-mono text-[9px] text-text-dim">#{index + 1}</span>
        {needsTruncate && (
          <span className="ml-1 text-[9px] text-text-dim">
            {expanded ? '收起' : '展开'}
          </span>
        )}
      </button>
      <div className="px-3 py-2">
        {message.tool_calls && message.tool_calls.length > 0 && (
          <div className="mb-2 space-y-1">
            {message.tool_calls.map((tc, ti) => (
              <div key={ti} className="flex items-center gap-1.5 rounded bg-black/20 px-2 py-1">
                <Wrench className="h-3 w-3 text-status-info" />
                <span className="font-mono text-[10px] text-status-info">
                  {tc.function?.name ?? tc.type ?? 'tool_call'}
                </span>
                {tc.function?.arguments && (
                  <span className="truncate font-mono text-[9px] text-text-dim" title={tc.function.arguments}>
                    {tc.function.arguments.slice(0, 60)}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
        <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-text-muted">
          {displayContent || <span className="italic text-text-dim">（无内容）</span>}
        </pre>
        {message.tool_call_id && (
          <div className="mt-1 flex items-center gap-1 text-[9px] text-text-dim">
            <Hash className="h-3 w-3" />
            tool_call_id: {message.tool_call_id}
          </div>
        )}
        {message.name && (
          <div className="mt-1 text-[9px] text-text-dim">
            name: {message.name}
          </div>
        )}
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12">
      <Loader2 className="h-6 w-6 animate-spin text-accent-secondary" />
      <span className="text-sm text-text-muted">正在加载上下文…</span>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10">
      <AlertCircle className="h-6 w-6 text-status-error" />
      <span className="text-sm text-status-error">加载失败</span>
      <span className="max-w-xs text-center text-[11px] text-text-dim">{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="mt-1 rounded-md bg-accent-secondary/15 px-3 py-1 text-[11px] text-accent-secondary hover:bg-accent-secondary/25 transition-colors"
      >
        重试
      </button>
    </div>
  );
}

function EmptyState({ reason }: { reason: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10">
      <MessageSquare className="h-6 w-6 text-text-dim" />
      <span className="text-sm text-text-muted">{reason}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ContextViewerModal({ contextSnapshotRef, roleId, onClose }: ContextViewerModalProps) {
  const [content, setContent] = useState<ContextPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchContext = useCallback(async () => {
    if (!contextSnapshotRef) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/v2/context/${contextSnapshotRef}`);
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}${text ? ': ' + text : ''}`);
      }
      const data = (await res.json()) as ContextPayload;
      setContent(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [contextSnapshotRef]);

  useEffect(() => {
    if (contextSnapshotRef) {
      void fetchContext();
    }
  }, [contextSnapshotRef, fetchContext]);

  // Close on Escape
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="LLM 上下文查看器"
    >
      <div className="flex max-h-[85vh] w-[92vw] max-w-3xl flex-col rounded-xl border border-white/[0.08] bg-bg-panel shadow-2xl">
        {/* Header */}
        <header className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <Bot className="h-4 w-4 shrink-0 text-accent-secondary" />
            <h2 className="truncate text-sm font-semibold text-text-main">
              完整上下文 · {roleId.toUpperCase()}
            </h2>
            {contextSnapshotRef && (
              <span className="flex items-center gap-1 rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim">
                <Hash className="h-3 w-3" />
                {contextSnapshotRef}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-dim hover:bg-white/5 hover:text-text-main transition-colors"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {!contextSnapshotRef ? (
            <EmptyState reason="完整上下文未采集（需后端开启）" />
          ) : loading ? (
            <LoadingState />
          ) : error ? (
            <ErrorState message={error} onRetry={fetchContext} />
          ) : content ? (
            <div className="space-y-3">
              {/* Meta bar */}
              <div className="flex flex-wrap items-center gap-2 text-[10px] text-text-dim">
                {content.call_id && (
                  <span className="flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5">
                    <Hash className="h-3 w-3" />
                    call: {content.call_id}
                  </span>
                )}
                {content.trace_id && (
                  <span className="flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5">
                    <Hash className="h-3 w-3" />
                    trace: {content.trace_id}
                  </span>
                )}
                <span className="flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5">
                  <Clock className="h-3 w-3" />
                  {formatStoredAt(content.stored_at)}
                </span>
                <span className="rounded bg-black/20 px-1.5 py-0.5">
                  {content.message_count} 条消息 · {content.total_chars.toLocaleString()} 字符
                </span>
              </div>

              {/* Messages */}
              {content.messages.length > 0 ? (
                <div className="space-y-2">
                  {content.messages.map((msg, index) => (
                    <MessageCard key={index} message={msg} index={index} />
                  ))}
                </div>
              ) : (
                <EmptyState reason="上下文文件无消息内容" />
              )}
            </div>
          ) : null}
        </div>

        {/* Footer */}
        <footer className="flex items-center justify-between border-t border-white/[0.06] px-4 py-2">
          <span className="text-[10px] text-text-dim">
            {content ? `schema v${content.schema_version}` : '—'}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md bg-white/5 px-3 py-1 text-[11px] text-text-muted hover:bg-white/10 transition-colors"
          >
            关闭
          </button>
        </footer>
      </div>
    </div>
  );
}
