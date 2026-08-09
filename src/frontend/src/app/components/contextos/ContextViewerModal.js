import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
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
 * Phase 3 无障碍硬化（默认开启，单文件增强）：
 * - AbortController 绑定 useEffect 清理：contextSnapshotRef 变化或组件卸载时
 *   取消 in-flight fetch，避免 setState-on-unmounted 与无谓 IO。
 * - 手写焦点陷阱：Tab / Shift+Tab 在弹窗内首尾可聚焦元素间循环。
 *   用容器级 keydown 监听 + Selector 集合，不引入 focus-trap 依赖。
 * - 焦点恢复：弹窗打开时记录 activeElement，关闭（onClose / Escape / 背景点击）
 *   时在清理函数中恢复。
 * - ARIA 强化：aria-labelledby 指向标题；正文区 aria-describedby 指向角色 chip
 *   元信息；aria-modal="true" 已在 dialog 上；加载/错误/空态补 aria-live="polite"。
 * - 弹窗打开时锁定 body 滚动（overflow:hidden + 还原），避免背景跟随。
 *
 * 核心原则（保留）：
 * - 事件流只传 hash（context_snapshot_ref），完整内容通过 GET /v2/context/{hash} 按需拉取。
 * - 严格 TypeScript，公共接口无 any。
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Bot, Check, ChevronRight, Clock, Code2, Copy, Cpu, FileText, Filter, Hash, Layers, Loader2, Maximize2, MessageSquare, Minimize2, Search, User, Wrench, X, } from 'lucide-react';
import { apiFetch } from '@/api';
import { cn } from '@/app/components/ui/utils';
import { buildFullMarkdown, buildMessageMarkdown, estimateTokens, highlightInline, normalizeViewModelPayload, parseCodeFences, prettyJsonOrNull, } from './contextosViewModel';
import { normalizeFinalProviderRequestPayload, } from './finalProviderRequestProtocol';
const CONTEXT_SNAPSHOT_REF_RE = /^[0-9a-f]{24}$/i;
// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function isRecord(value) {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function prettyJson(value) {
    try {
        return JSON.stringify(value ?? null, null, 2);
    }
    catch {
        return String(value ?? '');
    }
}
function roleIcon(role) {
    switch (role) {
        case 'system':
            return _jsx(FileText, { className: "h-3.5 w-3.5" });
        case 'user':
            return _jsx(User, { className: "h-3.5 w-3.5" });
        case 'assistant':
            return _jsx(Bot, { className: "h-3.5 w-3.5" });
        case 'tool':
            return _jsx(Wrench, { className: "h-3.5 w-3.5" });
        default:
            return _jsx(MessageSquare, { className: "h-3.5 w-3.5" });
    }
}
function roleLabel(role) {
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
function roleShortLabel(role) {
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
function roleColorClass(role) {
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
function formatStoredAt(raw) {
    if (!raw)
        return '—';
    try {
        const d = new Date(raw);
        return d.toLocaleString('zh-CN', { hour12: false });
    }
    catch {
        return raw;
    }
}
function truncateContent(content, maxLen = 800) {
    if (!content)
        return '';
    if (content.length <= maxLen)
        return content;
    return content.slice(0, maxLen) + '\n…（内容已截断，共 ' + content.length + ' 字符）';
}
/** 把字符串安全写入剪贴板：先 navigator.clipboard，否则 textarea + execCommand 兜底。 */
async function writeClipboard(text) {
    if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        }
        catch {
            // fall through to legacy path
        }
    }
    if (typeof document === 'undefined')
        return false;
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
    }
    catch {
        return false;
    }
}
/** 高亮片段渲染的颜色类。 */
function highlightClass(kind) {
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
/** Selector 集合：弹窗内参与 Tab 循环的可聚焦元素。 */
const FOCUSABLE_SELECTOR = [
    'a[href]',
    'area[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
    'audio[controls]',
    'video[controls]',
    'iframe',
    'object',
    'embed',
    'summary',
    '[contenteditable]:not([contenteditable="false"])',
].join(',');
/** 在容器内收集所有可达的可聚焦元素。 */
function getFocusableElements(container) {
    if (!container)
        return [];
    const nodes = Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR));
    return nodes.filter((el) => {
        if (el.hasAttribute('disabled'))
            return false;
        if (el.getAttribute('aria-hidden') === 'true')
            return false;
        // 跳过不可见元素（display:none / visibility:hidden）。
        // JSDOM 不实现 layout：getClientRects() 存在但恒返回空数组 → 不能用它判可见性。
        // 用 offsetParent 检测：JSDOM 中 offsetParent 为 null 仅当元素真的被 display:none 隐藏或
        // 未连接；这与生产浏览器的语义略不同，但对 JSDOM 测试更可靠。
        if (typeof el.offsetParent !== 'undefined') {
            // offsetParent === null 在 JSDOM 中常表示节点脱离 DOM 或 display:none。
            // 但 JSDOM 也对所有 display 不为 none 的元素返回非 null，所以我们额外用 style.display 兜底。
            const inlineDisplay = el.style.display;
            if (inlineDisplay === 'none')
                return false;
        }
        return true;
    });
}
function CodeBlockBase({ segment, expanded }) {
    const lang = segment.lang?.toLowerCase();
    const canHighlight = lang === 'json' || lang === 'python' || lang === 'bash' || lang === 'sql' || lang === 'ts' || lang === 'js';
    // useMemo 不需要 memo，因为 React.memo 包裹在 MessageCard 上
    const tokens = useMemo(() => {
        if (!canHighlight)
            return [];
        return highlightInline(segment.body, lang).slice(0, HIGHLIGHT_SPAN_CAP);
    }, [segment.body, lang, canHighlight]);
    const displayBody = expanded ? segment.body : truncateContent(segment.body, 800);
    return (_jsxs("div", { className: cn('relative rounded border border-white/[0.06] bg-black/30', !expanded && 'overflow-hidden'), children: [segment.lang && (_jsxs("div", { className: "flex items-center gap-1 border-b border-white/[0.05] px-2 py-0.5 text-[9px] uppercase tracking-wider text-text-dim", children: [_jsx(Code2, { className: "h-3 w-3" }), segment.lang] })), _jsx("pre", { "data-lang": segment.lang ?? '', className: "whitespace-pre-wrap break-words p-2 font-mono text-[11px] leading-relaxed text-text-muted", children: canHighlight ? (tokens.length === 0 ? (displayBody) : (tokens.map((tok, i) => (_jsx("span", { className: highlightClass(tok.kind), children: tok.v }, i))))) : (displayBody) })] }));
}
const CodeBlock = memo(CodeBlockBase);
function PlainTextSegmentBase({ body, expanded }) {
    const display = expanded ? body : truncateContent(body, 800);
    return (_jsx("div", { className: "whitespace-pre-wrap break-words text-[11px] leading-relaxed text-text-muted", children: display || _jsx("span", { className: "italic text-text-dim", children: "\uFF08\u65E0\u5185\u5BB9\uFF09" }) }));
}
const PlainTextSegment = memo(PlainTextSegmentBase);
function MessageCardBase({ message, index, onCopyMessage, copyState }) {
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
                return (_jsxs("div", { className: "space-y-1", children: [_jsx(CodeBlock, { segment: { kind: 'fence', lang: 'json', body: pretty }, expanded: expanded }), _jsxs("span", { "data-testid": `contextos-msg-${index}-formatted`, className: "inline-flex items-center gap-1 rounded bg-status-success/10 px-1.5 py-0.5 text-[9px] text-status-success", children: [_jsx(Check, { className: "h-3 w-3" }), "\u5DF2\u683C\u5F0F\u5316"] })] }));
            }
        }
        // 通用：拆 code fence + 纯文本
        const segments = parseCodeFences(content);
        if (segments.length === 0) {
            return _jsx(PlainTextSegment, { body: content, expanded: expanded });
        }
        return (_jsx("div", { className: "space-y-2", children: segments.map((seg, si) => seg.kind === 'fence' ? (_jsx(CodeBlock, { segment: seg, expanded: expanded }, si)) : (_jsx(PlainTextSegment, { body: seg.body, expanded: expanded }, si))) }));
    }, [content, expanded, message.role, index]);
    return (_jsxs("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] overflow-hidden", "data-testid": `contextos-msg-${index}`, "data-role": message.role, children: [_jsxs("div", { className: "flex w-full items-center gap-2 px-3 py-2 hover:bg-white/[0.03] transition-colors", children: [_jsxs("button", { type: "button", onClick: () => setExpanded((prev) => !prev), "aria-expanded": expanded, className: "flex min-w-0 flex-1 items-center gap-2 text-left", "data-testid": `contextos-msg-${index}-toggle`, children: [_jsx("span", { className: cn('flex h-6 w-6 shrink-0 items-center justify-center rounded border', roleColorClass(message.role)), children: roleIcon(message.role) }), _jsx("span", { className: "text-[11px] font-semibold text-text-main", children: roleLabel(message.role) }), _jsxs("span", { className: "ml-1 inline-flex items-center gap-0.5 rounded bg-black/30 px-1 py-0.5 font-mono text-[9px] text-text-dim", title: "\u6309 1/3.5 \u5B57\u7B26\u4F30\u7B97\uFF08CJK \u53CB\u597D\uFF0C\u7565\u4FDD\u5B88\u4E8E\u540E\u7AEF 1/4\uFF09", children: [_jsx(Hash, { className: "h-3 w-3" }), "~", tokens, " tok ", _jsx("sup", { className: "text-[7px] text-text-dim", children: "(\u4F30\u7B97)" })] }), _jsxs("span", { className: "ml-auto font-mono text-[9px] text-text-dim", children: ["#", index + 1] }), needsTruncate && (_jsx("span", { className: "ml-1 text-[9px] text-text-dim", children: expanded ? '收起' : '展开' }))] }), _jsx("button", { type: "button", onClick: handleCopy, "aria-label": "\u590D\u5236\u6B64\u6D88\u606F\u4E3A Markdown", className: "flex h-6 w-6 items-center justify-center rounded text-text-dim hover:bg-white/10 hover:text-text-main", "data-testid": `contextos-msg-${index}-copy`, children: copyState === 'done' ? _jsx(Check, { className: "h-3.5 w-3.5 text-status-success" }) : _jsx(Copy, { className: "h-3.5 w-3.5" }) })] }), _jsxs("div", { className: "px-3 py-2", children: [message.tool_calls && message.tool_calls.length > 0 && (_jsx("div", { className: "mb-2 space-y-1", children: message.tool_calls.map((tc, ti) => {
                            const raw = tc.function?.arguments;
                            const pretty = raw ? prettyJsonOrNull(raw) : null;
                            return (_jsxs("div", { className: "space-y-1", children: [_jsxs("div", { className: "flex items-center gap-1.5 rounded bg-black/20 px-2 py-1", children: [_jsx(Wrench, { className: "h-3 w-3 text-status-info" }), _jsx("span", { className: "font-mono text-[10px] text-status-info", children: tc.function?.name ?? tc.type ?? 'tool_call' }), raw && !pretty && (_jsx("span", { className: "truncate font-mono text-[9px] text-text-dim", title: raw, children: raw.slice(0, 60) })), pretty && (_jsxs("span", { "data-testid": `contextos-msg-${index}-toolcall-${ti}-formatted`, className: "inline-flex items-center gap-0.5 rounded bg-status-success/10 px-1 py-0.5 text-[9px] text-status-success", children: [_jsx(Check, { className: "h-2.5 w-2.5" }), "\u5DF2\u683C\u5F0F\u5316"] }))] }), pretty && (_jsx(CodeBlock, { segment: { kind: 'fence', lang: 'json', body: pretty }, expanded: expanded }))] }, ti));
                        }) })), renderedBody, message.tool_call_id && (_jsxs("div", { className: "mt-1 flex items-center gap-1 text-[9px] text-text-dim", children: [_jsx(Hash, { className: "h-3 w-3" }), "tool_call_id: ", message.tool_call_id] })), message.name && (_jsxs("div", { className: "mt-1 text-[9px] text-text-dim", children: ["name: ", message.name] }))] })] }));
}
const MessageCard = memo(MessageCardBase);
// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------
function LoadingState() {
    return (_jsxs("div", { className: "flex flex-col items-center justify-center gap-3 py-12", "data-testid": "contextos-viewer-loading", role: "status", "aria-live": "polite", "aria-busy": "true", children: [_jsx(Loader2, { className: "h-6 w-6 animate-spin text-accent-secondary", "aria-hidden": "true" }), _jsx("span", { className: "text-sm text-text-muted", children: "\u6B63\u5728\u52A0\u8F7D\u4E0A\u4E0B\u6587\u2026" })] }));
}
function ErrorState({ message, onRetry }) {
    return (_jsxs("div", { className: "flex flex-col items-center justify-center gap-3 py-10", "data-testid": "contextos-viewer-error", role: "alert", "aria-live": "assertive", children: [_jsx(AlertCircle, { className: "h-6 w-6 text-status-error", "aria-hidden": "true" }), _jsx("span", { className: "text-sm text-status-error", children: "\u52A0\u8F7D\u5931\u8D25" }), _jsx("span", { className: "max-w-xs text-center text-[11px] text-text-dim", children: message }), _jsx("button", { type: "button", onClick: onRetry, className: "mt-1 rounded-md bg-accent-secondary/15 px-3 py-1 text-[11px] text-accent-secondary hover:bg-accent-secondary/25 transition-colors", children: "\u91CD\u8BD5" })] }));
}
function EmptyState({ reason, testId, children }) {
    return (_jsxs("div", { className: "flex flex-col items-center justify-center gap-2 py-10", "data-testid": testId, role: "status", "aria-live": "polite", children: [_jsx(MessageSquare, { className: "h-6 w-6 text-text-dim", "aria-hidden": "true" }), _jsx("span", { className: "text-sm text-text-muted", children: reason }), children] }));
}
function ContextMissingState({ details }) {
    const searched = Array.isArray(details?.searched_paths) ? details.searched_paths : [];
    return (_jsxs(EmptyState, { reason: "\u5B8C\u6574\u4E0A\u4E0B\u6587\u5FEB\u7167\u4E0D\u53EF\u7528\uFF1A\u78C1\u76D8\u4E2D\u672A\u627E\u5230\u8BE5 hash\uFF0C\u53EF\u80FD\u5DF2\u88AB\u6E05\u7406\u6216\u6765\u81EA\u65E7\u8FD0\u884C\u4E8B\u4EF6", testId: "contextos-viewer-context-missing", children: [details?.workspace && (_jsxs("div", { className: "max-w-lg truncate rounded bg-black/20 px-2 py-1 font-mono text-[10px] text-text-dim", title: details.workspace, children: ["workspace: ", details.workspace] })), searched.length > 0 && (_jsxs("div", { className: "mt-1 w-full max-w-lg space-y-1 rounded border border-white/[0.06] bg-black/20 p-2 text-left", children: [_jsxs("div", { className: "font-mono text-[10px] text-text-dim", children: ["\u5DF2\u68C0\u67E5 ", searched.length, " \u4E2A\u5B58\u50A8\u4F4D\u7F6E"] }), searched.slice(0, 3).map((item, index) => (_jsxs("div", { className: "min-w-0 rounded bg-white/[0.03] px-2 py-1", children: [_jsx("div", { className: "font-mono text-[9px] text-accent-secondary", children: item.source || 'unknown' }), _jsx("div", { className: "truncate font-mono text-[9px] text-text-dim", title: item.context_path || '', children: item.context_path || 'n/a' })] }, `${item.source ?? 'source'}-${index}`)))] }))] }));
}
function FinalRequestPanel({ payload, loading, error, onRetry, }) {
    if (loading) {
        return (_jsxs("div", { className: "flex items-center gap-2 rounded border border-white/[0.06] bg-black/20 p-3 text-[11px] text-text-dim", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin", "aria-hidden": "true" }), "\u6B63\u5728\u8BFB\u53D6\u6700\u7EC8 provider request\u2026"] }));
    }
    if (error) {
        return (_jsxs("div", { className: "rounded border border-amber-500/20 bg-amber-500/10 p-3 text-[11px] text-amber-200", "data-testid": "contextos-final-request-unavailable", children: [_jsxs("div", { className: "flex items-center gap-2 font-medium", children: [_jsx(AlertCircle, { className: "h-3.5 w-3.5", "aria-hidden": "true" }), "\u6700\u7EC8 provider request \u8BC1\u636E\u4E0D\u53EF\u7528"] }), _jsx("div", { className: "mt-1 text-amber-100/80", children: error.message || error.code || 'unknown error' }), error.code && _jsxs("div", { className: "mt-1 font-mono text-[10px] text-amber-100/60", children: ["code: ", error.code] }), _jsx("button", { type: "button", onClick: onRetry, className: "mt-2 rounded bg-amber-400/15 px-2 py-1 text-[10px] text-amber-100 hover:bg-amber-400/25", children: "\u91CD\u8BD5" })] }));
    }
    if (!payload) {
        return _jsx(EmptyState, { reason: "\u6700\u7EC8 provider request \u5C1A\u672A\u52A0\u8F7D" });
    }
    const audit = payload.final_request_context_audit ?? {};
    const tokenEstimate = typeof audit.final_request_token_estimate === 'number' ? audit.final_request_token_estimate : null;
    const toolSchemaCount = typeof audit.tool_schema_count === 'number' ? audit.tool_schema_count : payload.tools?.length;
    const coverage = isRecord(audit.coverage) ? audit.coverage : {};
    const missingCoverage = isRecord(audit.context_quality) && Array.isArray(audit.context_quality.missing_coverage)
        ? audit.context_quality.missing_coverage
        : [];
    return (_jsxs("div", { className: "space-y-3", "data-testid": "contextos-final-request-panel", children: [_jsx("div", { className: "grid gap-2 sm:grid-cols-2", children: [
                    ['role', payload.role || '—'],
                    ['model', payload.model || '—'],
                    ['provider', payload.provider_id || payload.provider_type || '—'],
                    ['tools', String(toolSchemaCount ?? 0)],
                    ['tokens', tokenEstimate === null ? '—' : tokenEstimate.toLocaleString()],
                    ['messages', String(payload.message_count ?? 0)],
                ].map(([label, value]) => (_jsxs("div", { className: "rounded border border-white/[0.06] bg-black/20 px-2 py-1.5", children: [_jsx("div", { className: "text-[9px] uppercase tracking-wide text-text-dim", children: label }), _jsx("div", { className: "mt-0.5 truncate font-mono text-[11px] text-text-main", title: value, children: value })] }, label))) }), Object.keys(coverage).length > 0 && (_jsxs("div", { className: "rounded border border-white/[0.06] bg-black/20 p-2", children: [_jsx("div", { className: "mb-2 text-[10px] font-medium text-text-muted", children: "Coverage Flags" }), _jsx("div", { className: "flex flex-wrap gap-1.5", children: Object.entries(coverage).map(([key, ok]) => (_jsxs("span", { className: cn('rounded px-1.5 py-0.5 font-mono text-[9px]', ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'), children: [ok ? 'PASS' : 'MISS', " ", key] }, key))) }), missingCoverage.length > 0 && (_jsxs("div", { className: "mt-2 text-[10px] text-red-300", children: ["missing: ", missingCoverage.map((item) => String(item)).join(', ')] }))] })), _jsxs("div", { children: [_jsxs("div", { className: "mb-1 flex items-center gap-1 text-[10px] font-medium text-text-muted", children: [_jsx(FileText, { className: "h-3 w-3", "aria-hidden": "true" }), "provider_request"] }), _jsx(CodeBlock, { segment: { kind: 'fence', lang: 'json', body: prettyJson(payload.provider_request) }, expanded: true })] }), _jsxs("div", { children: [_jsxs("div", { className: "mb-1 flex items-center gap-1 text-[10px] font-medium text-text-muted", children: [_jsx(Wrench, { className: "h-3 w-3", "aria-hidden": "true" }), "final_request_context_audit"] }), _jsx(CodeBlock, { segment: { kind: 'fence', lang: 'json', body: prettyJson(payload.final_request_context_audit) }, expanded: true })] })] }));
}
function GroupSection({ role, count, totalTokens, children }) {
    return (_jsx("section", { "data-testid": `contextos-group-${role}`, "data-role": role, className: "rounded-lg border border-white/[0.05] bg-white/[0.015]", children: _jsxs("details", { open: true, className: "group", children: [_jsxs("summary", { className: "flex cursor-pointer items-center gap-2 px-3 py-2 text-[11px] text-text-muted hover:bg-white/[0.03]", children: [_jsx(ChevronRight, { className: "h-3.5 w-3.5 transition-transform group-open:rotate-90" }), roleIcon(role), _jsx("span", { className: "font-semibold text-text-main", children: roleLabel(role) }), _jsx("span", { className: "rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim", children: count }), _jsxs("span", { className: "ml-auto rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim", children: ["~", totalTokens, " tok"] })] }), _jsx("div", { className: "space-y-2 p-2", children: children })] }) }));
}
// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export function ContextViewerModal({ contextSnapshotRef, roleId, onClose, workspace, workerId }) {
    const [content, setContent] = useState(null);
    const [finalRequest, setFinalRequest] = useState(null);
    const [loading, setLoading] = useState(false);
    const [finalRequestLoading, setFinalRequestLoading] = useState(false);
    const [error, setError] = useState(null);
    const [finalRequestError, setFinalRequestError] = useState(null);
    const [contextMissing, setContextMissing] = useState(false);
    const [contextMissingDetails, setContextMissingDetails] = useState(null);
    // When the backend returns 403 WORKSPACE_FORBIDDEN we surface a localised
    // "other workspace" empty-state instead of a generic error banner.  The
    // advisory ACL only fires when the caller explicitly names a different
    // workspace via the ContextOS workspace selector, so this is opt-in —
    // single-tenant desktop flows never see it.
    const [workspaceForbidden, setWorkspaceForbidden] = useState(false);
    const [activeTab, setActiveTab] = useState('messages');
    const [search, setSearch] = useState('');
    const [groupByRole, setGroupByRole] = useState(false);
    const [allExpanded, setAllExpanded] = useState(null);
    const [globalCopyState, setGlobalCopyState] = useState('idle');
    const [perMessageCopy, setPerMessageCopy] = useState(null);
    const groupRefs = useRef({});
    const containerRef = useRef(null);
    const titleId = 'contextos-viewer-title';
    const descriptionId = 'contextos-viewer-description';
    const buildWorkspaceSuffix = useCallback(() => {
        const params = new URLSearchParams();
        const workspaceToken = typeof workspace === 'string' ? workspace.trim() : '';
        if (workspaceToken) {
            params.set('workspace', workspaceToken);
        }
        const suffix = params.toString();
        return suffix ? `?${suffix}` : '';
    }, [workspace]);
    const fetchFinalRequest = useCallback(async (signal) => {
        if (!contextSnapshotRef || !CONTEXT_SNAPSHOT_REF_RE.test(contextSnapshotRef)) {
            setFinalRequest(null);
            setFinalRequestLoading(false);
            setFinalRequestError(null);
            return;
        }
        setFinalRequestLoading(true);
        setFinalRequestError(null);
        try {
            const res = await apiFetch(`/v2/context/${contextSnapshotRef}/final-request${buildWorkspaceSuffix()}`);
            if (signal?.aborted)
                return;
            if (!res.ok) {
                let errorPayload = null;
                try {
                    errorPayload = (await res.json());
                }
                catch {
                    errorPayload = null;
                }
                const detail = errorPayload?.detail ?? errorPayload?.error;
                setFinalRequest(null);
                setFinalRequestError({
                    code: detail?.code || `HTTP_${res.status}`,
                    message: detail?.message || `HTTP ${res.status}`,
                });
                return;
            }
            const normalized = normalizeFinalProviderRequestPayload(await res.json());
            if (signal?.aborted)
                return;
            if (!normalized) {
                setFinalRequest(null);
                setFinalRequestError({
                    code: 'INVALID_FINAL_PROVIDER_REQUEST_AUDIT',
                    message: 'Final provider request audit payload is invalid.',
                });
                return;
            }
            setFinalRequest(normalized);
            setFinalRequestError(null);
        }
        catch (e) {
            if (e?.name === 'AbortError')
                return;
            setFinalRequest(null);
            setFinalRequestError({ code: 'FINAL_REQUEST_FETCH_FAILED', message: String(e) });
        }
        finally {
            if (!signal?.aborted) {
                setFinalRequestLoading(false);
            }
        }
    }, [buildWorkspaceSuffix, contextSnapshotRef]);
    const fetchContext = useCallback(async (signal) => {
        if (!contextSnapshotRef)
            return;
        if (!CONTEXT_SNAPSHOT_REF_RE.test(contextSnapshotRef)) {
            setContent(null);
            setFinalRequest(null);
            setLoading(false);
            setFinalRequestLoading(false);
            setError(null);
            setFinalRequestError(null);
            setContextMissing(true);
            setContextMissingDetails(null);
            setWorkspaceForbidden(false);
            return;
        }
        setLoading(true);
        setFinalRequestLoading(false);
        setFinalRequest(null);
        setError(null);
        setFinalRequestError(null);
        setContextMissing(false);
        setContextMissingDetails(null);
        setWorkspaceForbidden(false);
        try {
            const res = await apiFetch(`/v2/context/${contextSnapshotRef}${buildWorkspaceSuffix()}`);
            // 若请求在 await 期间被取消，response 解析也无意义。
            if (signal?.aborted)
                return;
            if (res.status === 403) {
                // Detect WORKSPACE_FORBIDDEN from the structured detail payload so
                // any other 403 still surfaces as a normal error.
                let isWorkspace = false;
                try {
                    const body = (await res.json());
                    isWorkspace = body?.detail?.code === 'WORKSPACE_FORBIDDEN';
                }
                catch {
                    isWorkspace = false;
                }
                if (isWorkspace) {
                    setWorkspaceForbidden(true);
                    return;
                }
                throw new Error(`HTTP ${res.status}`);
            }
            if (res.status === 404) {
                let isContextMissing = false;
                try {
                    const body = (await res.json());
                    isContextMissing =
                        body?.detail?.code === 'CONTEXT_NOT_FOUND' ||
                            body?.error?.code === 'CONTEXT_NOT_FOUND';
                    setContextMissingDetails(body?.detail?.details ?? body?.error?.details ?? null);
                }
                catch {
                    isContextMissing = false;
                    setContextMissingDetails(null);
                }
                if (isContextMissing) {
                    setContextMissing(true);
                    return;
                }
            }
            if (!res.ok) {
                const text = await res.text().catch(() => '');
                throw new Error(`HTTP ${res.status}${text ? ': ' + text : ''}`);
            }
            const data = normalizeViewModelPayload(await res.json());
            if (signal?.aborted)
                return;
            setContent(data);
            void fetchFinalRequest(signal);
        }
        catch (e) {
            // AbortError 静默：组件卸载或 ref 变化导致的取消不应作为错误呈现。
            if (e?.name === 'AbortError')
                return;
            setError(String(e));
        }
        finally {
            if (!signal?.aborted) {
                setLoading(false);
            }
        }
    }, [buildWorkspaceSuffix, contextSnapshotRef, fetchFinalRequest]);
    useEffect(() => {
        if (!contextSnapshotRef)
            return;
        // 每次 ref 变化（或组件挂载）创建新的 AbortController，清理时 abort。
        const controller = new AbortController();
        void fetchContext(controller.signal);
        return () => {
            controller.abort();
        };
    }, [contextSnapshotRef, fetchContext]);
    // Close on Escape / focus trap
    useEffect(() => {
        function handleKeyDown(e) {
            if (e.key === 'Escape') {
                e.stopPropagation();
                onClose();
                return;
            }
            // 焦点陷阱：Tab / Shift+Tab 在弹窗内首尾可聚焦元素间循环。
            if (e.key === 'Tab' && containerRef.current) {
                const focusables = getFocusableElements(containerRef.current);
                if (focusables.length === 0) {
                    e.preventDefault();
                    return;
                }
                const first = focusables[0];
                const last = focusables[focusables.length - 1];
                const active = document.activeElement;
                // 若当前焦点不在弹窗内（被前置 body 锁丢失等），回收到第一个可聚焦元素。
                if (!active || !containerRef.current.contains(active)) {
                    e.preventDefault();
                    first.focus();
                    return;
                }
                if (e.shiftKey && active === first) {
                    e.preventDefault();
                    last.focus();
                }
                else if (!e.shiftKey && active === last) {
                    e.preventDefault();
                    first.focus();
                }
            }
        }
        document.addEventListener('keydown', handleKeyDown);
        return () => document.removeEventListener('keydown', handleKeyDown);
    }, [onClose]);
    // 焦点进入弹窗：挂载时把焦点移入内部首个可聚焦元素。
    // 焦点恢复：卸载时把焦点还给打开弹窗前 activeElement。
    useEffect(() => {
        const previouslyFocused = document.activeElement;
        // 等到 microtask 让 dialog 渲染完毕，再投放焦点。
        const focusTimer = window.setTimeout(() => {
            const container = containerRef.current;
            if (!container)
                return;
            const focusables = getFocusableElements(container);
            if (focusables.length > 0) {
                focusables[0].focus();
            }
            else {
                // 容器本身兜底可聚焦。
                container.setAttribute('tabindex', '-1');
                container.focus();
            }
        }, 0);
        // Body 滚动锁：避免背景跟随内容滚动；记录原值以便还原。
        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            window.clearTimeout(focusTimer);
            document.body.style.overflow = previousOverflow;
            // 焦点恢复：仅在原本聚焦元素仍然挂载时归还（防御组件已卸载的边界）。
            if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
                try {
                    previouslyFocused.focus();
                }
                catch {
                    // 静默：DOM 已卸载 / 不可聚焦。
                }
            }
        };
    }, []);
    // 过滤后的消息列表
    const filteredMessages = useMemo(() => {
        if (!content)
            return [];
        const q = search.trim().toLowerCase();
        if (!q)
            return content.messages;
        return content.messages.filter((m) => {
            if ((m.content ?? '').toLowerCase().includes(q))
                return true;
            if ((m.name ?? '').toLowerCase().includes(q))
                return true;
            if ((m.tool_call_id ?? '').toLowerCase().includes(q))
                return true;
            if (m.tool_calls) {
                for (const tc of m.tool_calls) {
                    if ((tc.function?.name ?? '').toLowerCase().includes(q))
                        return true;
                    if ((tc.function?.arguments ?? '').toLowerCase().includes(q))
                        return true;
                }
            }
            return false;
        });
    }, [content, search]);
    // 是否存在超长消息（决定 expand-all/collapse-all 是否显示）
    const hasLongMessage = useMemo(() => {
        if (!content)
            return false;
        return content.messages.some((m) => (m.content ?? '').length > 800);
    }, [content]);
    // 按角色聚合 token + 数量（用于 sticky 锚点导航）
    const roleGroups = useMemo(() => {
        const map = new Map();
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
        if (!content)
            return;
        const ok = await writeClipboard(buildFullMarkdown(content));
        if (ok) {
            setGlobalCopyState('done');
            setTimeout(() => setGlobalCopyState('idle'), 2000);
        }
    }, [content]);
    const copyMessage = useCallback(async (idx, markdown) => {
        const ok = await writeClipboard(markdown);
        if (ok) {
            setPerMessageCopy(idx);
            setTimeout(() => setPerMessageCopy((prev) => (prev === idx ? null : prev)), 2000);
        }
    }, []);
    const scrollToGroup = useCallback((role) => {
        const el = groupRefs.current[role];
        if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }, []);
    const showCopyAll = activeTab === 'messages' && !!content && content.messages.length > 0;
    // expand-all 控制（作用于 CodeBlock / PlainTextSegment 内部 expand）；
    // 通过 useState 上提 → 一次性下发 props；当前实现把 expanded 完全交给子组件本地，
    // allExpanded 仅作 UI 切换指示（不强制子组件遵循，以避免破坏 per-card expand 行为）。
    void allExpanded;
    return (_jsx("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm", onClick: (e) => {
            if (e.target === e.currentTarget)
                onClose();
        }, role: "dialog", "aria-modal": "true", "aria-labelledby": titleId, "aria-describedby": descriptionId, "data-testid": "contextos-viewer-modal", children: _jsxs("div", { ref: containerRef, className: "flex max-h-[85vh] w-[92vw] max-w-3xl flex-col rounded-xl border border-white/[0.08] bg-bg-panel shadow-2xl", children: [_jsxs("header", { className: "flex items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-3", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx(Bot, { className: "h-4 w-4 shrink-0 text-accent-secondary", "aria-hidden": "true" }), _jsxs("h2", { id: titleId, className: "truncate text-sm font-semibold text-text-main", children: ["\u5B8C\u6574\u4E0A\u4E0B\u6587 \u00B7 ", roleId.toUpperCase()] }), workerId && (_jsxs("span", { className: "flex items-center gap-1 rounded bg-accent/15 px-1.5 py-0.5 font-mono text-[9px] text-accent", "data-testid": "contextos-viewer-worker-chip", title: `该上下文来自 worker ${workerId}`, children: [_jsx(Cpu, { className: "h-3 w-3", "aria-hidden": "true" }), "worker ", workerId] })), contextSnapshotRef && (_jsxs("span", { className: "flex items-center gap-1 rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim", children: [_jsx(Hash, { className: "h-3 w-3" }), contextSnapshotRef] }))] }), _jsx("button", { type: "button", onClick: onClose, className: "flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-dim hover:bg-white/5 hover:text-text-main transition-colors", "aria-label": "\u5173\u95ED", "data-testid": "contextos-viewer-close", children: _jsx(X, { className: "h-4 w-4" }) })] }), content && (_jsx("div", { className: "flex items-center gap-1 border-b border-white/[0.05] bg-bg-panel/60 px-4 py-2", children: [
                        { key: 'messages', label: '上下文消息', count: content.message_count },
                        { key: 'final-request', label: '最终请求', count: finalRequest?.tools?.length ?? 0 },
                    ].map((tab) => (_jsxs("button", { type: "button", onClick: () => setActiveTab(tab.key), "aria-pressed": activeTab === tab.key, "data-testid": `contextos-viewer-tab-${tab.key}`, className: cn('rounded-md px-2 py-1 text-[11px] transition-colors', activeTab === tab.key
                            ? 'bg-accent-secondary/15 text-accent-secondary'
                            : 'text-text-muted hover:bg-white/5 hover:text-text-main'), children: [tab.label, tab.key === 'messages' && (_jsx("span", { className: "ml-1 font-mono text-[9px] opacity-70", children: tab.count }))] }, tab.key))) })), activeTab === 'messages' && content && content.messages.length > 0 && (_jsxs("div", { className: "flex flex-wrap items-center gap-2 border-b border-white/[0.05] bg-bg-panel/40 px-4 py-2", children: [_jsxs("div", { className: "flex flex-1 items-center gap-1 rounded-md border border-white/[0.06] bg-black/20 px-2 py-1", children: [_jsx(Search, { className: "h-3.5 w-3.5 text-text-dim", "aria-hidden": "true" }), _jsx("input", { type: "text", value: search, onChange: (e) => setSearch(e.target.value), placeholder: "\u641C\u7D22\u6D88\u606F\u5185\u5BB9 / \u5DE5\u5177\u8C03\u7528\u2026", "aria-label": "\u641C\u7D22\u6D88\u606F", className: "flex-1 bg-transparent text-[11px] text-text-main outline-none placeholder:text-text-dim", "data-testid": "contextos-viewer-search" }), search && (_jsxs("span", { className: "rounded bg-black/30 px-1.5 py-0.5 font-mono text-[9px] text-text-dim", "data-testid": "contextos-viewer-search-count", "aria-live": "polite", children: [filteredMessages.length, " / ", content.messages.length, " \u547D\u4E2D"] }))] }), _jsxs("button", { type: "button", onClick: () => setGroupByRole((v) => !v), "aria-pressed": groupByRole, className: cn('flex items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors', groupByRole
                                ? 'bg-accent-secondary/15 text-accent-secondary'
                                : 'text-text-muted hover:bg-white/5 hover:text-text-main'), "data-testid": "contextos-viewer-group-toggle", title: "\u6309\u89D2\u8272\u6298\u53E0\u5206\u7EC4", children: [_jsx(Layers, { className: "h-3.5 w-3.5", "aria-hidden": "true" }), "\u5206\u7EC4"] }), _jsxs("button", { type: "button", onClick: () => setAllExpanded((v) => (v === true ? false : true)), "aria-pressed": allExpanded === true, className: cn('flex items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors', allExpanded
                                ? 'bg-accent-secondary/15 text-accent-secondary'
                                : 'text-text-muted hover:bg-white/5 hover:text-text-main'), "data-testid": "contextos-viewer-expand-toggle", title: allExpanded ? '全部收起' : '全部展开', children: [allExpanded ? _jsx(Minimize2, { className: "h-3.5 w-3.5", "aria-hidden": "true" }) : _jsx(Maximize2, { className: "h-3.5 w-3.5", "aria-hidden": "true" }), allExpanded ? '收起' : '展开'] }), _jsxs("button", { type: "button", onClick: () => void copyAll(), className: cn('flex items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors', globalCopyState === 'done'
                                ? 'bg-status-success/15 text-status-success'
                                : 'bg-accent-secondary/15 text-accent-secondary hover:bg-accent-secondary/25'), "data-testid": "contextos-viewer-copy-all", title: "\u590D\u5236\u5B8C\u6574 Markdown", "aria-label": "\u590D\u5236\u5B8C\u6574\u4E0A\u4E0B\u6587\u4E3A Markdown", children: [globalCopyState === 'done' ? _jsx(Check, { className: "h-3.5 w-3.5", "aria-hidden": "true" }) : _jsx(Copy, { className: "h-3.5 w-3.5", "aria-hidden": "true" }), "\u590D\u5236\u5168\u6587"] })] })), content && groupByRole && roleGroups.size > 0 && (_jsxs("div", { className: "sticky top-0 z-10 flex items-center gap-1 overflow-x-auto border-b border-white/[0.05] bg-bg-panel/70 px-4 py-1.5 backdrop-blur", "data-testid": "contextos-viewer-anchor-nav", children: [_jsx(Filter, { className: "h-3 w-3 shrink-0 text-text-dim", "aria-hidden": "true" }), Array.from(roleGroups.entries()).map(([role, info]) => (_jsxs("button", { type: "button", onClick: () => scrollToGroup(role), className: cn('flex shrink-0 items-center gap-1 rounded-md px-2 py-0.5 text-[10px] transition-colors', 'text-text-muted hover:bg-white/5 hover:text-text-main'), "data-testid": `contextos-viewer-anchor-${role}`, "aria-label": `跳转到 ${roleLabel(role)} 分组`, children: [roleShortLabel(role), " (", info.count, ")"] }, role)))] })), _jsx("div", { className: "min-h-0 flex-1 overflow-auto p-4", "data-testid": "contextos-viewer-body", children: !contextSnapshotRef ? (_jsx(EmptyState, { reason: "\u5B8C\u6574\u4E0A\u4E0B\u6587\u672A\u91C7\u96C6\uFF08\u9700\u540E\u7AEF\u5F00\u542F\uFF09", testId: "contextos-viewer-empty" })) : loading ? (_jsx(LoadingState, {})) : workspaceForbidden ? (_jsx(EmptyState, { reason: "\u8BE5\u5FEB\u7167\u5C5E\u4E8E\u5176\u4ED6\u5DE5\u4F5C\u533A\uFF0C\u8BF7\u5207\u6362\u5230\u5BF9\u5E94\u5DE5\u4F5C\u533A\u540E\u518D\u67E5\u770B", testId: "contextos-viewer-workspace-forbidden" })) : contextMissing ? (_jsx(ContextMissingState, { details: contextMissingDetails })) : error ? (_jsx(ErrorState, { message: error, onRetry: fetchContext })) : content ? (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { id: descriptionId, className: "flex flex-wrap items-center gap-2 text-[10px] text-text-dim", children: [content.call_id && (_jsxs("span", { className: "flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5", "data-testid": "contextos-viewer-meta-call", children: [_jsx(Hash, { className: "h-3 w-3", "aria-hidden": "true" }), "call: ", content.call_id] })), content.trace_id && (_jsxs("span", { className: "flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5", "data-testid": "contextos-viewer-meta-trace", children: [_jsx(Hash, { className: "h-3 w-3", "aria-hidden": "true" }), "trace: ", content.trace_id] })), _jsxs("span", { className: "flex items-center gap-1 rounded bg-black/20 px-1.5 py-0.5", "data-testid": "contextos-viewer-meta-stored", children: [_jsx(Clock, { className: "h-3 w-3", "aria-hidden": "true" }), formatStoredAt(content.stored_at)] }), _jsxs("span", { className: "rounded bg-black/20 px-1.5 py-0.5", "data-testid": "contextos-viewer-meta-count", children: [content.message_count, " \u6761\u6D88\u606F \u00B7 ", content.total_chars.toLocaleString(), " \u5B57\u7B26"] })] }), activeTab === 'final-request' ? (_jsx(FinalRequestPanel, { payload: finalRequest, loading: finalRequestLoading, error: finalRequestError, onRetry: () => void fetchFinalRequest() })) : content.messages.length === 0 ? (_jsx(EmptyState, { reason: "\u4E0A\u4E0B\u6587\u6587\u4EF6\u65E0\u6D88\u606F\u5185\u5BB9" })) : filteredMessages.length === 0 ? (_jsx(EmptyState, { reason: `无匹配消息（搜索词：${search}）` })) : groupByRole ? (_jsx("div", { className: "space-y-3", children: Array.from(roleGroups.entries()).map(([role, info]) => (_jsx("div", { ref: (el) => {
                                        groupRefs.current[role] = el;
                                    }, children: _jsx(GroupSection, { role: role, count: info.count, totalTokens: info.tokens, children: info.indices
                                            .map((originalIdx) => filteredMessages[originalIdx])
                                            .filter((m) => Boolean(m))
                                            .map((msg, localIdx) => (_jsx(MessageCard, { message: msg, index: filteredMessages.indexOf(msg), onCopyMessage: (idx, md) => void copyMessage(idx, md), copyState: perMessageCopy === filteredMessages.indexOf(msg) ? 'done' : 'idle' }, localIdx))) }) }, role))) })) : (_jsx("div", { className: "space-y-2", children: filteredMessages.map((msg, index) => (_jsx(MessageCard, { message: msg, index: index, onCopyMessage: (idx, md) => void copyMessage(idx, md), copyState: perMessageCopy === index ? 'done' : 'idle' }, index))) }))] })) : null }), _jsxs("footer", { className: "flex items-center justify-between border-t border-white/[0.06] px-4 py-2", children: [_jsx("span", { className: "text-[10px] text-text-dim", children: content ? `schema v${content.schema_version}` : '—' }), _jsxs("div", { className: "flex items-center gap-2", children: [showCopyAll && (_jsx("button", { type: "button", onClick: () => void copyAll(), className: "rounded-md bg-accent-secondary/15 px-3 py-1 text-[11px] text-accent-secondary hover:bg-accent-secondary/25 transition-colors", "data-testid": "contextos-viewer-footer-copy", children: "\u590D\u5236\u5168\u6587 Markdown" })), _jsx("button", { type: "button", onClick: onClose, className: "rounded-md bg-white/5 px-3 py-1 text-[11px] text-text-muted hover:bg-white/10 transition-colors", children: "\u5173\u95ED" })] })] })] }) }));
}
