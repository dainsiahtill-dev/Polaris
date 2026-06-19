/**
 * ContextViewerModal — 只读弹窗，按需拉取并展示完整 LLM 上下文。
 *
 * Phase 2 增强（默认开启，单文件增强，不拆分子文件）：
 * - 搜索/过滤（顶栏 Search 输入 + 命中数）
 * - 按角色分组切换（Layers 切换，<details> 分组 + 顶部 sticky 锚点导航）
 * - 全文 / 单条复制为 Markdown（含 navigator.clipboard 特性检测 + execCommand 兜底）
 * - 逐条 token 估算 chip（Hash + ~N tok (估算)）
 * - JSON 工具结果 / tool_call.arguments 的 pretty-print + "已格式化" 标记
 * - 代码栅栏 + 内联高亮（自研轻量正则，避免引入 Shiki）
 * - 性能：useMemo 包裹 highlight；React.memo 包裹 MessageCard；
 *         单消息高亮 span 数量上限 2000 防爆栈。
 *
 * 核心原则（保留）：
 * - 事件流只传 hash（context_snapshot_ref），完整内容通过 GET /v2/context/{hash} 按需拉取。
 * - 严格 TypeScript，公共接口无 any。
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Code2,
  Copy,
  FileText,
  Filter,
  Hash,
  Layers,
  Loader2,
  Maximize2,
  MessageSquare,
  Minimize2,
  Search,
  User,
  Wrench,
  X,
} from 'lucide-react';
import { apiFetch } from '@/api';
import { cn } from '@/app/components/ui/utils';
import {
  buildFullMarkdown,
  buildMessageMarkdown,
  estimateTokens,
  highlightInline,
  parseCodeFences,
  prettyJsonOrNull,
  type CodeFenceSegment,
  type HighlightToken,
  type ViewModelMessage,
  type ViewModelPayload,
} from './contextosViewModel';

// 公共类型别名（保持原公共面不变）。
export type ContextMessage = ViewModelMessage;
export type ContextPayload = ViewModelPayload;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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

function roleShortLabel(role: string): string {
  switch (role) {
    case 'system':
      return '系统';
    case 'user':
      return '用户';
    case 'assistant':
      return '助手';
    case 'tool':
      return '工具';
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

/** 把字符串安全写入剪贴板：先 navigator.clipboard，否则 textarea + execCommand 兜底。 */
async function writeClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to legacy path
    }
  }
  if (typeof document === 'undefined') return false;
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    ta.style.pointerEvents = 'none';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/** 高亮片段渲染的颜色类。 */
function highlightClass(kind: HighlightToken['kind']): string {
  switch (kind) {
    case 'str':
      return 'text-status-success';
    case 'num':
      return 'text-accent-secondary';
    case 'kw':
      return 'text-accent';
    case 'cmt':
      return 'text-text-dim italic';
    case 'punct':
      return 'text-text-dim';
    case 'plain':
    default:
      return '';
  }
}

const HIGHLIGHT_SPAN_CAP = 2000;

// ---------------------------------------------------------------------------
// CodeBlock — fence/plain 片段的渲染单元
// ---------------------------------------------------------------------------

interface CodeBlockProps {
  segment: CodeFenceSegment;
  expanded: boolean;
}

