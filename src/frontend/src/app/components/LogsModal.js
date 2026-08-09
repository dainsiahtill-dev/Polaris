import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { RefreshCw, X, FileText, Activity, AlertTriangle, TerminalSquare, Wrench } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch } from '@/api';
import { useConnectionState, useMessageHandler, useTransportActions } from '@/runtime/transport';
import { CodexCliStreamParser, parseCodexCliLines, stripLlmTags } from '@/app/components/logs/CodexCliStreamParser';
import { LlmEventCard } from '@/app/components/logs/LlmEventCard';
import { parseLlmEventLine, parseLlmEventLines } from '@/app/components/logs/LlmEventTypes';
import { PolarisTerminalRenderer } from '@/app/components/PolarisTerminalRenderer';
import { sanitizeHtml } from '@/app/utils/xssSanitizer';
const DEFAULT_LOG_SOURCES = [
    { id: 'pm-subprocess', label: 'PM 子进程', path: 'runtime/logs/pm.process.log', channel: 'process' },
    { id: 'pm-report', label: 'PM 禀报', path: 'runtime/results/pm.report.md', channel: '' },
    { id: 'pm-log', label: 'PM 纪要（jsonl）', path: 'runtime/events/pm.events.jsonl', channel: '' },
    { id: 'director', label: 'Director 子进程', path: 'runtime/logs/director.process.log', channel: 'process' },
    { id: 'planner', label: '谋划稿', path: 'runtime/results/planner.output.md', channel: '' },
    { id: 'ollama', label: 'Ollama', path: 'runtime/results/director_llm.output.md', channel: 'llm' },
    { id: 'qa', label: '审校', path: 'runtime/results/qa.review.md', channel: '' },
    { id: 'runlog', label: '运行纪要', path: 'runtime/logs/director.runlog.md', channel: 'process' },
];
function SmartText({ text }) {
    const max = 400;
    if (text.length <= max) {
        return _jsx("div", { className: "text-xs text-gray-200 whitespace-pre-wrap", children: text });
    }
    return (_jsxs("details", { className: "text-xs text-gray-200 whitespace-pre-wrap", children: [_jsx("summary", { className: "cursor-pointer text-gray-400", children: "\u5C55\u5F00\u5185\u5BB9" }), text] }));
}
const ROLE_BADGE_STYLES = {
    user: 'border-blue-500/40 bg-blue-500/20 text-blue-200',
    thinking: 'border-slate-500/40 bg-slate-500/20 text-slate-200',
    exec: 'border-amber-500/40 bg-amber-500/20 text-amber-200',
};
function RoleBadge({ role }) {
    return (_jsx("span", { className: `inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${ROLE_BADGE_STYLES[role]}`, children: role }));
}
function escapeHtml(source) {
    return source
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
function buildMarkupSrcDoc(kind, source) {
    const trimmed = (source || '').trim();
    if (!trimmed)
        return '';
    if (kind === 'svg') {
        return `<!doctype html><html><head><meta charset="utf-8" /><style>html,body{margin:0;padding:0;background:#0f1117;color:#e5e7eb;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans SC','Source Han Sans SC','SimSun','SimHei',sans-serif;}a{color:#6ee7b7;}code,pre{font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;}</style></head><body style="display:flex;align-items:center;justify-content:center;min-height:100vh;"><div style="padding:14px;border:1px solid rgba(148,163,184,.18);border-radius:8px;background:rgba(15,17,23,.85);">${trimmed}</div></body></html>`;
    }
    if (kind !== 'html')
        return '';
    let headHtml = '';
    let bodyHtml = trimmed;
    let textContent = '';
    let hasRenderableElements = false;
    try {
        const parser = new DOMParser();
        const doc = parser.parseFromString(trimmed, 'text/html');
        doc.querySelectorAll('script').forEach((node) => node.remove());
        headHtml = doc.head ? doc.head.innerHTML : '';
        bodyHtml = doc.body ? doc.body.innerHTML : trimmed;
        textContent = (doc.body?.textContent || '').trim();
        hasRenderableElements = Boolean(doc.body?.querySelector('img,svg,canvas,video,iframe,object,embed,table,button,input,select,textarea,hr,ul,ol,li,blockquote'));
    }
    catch {
        headHtml = '';
        bodyHtml = trimmed;
        textContent = '';
        hasRenderableElements = false;
    }
    const normalizedText = textContent.replace(/\s+/g, ' ').trim();
    const visible = normalizedText.length > 0 || hasRenderableElements;
    const fallback = `<pre style="white-space:pre-wrap;word-break:break-word;margin:0;font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;font-size:12px;line-height:1.5;color:#e5e7eb;">${escapeHtml(trimmed)}</pre>`;
    const finalBody = visible ? sanitizeHtml(bodyHtml) : fallback;
    return `<!doctype html><html><head><meta charset="utf-8" /><base target="_blank" />${headHtml}<style>html,body{margin:0;padding:0;background:#0f1117;color:#e5e7eb;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans SC','Source Han Sans SC','SimSun','SimHei',sans-serif;}a{color:#6ee7b7;}a:hover{color:#a7f3d0;}code,pre{font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;}*{box-sizing:border-box;}hr{border:0;border-top:1px solid rgba(148,163,184,.22);}table{border-collapse:collapse;}td,th{border:1px solid rgba(148,163,184,.18);padding:6px 8px;}blockquote{border-left:3px solid rgba(148,163,184,.3);margin:8px 0;padding:6px 10px;background:rgba(15,17,23,.5);}img{max-width:100%;height:auto;} </style></head><body><div style="padding:14px;"><div style="border:1px solid rgba(148,163,184,.15);border-radius:8px;background:rgba(15,17,23,.85);padding:12px;min-height:100%;">${finalBody}</div></div></body></html>`;
}
function detectMarkupKind(source, pathHint) {
    if (!source)
        return null;
    const hint = (pathHint || '').toLowerCase();
    if (hint.endsWith('.vue'))
        return null;
    if (hint.endsWith('.svg'))
        return 'svg';
    if (hint.endsWith('.html') || hint.endsWith('.htm'))
        return 'html';
    if (hint.endsWith('.xml'))
        return 'xml';
    const trimmed = source.trim();
    if (!trimmed.startsWith('<'))
        return null;
    if (/^<!doctype\s+html/i.test(trimmed) || /<html[\s>]/i.test(trimmed))
        return 'html';
    if (/<svg[\s>]/i.test(trimmed))
        return 'svg';
    if (/^<\?xml/i.test(trimmed))
        return 'xml';
    if (/<\s*(div|span|p|a|img|table|tr|td|th|ul|ol|li|section|article|header|footer|main|nav|pre|code|h[1-6]|br|hr|input|button|form|label|textarea|select)\b/i.test(trimmed)) {
        return 'html';
    }
    if (typeof window !== 'undefined' && 'DOMParser' in window) {
        try {
            const parser = new DOMParser();
            const xml = parser.parseFromString(trimmed, 'text/xml');
            if (!xml.querySelector('parsererror')) {
                const root = (xml.documentElement?.tagName || '').toLowerCase();
                if (root && root !== 'html' && root !== 'svg')
                    return 'xml';
            }
        }
        catch {
            // ignore parse failures
        }
    }
    return null;
}
function MarkupCard({ title, source, kind, badge, meta, }) {
    const renderProbablyBlank = useMemo(() => {
        if (kind !== 'html')
            return false;
        if (/<\s*(img|svg|canvas|video|iframe|object|embed|table|button|input|select|textarea)\b/i.test(source)) {
            return false;
        }
        const textOnly = source
            .replace(/<!--[\s\S]*?-->/g, '')
            .replace(/<script[\s\S]*?<\/script>/gi, '')
            .replace(/<style[\s\S]*?<\/style>/gi, '')
            .replace(/<[^>]+>/g, '')
            .replace(/&nbsp;/gi, ' ')
            .trim();
        return textOnly.length === 0;
    }, [kind, source]);
    const [view, setView] = useState(() => {
        if (kind === 'xml')
            return 'tree';
        if (kind === 'html' && renderProbablyBlank)
            return 'source';
        return 'render';
    });
    const canRender = kind === 'html' || kind === 'svg';
    const canTree = kind === 'xml';
    const xmlTree = useMemo(() => {
        if (!canTree)
            return null;
        try {
            const parser = new DOMParser();
            const xml = parser.parseFromString(source, 'text/xml');
            if (xml.querySelector('parsererror'))
                return null;
            const root = xml.documentElement;
            return root ? buildXmlTree(root) : null;
        }
        catch {
            return null;
        }
    }, [canTree, source]);
    const srcDoc = useMemo(() => {
        if (!canRender)
            return '';
        return buildMarkupSrcDoc(kind, source);
    }, [canRender, kind, source]);
    return (_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400", children: [badge, _jsx("span", { children: title }), _jsx("span", { className: "rounded bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300 uppercase", children: kind }), meta ? _jsx("span", { className: "text-gray-500", children: meta }) : null] }), _jsxs("div", { className: "flex items-center gap-1 text-[10px]", children: [canRender ? (_jsx("button", { className: `rounded px-2 py-0.5 ${view === 'render' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'}`, onClick: () => setView('render'), children: "\u6E32\u67D3" })) : null, canTree ? (_jsx("button", { className: `rounded px-2 py-0.5 ${view === 'tree' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'}`, onClick: () => setView('tree'), children: "\u6811\u89C6\u56FE" })) : null, _jsx("button", { className: `rounded px-2 py-0.5 ${view === 'source' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'}`, onClick: () => setView('source'), children: "\u6E90\u6587" })] })] }), view === 'render' && canRender ? (_jsx("iframe", { className: "mt-2 h-60 w-full rounded border border-gray-700 bg-transparent", sandbox: "", srcDoc: srcDoc, title: title })) : view === 'tree' && canTree ? (xmlTree ? (_jsx("div", { className: "mt-2", children: _jsx(XmlTreeNodeView, { node: xmlTree, depth: 0 }) })) : (_jsx("pre", { className: "mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all", children: source || '(空)' }))) : (_jsx("pre", { className: "mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all", children: source || '(空)' }))] }));
}
function buildXmlTree(node) {
    if (node.nodeType === Node.TEXT_NODE || node.nodeType === Node.CDATA_SECTION_NODE) {
        const text = (node.textContent || '').trim();
        if (!text)
            return null;
        return { name: '#text', attributes: [], children: [], text };
    }
    if (node.nodeType !== Node.ELEMENT_NODE)
        return null;
    const el = node;
    const attributes = Array.from(el.attributes).map((attr) => [attr.name, attr.value]);
    const children = [];
    Array.from(el.childNodes).forEach((child) => {
        const childNode = buildXmlTree(child);
        if (childNode)
            children.push(childNode);
    });
    return {
        name: el.tagName,
        attributes,
        children,
    };
}
function XmlTreeNodeView({ node, depth }) {
    const isText = node.name === '#text';
    if (isText) {
        return (_jsx("div", { className: "ml-4 text-xs text-gray-300 italic whitespace-pre-wrap", children: node.text }));
    }
    const openByDefault = depth < 1;
    return (_jsxs("details", { open: openByDefault, className: "text-xs text-gray-200", children: [_jsxs("summary", { className: "cursor-pointer text-gray-200", children: [_jsxs("span", { className: "text-blue-200", children: ["<", node.name] }), node.attributes.length
                        ? node.attributes.map(([key, value]) => (_jsxs("span", { className: "ml-1 text-emerald-200", children: [key, "=\"", _jsx("span", { className: "text-gray-300", children: value }), "\""] }, key)))
                        : null, _jsx("span", { className: "text-blue-200", children: ">" })] }), _jsxs("div", { className: "ml-4 space-y-1", children: [node.children.map((child, idx) => (_jsx(XmlTreeNodeView, { node: child, depth: depth + 1 }, `${child.name}-${idx}`))), _jsxs("div", { className: "text-blue-200", children: ["</", node.name, ">"] })] })] }));
}
export function LogsModal({ isOpen, onClose, initialSourceId, runId, banner, onDismissBanner, }) {
    const bannerText = useMemo(() => {
        if (typeof banner === 'string') {
            return banner.trim();
        }
        if (banner == null)
            return '';
        try {
            return JSON.stringify(banner, null, 2);
        }
        catch {
            return String(banner);
        }
    }, [banner]);
    // If runId is provided, we map sources to the run directory
    const sources = useMemo(() => {
        if (!runId)
            return DEFAULT_LOG_SOURCES;
        return DEFAULT_LOG_SOURCES.map((s) => ({
            ...s,
            // PM logs are global, so we might want to keep them or point them to run specific if available
            // But typically run specific logs are:
            // - DIRECTOR_SUBPROCESS.log -> runtime/runs/<runId>/DIRECTOR_SUBPROCESS.log (if archived? or RUNLOG.md)
            // Actually loop-pm.py:1053 says: run_director_log = os.path.join(run_dir, "RUNLOG.md")
            // And director_subprocess_log is usually global but can be per-run if we want.
            // Let's look at loop-pm.py resolve logic.
            // For now, let's just map the ones we know exist in run dir.
            path: `runtime/runs/${runId}/${s.path.split('/').pop()}`,
        }));
    }, [runId]);
    const [active, setActive] = useState(sources[0].id);
    const [lines, setLines] = useState([]);
    const [mtime, setMtime] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [live, setLive] = useState(false);
    const [viewMode, setViewMode] = useState('smart');
    const [filter, setFilter] = useState('all');
    const [query, setQuery] = useState('');
    const [streamEvents, setStreamEvents] = useState([]);
    const parserRef = useRef(null);
    const activeSource = useMemo(() => sources.find((item) => item.id === active) || sources[0], [active, sources]);
    const LLM_CHANNEL_MAP = { 'pm-subprocess': 'llm', 'director': 'llm' };
    const llmChannel = LLM_CHANNEL_MAP[active] || '';
    const hasLlmChannel = !!llmChannel;
    const isHpSmart = active === 'runlog';
    const allowSmart = hasLlmChannel || isHpSmart;
    const allowJson = active === 'pm-log';
    const allowRaw = active !== 'pm-log';
    const [llmEvents, setLlmEvents] = useState([]);
    const llmSeenIds = useRef(new Set());
    const refresh = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await apiFetch(`/v2/files/read?path=${encodeURIComponent(activeSource.path)}&tail_lines=400`);
            if (!res.ok) {
                throw new Error('读取案牍失败');
            }
            const payload = (await res.json());
            setLines(payload.content ? payload.content.split('\n') : []);
            setMtime(payload.mtime || '');
        }
        catch (err) {
            setError(err instanceof Error ? err.message : '读取案牍失败');
            setLines([]);
            setMtime('');
        }
        finally {
            setLoading(false);
        }
    };
    useEffect(() => {
        if (!isOpen)
            return;
        refresh();
    }, [isOpen, active]);
    useEffect(() => {
        if (!isOpen)
            return;
        if (initialSourceId) {
            const exists = sources.some((item) => item.id === initialSourceId);
            setActive(exists ? initialSourceId : sources[0].id);
        }
    }, [isOpen, initialSourceId, sources]);
    useEffect(() => {
        if (!isOpen)
            return;
        if (active === 'pm-subprocess' || active === 'director' || active === 'runlog') {
            setViewMode('smart');
        }
        else if (active === 'pm-log') {
            setViewMode('json');
        }
        else {
            setViewMode('raw');
        }
    }, [isOpen, active]);
    const { connected: transportConnected } = useConnectionState();
    const { subscribeChannels } = useTransportActions();
    const { registerMessageHandler } = useMessageHandler();
    useEffect(() => {
        if (!isOpen)
            return;
        const channels = Array.from(new Set([activeSource.channel, llmChannel].filter(Boolean)));
        if (!transportConnected) {
            setLive(false);
            return;
        }
        if (channels.length === 0) {
            setLive(false);
            return;
        }
        setLive(true);
        const unsubscribe = subscribeChannels(channels.map((channel) => ({ channel, tailLines: 0 })));
        const unregister = registerMessageHandler((raw) => {
            if (!raw || typeof raw !== 'object')
                return;
            const ev = raw;
            const ch = String(ev.channel || '').trim();
            const kind = String(ev.kind || ev.type || '').trim().toLowerCase();
            const payload = (ev.payload && typeof ev.payload === 'object'
                ? ev.payload
                : null);
            const line = typeof ev.line === 'string' ? ev.line : null;
            const text = line
                || (payload && typeof payload.line === 'string' ? payload.line : '')
                || (payload ? JSON.stringify(payload) : '');
            if (activeSource.channel && ch === activeSource.channel) {
                if (kind === 'snapshot' && payload && Array.isArray(payload.lines)) {
                    setLines(payload.lines);
                    const parser = new CodexCliStreamParser();
                    payload.lines.forEach((line) => parser.feedLine(line));
                    parserRef.current = parser;
                    setStreamEvents([...parser.events]);
                }
                else if ((kind === 'line'
                    || kind === 'process_stream'
                    || kind === 'dialogue_event'
                    || kind === 'process_line')
                    && text) {
                    setLines((prev) => [...prev, text].slice(-1000));
                    if (!parserRef.current)
                        parserRef.current = new CodexCliStreamParser();
                    parserRef.current.feedLine(text);
                    setStreamEvents([...parserRef.current.events]);
                }
            }
            if (ch === llmChannel) {
                if (kind === 'snapshot' && payload && Array.isArray(payload.lines)) {
                    const parsed = parseLlmEventLines(payload.lines);
                    const ids = new Set();
                    for (const ev2 of parsed)
                        ids.add(ev2.event_id);
                    llmSeenIds.current = ids;
                    setLlmEvents(parsed);
                }
                else if ((kind === 'line' || kind === 'llm_stream') && text) {
                    const ev2 = parseLlmEventLine(text);
                    if (ev2 && !llmSeenIds.current.has(ev2.event_id)) {
                        llmSeenIds.current.add(ev2.event_id);
                        setLlmEvents((prev) => [...prev, ev2].slice(-500));
                    }
                }
            }
        });
        return () => {
            try {
                unsubscribe();
            }
            catch { /* noop */ }
            try {
                unregister();
            }
            catch { /* noop */ }
        };
    }, [isOpen, activeSource.channel, llmChannel, transportConnected, subscribeChannels, registerMessageHandler]);
    const isCodexSmart = !hasLlmChannel && (active === 'pm-subprocess' || active === 'director');
    const smartEvents = useMemo(() => {
        if (!isCodexSmart)
            return [];
        if (streamEvents.length > 0)
            return streamEvents;
        return parseCodexCliLines(lines);
    }, [isCodexSmart, lines, streamEvents]);
    const jsonEvents = useMemo(() => {
        if (active !== 'pm-log')
            return [];
        return lines
            .map((line, idx) => {
            const trimmed = line.trim();
            if (!trimmed)
                return null;
            try {
                return { id: `jsonl-${idx}`, raw: trimmed, value: JSON.parse(trimmed) };
            }
            catch {
                return { id: `jsonl-${idx}`, raw: trimmed, value: null };
            }
        })
            .filter(Boolean);
    }, [active, lines]);
    const isEmptyJson = (value, raw) => {
        if (value == null)
            return !(raw && raw.trim());
        if (Array.isArray(value))
            return value.length === 0;
        if (typeof value === 'object')
            return Object.keys(value).length === 0;
        return false;
    };
    const filteredEvents = useMemo(() => {
        return smartEvents.filter((event) => {
            // 1. First apply type filter (Tabs: All/Errors/Exec/Tool)
            if (filter !== 'all' && event.kind !== filter) {
                return false;
            }
            // 2. Then apply search query (Global Search)
            if (!query.trim())
                return true;
            const lowerQuery = query.toLowerCase();
            // Helper to check content based on event type
            const checkContent = () => {
                switch (event.kind) {
                    case 'json':
                        return (event.raw || '').toLowerCase().includes(lowerQuery) ||
                            JSON.stringify(event.value).toLowerCase().includes(lowerQuery);
                    case 'error':
                        return (event.raw || '').toLowerCase().includes(lowerQuery) ||
                            (event.errorType || '').toLowerCase().includes(lowerQuery);
                    case 'section':
                        return (event.title || '').toLowerCase().includes(lowerQuery) ||
                            (event.body || '').toLowerCase().includes(lowerQuery);
                    case 'exec':
                        return (event.cmd || '').toLowerCase().includes(lowerQuery) ||
                            (event.cwd || '').toLowerCase().includes(lowerQuery);
                    case 'tool':
                        return (event.tool || '').toLowerCase().includes(lowerQuery) ||
                            (event.message || '').toLowerCase().includes(lowerQuery);
                    case 'thinking':
                        return (event.title || '').toLowerCase().includes(lowerQuery) ||
                            (event.body || '').toLowerCase().includes(lowerQuery);
                    case 'runStart':
                        return (event.version || '').toLowerCase().includes(lowerQuery) ||
                            Object.values(event.meta).join(' ').toLowerCase().includes(lowerQuery);
                    case 'role':
                        return (event.role || '').toLowerCase().includes(lowerQuery);
                    case 'command':
                        return (event.cmd || '').toLowerCase().includes(lowerQuery) ||
                            (event.shell || '').toLowerCase().includes(lowerQuery);
                    case 'commandResult':
                        return (event.status || '').toLowerCase().includes(lowerQuery) ||
                            (event.cwd || '').toLowerCase().includes(lowerQuery);
                    case 'table':
                        return (event.title || '').toLowerCase().includes(lowerQuery) ||
                            event.columns.join(' ').toLowerCase().includes(lowerQuery) ||
                            event.rows.flat().join(' ').toLowerCase().includes(lowerQuery);
                    case 'fileContent':
                        return (event.pathHint || '').toLowerCase().includes(lowerQuery) ||
                            (event.content || '').toLowerCase().includes(lowerQuery);
                    case 'metric':
                        return (event.label || '').toLowerCase().includes(lowerQuery) ||
                            String(event.value).toLowerCase().includes(lowerQuery);
                    case 'text':
                        return (event.text || '').toLowerCase().includes(lowerQuery);
                    default:
                        return false;
                }
            };
            return checkContent();
        });
    }, [smartEvents, filter, query]);
    const summary = useMemo(() => {
        let errors = 0;
        let execs = 0;
        let tools = 0;
        smartEvents.forEach((event) => {
            if (event.kind === 'error')
                errors += 1;
            if (event.kind === 'exec')
                execs += 1;
            if (event.kind === 'tool')
                tools += 1;
        });
        return { errors, execs, tools };
    }, [smartEvents]);
    if (!isOpen)
        return null;
    return (_jsx("div", { "data-testid": "logs-modal", className: "fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-3 backdrop-blur-[2px] sm:p-4", children: _jsxs("div", { "data-testid": "logs-modal-panel", className: "flex min-h-0 w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-gray-700 bg-[#252526] shadow-2xl shadow-black/50", style: { maxHeight: 'min(86vh, 760px)' }, children: [_jsxs("div", { className: "flex min-w-0 items-center justify-between gap-3 border-b border-gray-700 p-4", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx(FileText, { className: "size-4 text-blue-400" }), _jsx("h2", { className: "truncate text-lg font-semibold text-gray-200", children: "\u8FD0\u884C\u65E5\u5FD7" })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-2", children: [_jsx("button", { onClick: refresh, "data-testid": "logs-modal-refresh", "aria-label": "\u5237\u65B0\u8FD0\u884C\u65E5\u5FD7", className: "p-2 text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded transition-colors", disabled: loading, children: _jsx(RefreshCw, { className: "size-4" }) }), _jsx("button", { onClick: onClose, "data-testid": "logs-modal-close", "aria-label": "\u5173\u95ED\u8FD0\u884C\u65E5\u5FD7", className: "p-2 text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded transition-colors", children: _jsx(X, { className: "size-4" }) })] })] }), bannerText ? (_jsx("div", { className: "mx-4 mt-3 max-h-40 overflow-auto rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200 whitespace-pre-wrap break-words", children: _jsxs("div", { className: "flex min-w-0 items-start justify-between gap-2", children: [_jsx("div", { className: "min-w-0 flex-1", children: bannerText }), onDismissBanner ? (_jsx("button", { onClick: onDismissBanner, className: "ml-2 text-red-200/70 hover:text-red-100 transition-colors", "aria-label": "\u5173\u95ED\u63D0\u793A", children: _jsx(X, { className: "size-4" }) })) : null] }) })) : null, _jsx("div", { className: "px-4 pt-3", children: _jsxs("div", { "data-testid": "logs-modal-source-row", className: "flex min-w-0 flex-col gap-2 pb-2 xl:flex-row xl:items-center", children: [_jsx("div", { className: "flex min-w-0 flex-1 items-center gap-2 overflow-x-auto", children: sources.map((item) => (_jsx("button", { onClick: () => setActive(item.id), className: `shrink-0 whitespace-nowrap rounded px-3 py-1.5 text-sm transition-colors ${active === item.id
                                        ? 'bg-blue-500/20 text-blue-300'
                                        : 'bg-gray-800 text-gray-400 hover:bg-gray-700'}`, children: item.label }, item.id))) }), _jsxs("div", { className: "flex shrink-0 flex-wrap items-center gap-2 text-xs text-gray-500", children: [_jsxs("span", { className: "break-all", children: ["\u66F4\u65B0\u65F6\u95F4: ", mtime || '-'] }), _jsxs("span", { className: "flex items-center gap-1", children: [_jsx(Activity, { className: "size-3" }), live ? '实时' : '离线'] })] })] }) }), _jsxs("div", { "data-testid": "logs-modal-controls-row", className: "flex min-w-0 flex-wrap items-center gap-2 px-4 pt-3", children: [_jsxs("div", { className: "flex shrink-0 items-center gap-1 rounded-md border border-gray-700 bg-gray-800/80 p-1", children: [_jsx("button", { onClick: () => allowRaw && setViewMode('raw'), disabled: !allowRaw, "data-testid": "logs-modal-view-raw", className: `px-2 py-1 text-xs rounded ${viewMode === 'raw' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'} ${!allowRaw ? 'opacity-40 cursor-not-allowed' : ''}`, children: "\u539F\u59CB" }), _jsx("button", { onClick: () => allowSmart && setViewMode('smart'), disabled: !allowSmart, "data-testid": "logs-modal-view-smart", className: `px-2 py-1 text-xs rounded ${viewMode === 'smart' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'} ${!allowSmart ? 'opacity-40 cursor-not-allowed' : ''}`, children: "\u667A\u6790" }), _jsx("button", { onClick: () => allowJson && setViewMode('json'), disabled: !allowJson, "data-testid": "logs-modal-view-json", className: `px-2 py-1 text-xs rounded ${viewMode === 'json' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'} ${!allowJson ? 'opacity-40 cursor-not-allowed' : ''}`, children: "JSON" })] }), viewMode === 'smart' && hasLlmChannel ? (_jsxs("div", { className: "ml-2 text-[10px] text-gray-500", children: [llmEvents.length, " events"] })) : viewMode === 'smart' && isCodexSmart ? (_jsxs(_Fragment, { children: [_jsxs("div", { className: "flex shrink-0 items-center gap-2 text-xs text-gray-400 sm:ml-2", children: [_jsxs("span", { className: "flex items-center gap-1", children: [_jsx(AlertTriangle, { className: "size-3 text-red-300" }), summary.errors] }), _jsxs("span", { className: "flex items-center gap-1", children: [_jsx(TerminalSquare, { className: "size-3 text-blue-300" }), summary.execs] }), _jsxs("span", { className: "flex items-center gap-1", children: [_jsx(Wrench, { className: "size-3 text-emerald-300" }), summary.tools] })] }), _jsxs("select", { className: "min-w-24 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-300 sm:ml-auto", value: filter, onChange: (event) => setFilter(event.target.value), children: [_jsx("option", { value: "all", children: "\u5168\u90E8" }), _jsx("option", { value: "error", children: "Reject" }), _jsx("option", { value: "exec", children: "\u6267\u884C" }), _jsx("option", { value: "tool", children: "\u5668\u7528" })] }), _jsx("input", { className: "min-w-0 flex-1 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-300 sm:max-w-64", placeholder: "\u641C\u7D22...", value: query, onChange: (event) => setQuery(event.target.value) })] })) : null] }), _jsx("div", { "data-testid": "logs-modal-content", className: "min-h-0 flex-1 overflow-auto p-4", children: error ? (_jsx("div", { className: "text-sm text-red-300", children: error })) : viewMode === 'raw' ? (_jsx("pre", { className: "text-xs text-gray-300 font-mono whitespace-pre-wrap break-all", children: loading ? '加载中...' : lines.join('\n') || '(空)' })) : viewMode === 'json' ? (_jsx("div", { className: "space-y-2", children: loading ? (_jsx("div", { className: "text-sm text-gray-300", children: "\u52A0\u8F7D\u4E2D..." })) : jsonEvents.length === 0 ? (_jsx("div", { className: "text-sm text-gray-400", children: "(\u7A7A)" })) : (jsonEvents.map((event) => (_jsx("pre", { className: "text-xs text-gray-200 font-mono whitespace-pre-wrap break-all", children: event.value ? JSON.stringify(event.value, null, 2) : event.raw }, event.id)))) })) : (_jsx("div", { className: "space-y-3", children: loading ? (_jsx("div", { className: "text-sm text-gray-300", children: "\u52A0\u8F7D\u4E2D..." })) : hasLlmChannel ? (llmEvents.length === 0 ? (_jsx("div", { className: "text-sm text-gray-400", children: "(\u7A7A \u2014 \u7B49\u5F85 LLM \u4E8B\u4EF6)" })) : (llmEvents
                            .filter(ev => !query.trim() || JSON.stringify(ev).toLowerCase().includes(query.toLowerCase()))
                            .map(ev => (_jsx("div", { className: "mx-1", children: _jsx(LlmEventCard, { event: ev }) }, ev.event_id))))) : isHpSmart ? (_jsx(PolarisTerminalRenderer, { text: lines.join('\n'), className: "text-slate-100" })) : filteredEvents.length === 0 ? (_jsx("div", { className: "text-sm text-gray-400", children: "(\u7A7A)" })) : ((() => {
                            const nodes = [];
                            let currentRole = null;
                            for (let i = 0; i < filteredEvents.length; i += 1) {
                                const event = filteredEvents[i];
                                const next = filteredEvents[i + 1];
                                const roleBadge = currentRole ? _jsx(RoleBadge, { role: currentRole }) : null;
                                if (event.kind === 'commandResult' && next?.kind === 'fileContent') {
                                    nodes.push(_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("div", { className: "flex items-center gap-2", children: [roleBadge, _jsx("div", { className: `text-xs ${event.status === 'ok' ? 'text-emerald-300' : 'text-red-300'}`, children: event.status === 'ok' ? '成功' : '失败' })] }), _jsxs("div", { className: "mt-1 text-xs text-gray-400", children: [event.cwd ? `cwd: ${event.cwd} ` : '', typeof event.ms === 'number' ? `· ${event.ms}ms ` : '', typeof event.exitCode === 'number' ? `· exit ${event.exitCode}` : ''] }), _jsxs("div", { className: "mt-2 text-xs text-gray-400", children: ["\u6587\u4EF6 ", next.pathHint || '', " ", next.encodingWarning ? ' · 编码告警' : ''] }), _jsx("pre", { className: "mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all", children: next.content || '(空)' })] }, event.id));
                                    i += 1;
                                    continue;
                                }
                                if (event.kind === 'commandResult' && next?.kind === 'error') {
                                    nodes.push(_jsxs("div", { className: "rounded border border-red-500/40 bg-red-500/10 p-3", children: [_jsxs("div", { className: "flex items-center gap-2", children: [roleBadge, _jsx("div", { className: "text-xs text-red-300", children: "\u5931\u8D25" })] }), _jsxs("div", { className: "mt-1 text-xs text-gray-400", children: [event.cwd ? `cwd: ${event.cwd} ` : '', typeof event.ms === 'number' ? `· ${event.ms}ms ` : '', typeof event.exitCode === 'number' ? `· exit ${event.exitCode}` : ''] }), _jsx("pre", { className: "mt-2 text-xs text-red-100 whitespace-pre-wrap break-all", children: next.raw })] }, event.id));
                                    i += 1;
                                    continue;
                                }
                                if (event.kind === 'section') {
                                    nodes.push(_jsxs("details", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("summary", { className: "cursor-pointer text-sm text-blue-200 flex items-center gap-2", children: [roleBadge, _jsx("span", { children: event.title })] }), _jsx("div", { className: "mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all", children: event.body || '(空)' })] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'runStart') {
                                    nodes.push(_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400", children: [roleBadge, _jsx("span", { children: "\u8F6E\u6B21" })] }), _jsxs("div", { className: "text-sm text-gray-200", children: ["OpenAI Codex v", event.version] }), _jsx("div", { className: "mt-1 text-xs text-gray-400", children: Object.entries(event.meta).map(([k, v]) => `${k}: ${v}`).join(' · ') })] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'role') {
                                    currentRole = event.role;
                                    continue;
                                }
                                if (event.kind === 'json') {
                                    if (isEmptyJson(event.value, event.raw)) {
                                        continue;
                                    }
                                    const jsonBody = event.value != null ? JSON.stringify(event.value, null, 2) : event.raw;
                                    nodes.push(_jsxs("details", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("summary", { className: "cursor-pointer text-sm text-emerald-200 flex items-center gap-2", children: [roleBadge, _jsx("span", { children: "JSON" })] }), _jsx("pre", { className: "mt-2 text-xs text-gray-200 whitespace-pre-wrap", children: jsonBody })] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'command') {
                                    nodes.push(_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400", children: [roleBadge, !roleBadge ? _jsx("span", { children: "\u6267\u884C" }) : null] }), _jsx("div", { className: "text-sm text-gray-200 break-all", children: event.cmd }), _jsx("div", { className: "mt-1 text-xs text-gray-400", children: event.shell }), event.lifecycle === 'open' ? (_jsx("div", { className: "mt-1 text-xs text-blue-300", children: "\u6D41\u5F0F\u8F93\u51FA\u4E2D..." })) : null] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'commandResult') {
                                    nodes.push(_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("div", { className: "flex items-center gap-2", children: [roleBadge, _jsx("div", { className: `text-xs ${event.status === 'ok' ? 'text-emerald-300' : 'text-red-300'}`, children: event.status === 'ok' ? '成功' : '失败' })] }), _jsxs("div", { className: "mt-1 text-xs text-gray-400", children: [event.cwd ? `cwd: ${event.cwd} ` : '', typeof event.ms === 'number' ? `· ${event.ms}ms ` : '', typeof event.exitCode === 'number' ? `· exit ${event.exitCode}` : ''] }), event.lifecycle === 'open' ? (_jsx("div", { className: "mt-1 text-xs text-blue-300", children: "\u6D41\u5F0F\u8F93\u51FA\u4E2D..." })) : null] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'exec') {
                                    nodes.push(_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400", children: [roleBadge, !roleBadge ? _jsx("span", { children: "\u6267\u884C" }) : null] }), _jsx("div", { className: "text-sm text-gray-200 break-all", children: event.cmd }), _jsxs("div", { className: "mt-1 text-xs text-gray-400", children: [event.cwd ? `cwd: ${event.cwd} ` : '', typeof event.ms === 'number' ? `· ${event.ms}ms ` : '', typeof event.exitCode === 'number' ? `· exit ${event.exitCode}` : ''] })] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'tool') {
                                    nodes.push(_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400", children: [roleBadge, _jsx("span", { children: "\u5668\u7528" })] }), _jsxs("div", { className: "text-sm text-gray-200", children: [event.tool, " \u00B7 ", event.phase] }), event.message ? _jsx("div", { className: "mt-1 text-xs text-gray-300 whitespace-pre-wrap break-all", children: event.message }) : null] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'table') {
                                    nodes.push(_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400", children: [roleBadge, _jsx("span", { children: "\u76EE\u5F55" })] }), _jsx("div", { className: "text-xs text-gray-400", children: event.title || '' }), _jsx("div", { className: "mt-2 overflow-auto", children: _jsxs("table", { className: "w-full text-xs text-gray-200", children: [_jsx("thead", { children: _jsx("tr", { children: event.columns.map((c, ci) => (_jsx("th", { className: "text-left font-medium pr-4", children: c }, ci))) }) }), _jsx("tbody", { children: event.rows.map((r, ri) => (_jsx("tr", { children: r.map((cell, ci) => (_jsx("td", { className: "pr-4 py-0.5", children: cell }, ci))) }, ri))) })] }) })] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'fileContent') {
                                    const markupKind = detectMarkupKind(event.content, event.pathHint);
                                    if (markupKind) {
                                        nodes.push(_jsx(MarkupCard, { title: `文件 ${event.pathHint || ''}`, source: event.content, kind: markupKind, badge: roleBadge, meta: event.encodingWarning ? '编码告警' : undefined }, event.id));
                                        continue;
                                    }
                                    nodes.push(_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400", children: [roleBadge, _jsxs("span", { children: ["\u6587\u4EF6 ", event.pathHint || '', " ", event.encodingWarning ? ' · 编码告警' : ''] })] }), event.lifecycle === 'open' ? (_jsx("div", { className: "mt-1 text-xs text-blue-300", children: "\u6D41\u5F0F\u8F93\u51FA\u4E2D..." })) : null, _jsx("pre", { className: "mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all", children: event.content || '(空)' })] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'metric') {
                                    const normalizedLabel = (event.label || '').trim().toLowerCase().replace(/\s+/g, ' ');
                                    const isTokensUsed = normalizedLabel === 'tokens used' || normalizedLabel === 'token used';
                                    const rawValue = String(event.value || '');
                                    const numeric = Number.parseInt(rawValue.replace(/[^\d]/g, ''), 10);
                                    const formatted = Number.isFinite(numeric) ? numeric.toLocaleString() : rawValue;
                                    const compact = Number.isFinite(numeric)
                                        ? new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(numeric)
                                        : null;
                                    nodes.push(_jsx("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: isTokensUsed ? (_jsxs(_Fragment, { children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400", children: [roleBadge, _jsx("span", { className: "uppercase tracking-wider text-[10px] text-slate-300", children: "\u8BCD\u5143\u8017\u7528" }), compact ? (_jsx("span", { className: "ml-auto rounded-full border border-emerald-400/20 bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-200", children: compact })) : null] }), _jsxs("div", { className: "mt-2 flex items-end justify-between gap-3", children: [_jsx("div", { className: "text-2xl font-semibold leading-none text-slate-100", children: formatted }), _jsx("div", { className: "text-[11px] text-gray-400", children: "\u8BCD\u5143" })] }), _jsx("div", { className: "mt-2 h-1.5 w-full rounded-full bg-gray-800/80 overflow-hidden", children: _jsx("div", { className: "h-full w-full bg-emerald-500/50" }) })] })) : (_jsxs(_Fragment, { children: [_jsxs("div", { className: "flex items-center gap-2 text-xs text-gray-400", children: [roleBadge, _jsx("span", { className: "uppercase tracking-wider text-[10px]", children: event.label || '指标' })] }), _jsx("div", { className: "mt-1 text-sm text-gray-200", children: _jsx("span", { className: "font-semibold text-emerald-200", children: event.value }) })] })) }, event.id));
                                    continue;
                                }
                                if (event.kind === 'thinking') {
                                    const cleanBody = stripLlmTags(event.body);
                                    nodes.push(_jsxs("details", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [_jsxs("summary", { className: "cursor-pointer text-sm text-slate-300 flex items-center gap-2", children: [roleBadge, _jsx("span", { children: event.title || '思考' })] }), _jsx("div", { className: "mt-2 text-xs text-gray-200", children: cleanBody ? _jsx("div", { className: "mt-2 whitespace-pre-wrap break-all text-gray-200", children: cleanBody }) : null })] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'error') {
                                    nodes.push(_jsxs("details", { className: "rounded border border-red-500/40 bg-red-500/10 p-3", children: [_jsxs("summary", { className: "cursor-pointer text-sm text-red-200 flex items-center gap-2", children: [roleBadge, _jsx("span", { children: event.errorType })] }), _jsx("pre", { className: "mt-2 text-xs text-red-100 whitespace-pre-wrap break-all", children: event.raw })] }, event.id));
                                    continue;
                                }
                                if (event.kind === 'text') {
                                    const cleanText = stripLlmTags(event.text);
                                    if (!cleanText)
                                        continue;
                                    const markupKind = detectMarkupKind(cleanText);
                                    if (markupKind) {
                                        nodes.push(_jsx(MarkupCard, { title: "\u6807\u8BB0\u5185\u5BB9", source: cleanText, kind: markupKind, badge: roleBadge }, event.id));
                                        continue;
                                    }
                                    nodes.push(_jsxs("div", { className: "rounded border border-gray-700 bg-gray-900/40 p-3", children: [roleBadge ? _jsx("div", { className: "mb-1", children: roleBadge }) : null, _jsx("div", { className: "text-xs text-gray-200 whitespace-pre-wrap break-all", children: cleanText })] }, event.id));
                                    continue;
                                }
                            }
                            return nodes;
                        })()) })) })] }) }));
}