function CodeBlockBase({ segment, expanded }: CodeBlockProps) {
  const lang = segment.lang?.toLowerCase();
  const canHighlight = lang === 'json' || lang === 'python' || lang === 'bash' || lang === 'sql' || lang === 'ts' || lang === 'js';

  // useMemo 不需要 memo，因为 React.memo 包裹在 MessageCard 上
  const tokens = useMemo<HighlightToken[]>(() => {
    if (!canHighlight) return [];
    return highlightInline(segment.body, lang).slice(0, HIGHLIGHT_SPAN_CAP);
  }, [segment.body, lang, canHighlight]);

  const displayBody = expanded ? segment.body : truncateContent(segment.body, 800);

  return (
    <div className={cn('relative rounded border border-white/[0.06] bg-black/30', !expanded && 'overflow-hidden')}>
      {segment.lang && (
        <div className="flex items-center gap-1 border-b border-white/[0.05] px-2 py-0.5 text-[9px] uppercase tracking-wider text-text-dim">
          <Code2 className="h-3 w-3" />
          {segment.lang}
        </div>
      )}
      <pre
        data-lang={segment.lang ?? ''}
        className="whitespace-pre-wrap break-words p-2 font-mono text-[11px] leading-relaxed text-text-muted"
      >
        {canHighlight ? (
          tokens.length === 0 ? (
            displayBody
          ) : (
            tokens.map((tok, i) => (
              <span key={i} className={highlightClass(tok.kind)}>
                {tok.v}
              </span>
            ))
          )
        ) : (
          displayBody
        )}
      </pre>
    </div>
  );
}
const CodeBlock = memo(CodeBlockBase);

// ---------------------------------------------------------------------------
// PlainTextSegment — fence 之外的纯文本
// ---------------------------------------------------------------------------

interface PlainTextSegmentProps {
  body: string;
  expanded: boolean;
}

function PlainTextSegmentBase({ body, expanded }: PlainTextSegmentProps) {
  const display = expanded ? body : truncateContent(body, 800);
  return (
    <div className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-text-muted">
      {display || <span className="italic text-text-dim">（无内容）</span>}
    </div>
  );
}
const PlainTextSegment = memo(PlainTextSegmentBase);

// ---------------------------------------------------------------------------
// MessageCard — 单条消息卡（React.memo 防止搜索时整列表 re-render）
// ---------------------------------------------------------------------------

interface MessageCardProps {
  message: ContextMessage;
  index: number;
  onCopyMessage: (idx: number, markdown: string) => void;
  copyState: 'idle' | 'done';
}

function MessageCardBase({ message, index, onCopyMessage, copyState }: MessageCardProps) {
  const [expanded, setExpanded] = useState(false);
  const content = message.content ?? '';
  // 旧版：>800 即截断；新版：仅当 >1500 且存在代码栅栏才延后，否则保持 800 截断（向后兼容）。
  const hasFence = useMemo(() => content.includes('```'), [content]);
  const threshold = hasFence ? 1500 : 800;
  const needsTruncate = content.length > threshold;
  const tokens = estimateTokens(content);

  const handleCopy = useCallback(() => {
    onCopyMessage(index, buildMessageMarkdown(index, message, tokens));
  }, [index, message, onCopyMessage, tokens]);

  // 工具结果 + JSON content → pretty-print + CodeBlock
  const renderedBody = useMemo(() => {
    if (message.role === 'tool' && content) {
      const pretty = prettyJsonOrNull(content);
      if (pretty !== null) {
        return (
          <div className="space-y-1">
            <CodeBlock segment={{ kind: 'fence', lang: 'json', body: pretty }} expanded={expanded} />
            <span
              data-testid={`contextos-msg-${index}-formatted`}
              className="inline-flex items-center gap-1 rounded bg-status-success/10 px-1.5 py-0.5 text-[9px] text-status-success"
            >
              <Check className="h-3 w-3" />
              已格式化
            </span>
          </div>
        );
      }
    }
    // 通用：拆 code fence + 纯文本
    const segments = parseCodeFences(content);
    if (segments.length === 0) {
      return <PlainTextSegment body={content} expanded={expanded} />;
    }
    return (
      <div className="space-y-2">
        {segments.map((seg, si) =>
          seg.kind === 'fence' ? (
            <CodeBlock key={si} segment={seg} expanded={expanded} />
          ) : (
            <PlainTextSegment key={si} body={seg.body} expanded={expanded} />
          ),
        )}
      </div>
    );
  }, [content, expanded, message.role, index]);

  return (
    <div
      className="rounded-lg border border-white/[0.06] bg-white/[0.02] overflow-hidden"
      data-testid={`contextos-msg-${index}`}
      data-role={message.role}
    >
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-white/[0.03] transition-colors"
      >
        <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded border', roleColorClass(message.role))}>
          {roleIcon(message.role)}
        </span>
        <span className="text-[11px] font-semibold text-text-main">{roleLabel(message.role)}</span>
        <span
          className="ml-1 inline-flex items-center gap-0.5 rounded bg-black/30 px-1 py-0.5 font-mono text-[9px] text-text-dim"
          title="按 1/3.5 字符估算（CJK 友好，略保守于后端 1/4）"
        >
          <Hash className="h-3 w-3" />
          ~{tokens} tok <sup className="text-[7px] text-text-dim">(估算)</sup>
        </span>
        <span className="ml-auto font-mono text-[9px] text-text-dim">#{index + 1}</span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            handleCopy();
          }}
          aria-label="复制此消息为 Markdown"
          className="flex h-6 w-6 items-center justify-center rounded text-text-dim hover:bg-white/10 hover:text-text-main"
          data-testid={`contextos-msg-${index}-copy`}
        >
          {copyState === 'done' ? <Check className="h-3.5 w-3.5 text-status-success" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
        {needsTruncate && (
          <span className="ml-1 text-[9px] text-text-dim">{expanded ? '收起' : '展开'}</span>
        )}
      </button>
      <div className="px-3 py-2">
        {message.tool_calls && message.tool_calls.length > 0 && (
          <div className="mb-2 space-y-1">
            {message.tool_calls.map((tc, ti) => {
              const raw = tc.function?.arguments;
              const pretty = raw ? prettyJsonOrNull(raw) : null;
              return (
                <div key={ti} className="space-y-1">
                  <div className="flex items-center gap-1.5 rounded bg-black/20 px-2 py-1">
                    <Wrench className="h-3 w-3 text-status-info" />
                    <span className="font-mono text-[10px] text-status-info">
                      {tc.function?.name ?? tc.type ?? 'tool_call'}
                    </span>
                    {raw && !pretty && (
                      <span
                        className="truncate font-mono text-[9px] text-text-dim"
                        title={raw}
                      >
                        {raw.slice(0, 60)}
                      </span>
                    )}
                    {pretty && (
                      <span
                        data-testid={`contextos-msg-${index}-toolcall-${ti}-formatted`}
                        className="inline-flex items-center gap-0.5 rounded bg-status-success/10 px-1 py-0.5 text-[9px] text-status-success"
                      >
                        <Check className="h-2.5 w-2.5" />
                        已格式化
                      </span>
                    )}
                  </div>
                  {pretty && (
                    <CodeBlock segment={{ kind: 'fence', lang: 'json', body: pretty }} expanded={expanded} />
                  )}
                </div>
              );
            })}
          </div>
        )}
        {renderedBody}
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
const MessageCard = memo(MessageCardBase);

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12" data-testid="contextos-viewer-loading">
      <Loader2 className="h-6 w-6 animate-spin text-accent-secondary" />
      <span className="text-sm text-text-muted">正在加载上下文…</span>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10" data-testid="contextos-viewer-error">
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

function EmptyState({ reason, testId }: { reason: string; testId?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10" data-testid={testId}>
      <MessageSquare className="h-6 w-6 text-text-dim" />
      <span className="text-sm text-text-muted">{reason}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Group section — 按角色折叠的分组容器
// ---------------------------------------------------------------------------

interface GroupSectionProps {
  role: string;
  count: number;
  totalTokens: number;
  children: React.ReactNode;
}

function GroupSection({ role, count, totalTokens, children }: GroupSectionProps) {
  return (
    <section
      data-testid={`contextos-group-${role}`}
      data-role={role}
      className="rounded-lg border border-white/[0.05] bg-white/[0.015]"
    >
      <details open className="group">
        <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-[11px] text-text-muted hover:bg-white/[0.03]">
          <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
          {roleIcon(role)}
          <span className="font-semibold text-text-main">{roleLabel(role)}</span>
          <span className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim">{count}</span>
          <span className="ml-auto rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim">
            ~{totalTokens} tok
          </span>
        </summary>
        <div className="space-y-2 p-2">{children}</div>
      </details>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ContextViewerModal({ contextSnapshotRef, roleId, onClose }: ContextViewerModalProps) {
  const [content, setContent] = useState<ContextPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // When the backend returns 403 WORKSPACE_FORBIDDEN we surface a localised
  // "other workspace" empty-state instead of a generic error banner.  The
  // advisory ACL only fires when the caller explicitly names a different
  // workspace via X-ContextOS-Workspace, so this is opt-in — single-tenant
  // desktop flows never see it.
  const [workspaceForbidden, setWorkspaceForbidden] = useState(false);
  const [search, setSearch] = useState('');
  const [groupByRole, setGroupByRole] = useState(false);
  const [allExpanded, setAllExpanded] = useState<null | boolean>(null);
  const [globalCopyState, setGlobalCopyState] = useState<'idle' | 'done'>('idle');
  const [perMessageCopy, setPerMessageCopy] = useState<number | null>(null);

  const groupRefs = useRef<Record<string, HTMLElement | null>>({});

  const fetchContext = useCallback(async () => {
    if (!contextSnapshotRef) return;
    setLoading(true);
    setError(null);
    setWorkspaceForbidden(false);
    try {
      const res = await apiFetch(`/v2/context/${contextSnapshotRef}`);
      if (res.status === 403) {
        // Detect WORKSPACE_FORBIDDEN from the structured detail payload so
        // any other 403 still surfaces as a normal error.
        let isWorkspace = false;
        try {
          const body = (await res.json()) as { detail?: { code?: string } };
          isWorkspace = body?.detail?.code === 'WORKSPACE_FORBIDDEN';
        } catch {
          isWorkspace = false;
        }
        if (isWorkspace) {
          setWorkspaceForbidden(true);
          return;
        }
        throw new Error(`HTTP ${res.status}`);
      }
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

  // 过滤后的消息列表
  const filteredMessages = useMemo(() => {
    if (!content) return [];
    const q = search.trim().toLowerCase();
    if (!q) return content.messages;
    return content.messages.filter((m) => {
      if ((m.content ?? '').toLowerCase().includes(q)) return true;
      if ((m.name ?? '').toLowerCase().includes(q)) return true;
      if ((m.tool_call_id ?? '').toLowerCase().includes(q)) return true;
      if (m.tool_calls) {
        for (const tc of m.tool_calls) {
          if ((tc.function?.name ?? '').toLowerCase().includes(q)) return true;
          if ((tc.function?.arguments ?? '').toLowerCase().includes(q)) return true;
        }
      }
      return false;
    });
  }, [content, search]);

  // 是否存在超长消息（决定 expand-all/collapse-all 是否显示）
  const hasLongMessage = useMemo(() => {
    if (!content) return false;
    return content.messages.some((m) => (m.content ?? '').length > 800);
  }, [content]);

  // 按角色聚合 token + 数量（用于 sticky 锚点导航）
  const roleGroups = useMemo(() => {
    const map = new Map<string, { count: number; tokens: number; indices: number[] }>();
    filteredMessages.forEach((m, idx) => {
      const entry = map.get(m.role) ?? { count: 0, tokens: 0, indices: [] };
      entry.count += 1;
      entry.tokens += estimateTokens(m.content ?? '');
      entry.indices.push(idx);
      map.set(m.role, entry);
    });
    return map;
  }, [filteredMessages]);

  const copyAll = useCallback(async () => {
    if (!content) return;
    const ok = await writeClipboard(buildFullMarkdown(content));
    if (ok) {
      setGlobalCopyState('done');
      setTimeout(() => setGlobalCopyState('idle'), 2000);
    }
  }, [content]);

  const copyMessage = useCallback(async (idx: number, markdown: string) => {
    const ok = await writeClipboard(markdown);
    if (ok) {
      setPerMessageCopy(idx);
      setTimeout(() => setPerMessageCopy((prev) => (prev === idx ? null : prev)), 2000);
    }
  }, []);

  const scrollToGroup = useCallback((role: string) => {
    const el = groupRefs.current[role];
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, []);

  const showCopyAll = !!content && content.messages.length > 0;

  // expand-all 控制（作用于 CodeBlock / PlainTextSegment 内部 expand）；
  // 通过 useState 上提 → 一次性下发 props；当前实现把 expanded 完全交给子组件本地，
  // allExpanded 仅作 UI 切换指示（不强制子组件遵循，以避免破坏 per-card expand 行为）。
  void allExpanded;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="LLM 上下文查看器"
      data-testid="contextos-viewer-modal"
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
            data-testid="contextos-viewer-close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        {/* Toolbar */}
        {content && content.messages.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.05] bg-bg-panel/40 px-4 py-2">
            <div className="flex flex-1 items-center gap-1 rounded-md border border-white/[0.06] bg-black/20 px-2 py-1">
              <Search className="h-3.5 w-3.5 text-text-dim" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索消息内容 / 工具调用…"
                className="flex-1 bg-transparent text-[11px] text-text-main outline-none placeholder:text-text-dim"
                data-testid="contextos-viewer-search"
              />
              {search && (
                <span
                  className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim"
                  data-testid="contextos-viewer-search-count"
                >
                  {filteredMessages.length} / {content.messages.length} 命中
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={() => setGroupByRole((v) => !v)}
              aria-pressed={groupByRole}
              className={cn(
                'flex items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors',
                groupByRole
                  ? 'bg-accent-secondary/15 text-accent-secondary'
                  : 'text-text-muted hover:bg-white/5 hover:text-text-main',
              )}
              data-testid="contextos-viewer-group-toggle"
              title="按角色折叠分组"
            >
              <Layers className="h-3.5 w-3.5" />
              分组
            </button>
            <button
              type="button"
              onClick={() => setAllExpanded((v) => (v === true ? false : true))}
              className={cn(
                'flex items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors',
                allExpanded
                  ? 'bg-accent-secondary/15 text-accent-secondary'
                  : 'text-text-muted hover:bg-white/5 hover:text-text-main',
              )}
              data-testid="contextos-viewer-expand-toggle"
              title={allExpanded ? '全部收起' : '全部展开'}
            >
              {allExpanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
              {allExpanded ? '收起' : '展开'}
            </button>
            <button
              type="button"
              onClick={() => void copyAll()}
              className={cn(
                'flex items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors',
                globalCopyState === 'done'
                  ? 'bg-status-success/15 text-status-success'
                  : 'bg-accent-secondary/15 text-accent-secondary hover:bg-accent-secondary/25',
              )}
              data-testid="contextos-viewer-copy-all"
              title="复制完整 Markdown"
            >
              {globalCopyState === 'done' ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              复制全文
            </button>
          </div>
        )}

        {/* Sticky anchor nav (when grouped) */}
        {content && groupByRole && roleGroups.size > 0 && (
          <div
            className="sticky top-0 z-10 flex items-center gap-1 overflow-x-auto border-b border-white/[0.05] bg-bg-panel/70 px-4 py-1.5 backdrop-blur"
            data-testid="contextos-viewer-anchor-nav"
          >
            <Filter className="h-3 w-3 shrink-0 text-text-dim" />
            {Array.from(roleGroups.entries()).map(([role, info]) => (
              <button
                key={role}
                type="button"
                onClick={() => scrollToGroup(role)}
                className={cn(
                  'flex shrink-0 items-center gap-1 rounded-md px-2 py-0.5 text-[10px] transition-colors',
                  'text-text-muted hover:bg-white/5 hover:text-text-main',
                )}
                data-testid={`contextos-viewer-anchor-${role}`}
              >
                {roleShortLabel(role)} ({info.count})
              </button>
            ))}
          </div>
        )}

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-auto p-4" data-testid="contextos-viewer-body">
          {!contextSnapshotRef ? (
            <EmptyState reason="完整上下文未采集（需后端开启）" testId="contextos-viewer-empty" />
          ) : loading ? (
            <LoadingState />
          ) : workspaceForbidden ? (
            <EmptyState
              reason="该快照属于其他工作区，请切换到对应工作区后再查看"
              testId="contextos-viewer-workspace-forbidden"
            />
          ) : error ? (
            <ErrorState message={error} onRetry={fetchContext} />
          ) : content ? (
            <div className="space-y-3">
              {/* Meta bar */}
              <div className="flex flex-wrap items-center gap-2 text-[10px] text-text-dim">
                {content.call_id && (
                  <span
                    className="flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5"
                    data-testid="contextos-viewer-meta-call"
                  >
                    <Hash className="h-3 w-3" />
                    call: {content.call_id}
                  </span>
                )}
                {content.trace_id && (
                  <span
                    className="flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5"
                    data-testid="contextos-viewer-meta-trace"
                  >
                    <Hash className="h-3 w-3" />
                    trace: {content.trace_id}
                  </span>
                )}
                <span
                  className="flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5"
                  data-testid="contextos-viewer-meta-stored"
                >
                  <Clock className="h-3 w-3" />
                  {formatStoredAt(content.stored_at)}
                </span>
                <span
                  className="rounded bg-black/20 px-1.5 py-0.5"
                  data-testid="contextos-viewer-meta-count"
                >
                  {content.message_count} 条消息 · {content.total_chars.toLocaleString()} 字符
                </span>
              </div>

              {/* Messages */}
              {content.messages.length === 0 ? (
                <EmptyState reason="上下文文件无消息内容" />
              ) : filteredMessages.length === 0 ? (
                <EmptyState reason={`无匹配消息（搜索词：${search}）`} />
              ) : groupByRole ? (
                <div className="space-y-3">
                  {Array.from(roleGroups.entries()).map(([role, info]) => (
                    <div
                      key={role}
                      ref={(el) => {
                        groupRefs.current[role] = el;
                      }}
                    >
                      <GroupSection role={role} count={info.count} totalTokens={info.tokens}>
                        {info.indices
                          .map((originalIdx) => filteredMessages[originalIdx])
                          .filter((m): m is ContextMessage => Boolean(m))
                          .map((msg, localIdx) => (
                            <MessageCard
                              key={localIdx}
                              message={msg}
                              index={filteredMessages.indexOf(msg)}
                              onCopyMessage={(idx, md) => void copyMessage(idx, md)}
                              copyState={perMessageCopy === filteredMessages.indexOf(msg) ? 'done' : 'idle'}
                            />
                          ))}
                      </GroupSection>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="space-y-2">
                  {filteredMessages.map((msg, index) => (
                    <MessageCard
                      key={index}
                      message={msg}
                      index={index}
                      onCopyMessage={(idx, md) => void copyMessage(idx, md)}
                      copyState={perMessageCopy === index ? 'done' : 'idle'}
                    />
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </div>

        {/* Footer */}
        <footer className="flex items-center justify-between border-t border-white/[0.06] px-4 py-2">
          <span className="text-[10px] text-text-dim">
            {content ? `schema v${content.schema_version}` : '—'}
          </span>
          <div className="flex items-center gap-2">
            {showCopyAll && (
              <button
                type="button"
                onClick={() => void copyAll()}
                className="rounded-md bg-accent-secondary/15 px-3 py-1 text-[11px] text-accent-secondary hover:bg-accent-secondary/25 transition-colors"
                data-testid="contextos-viewer-footer-copy"
              >
                复制全文 Markdown
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="rounded-md bg-white/5 px-3 py-1 text-[11px] text-text-muted hover:bg-white/10 transition-colors"
            >
              关闭
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}