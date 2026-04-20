import { RefreshCw, X, FileText, Activity, AlertTriangle, TerminalSquare, Wrench } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch, connectWebSocket } from '@/api';
import { CodexCliStreamParser, parseCodexCliLines, stripLlmTags, type LogEvent } from '@/app/components/logs/CodexCliStreamParser';
import { LlmEventCard } from '@/app/components/logs/LlmEventCard';
import { parseLlmEventLine, parseLlmEventLines, type LlmEvent } from '@/app/components/logs/LlmEventTypes';
import { PolarisTerminalRenderer } from '@/app/components/PolarisTerminalRenderer';
import { sanitizeHtml } from '@/app/utils/xssSanitizer';

interface LogsModalProps {
  isOpen: boolean;
  onClose: () => void;
  workspace?: string; // Add workspace prop
  initialSourceId?: string | null;
  runId?: string | null; // Support viewing logs for a specific run
  banner?: string | null;
  onDismissBanner?: () => void;
}

const DEFAULT_LOG_SOURCES = [
  { id: 'pm-subprocess', label: 'PM 子进程', path: 'runtime/logs/pm.process.log', channel: 'pm_subprocess' },
  { id: 'pm-report', label: 'PM 禀报', path: 'runtime/results/pm.report.md', channel: 'pm_report' },
  { id: 'pm-log', label: 'PM 纪要（jsonl）', path: 'runtime/events/pm.events.jsonl', channel: 'pm_log' },
  { id: 'director', label: '工部子进程', path: 'runtime/logs/director.process.log', channel: 'director_console' },
  { id: 'planner', label: '谋划稿', path: 'runtime/results/planner.output.md', channel: 'planner' },
  { id: 'ollama', label: 'Ollama', path: 'runtime/results/director_llm.output.md', channel: 'ollama' },
  { id: 'qa', label: '审校', path: 'runtime/results/qa.review.md', channel: 'qa' },
  { id: 'runlog', label: '运行纪要', path: 'runtime/logs/director.runlog.md', channel: 'runlog' },
];

function SmartText({ text }: { text: string }) {
  const max = 400;
  if (text.length <= max) {
    return <div className="text-xs text-gray-200 whitespace-pre-wrap">{text}</div>;
  }
  return (
    <details className="text-xs text-gray-200 whitespace-pre-wrap">
      <summary className="cursor-pointer text-gray-400">展开内容</summary>
      {text}
    </details>
  );
}

const ROLE_BADGE_STYLES: Record<'user' | 'thinking' | 'exec', string> = {
  user: 'border-blue-500/40 bg-blue-500/20 text-blue-200',
  thinking: 'border-purple-500/40 bg-purple-500/20 text-purple-200',
  exec: 'border-amber-500/40 bg-amber-500/20 text-amber-200',
};

function RoleBadge({ role }: { role: 'user' | 'thinking' | 'exec' }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${ROLE_BADGE_STYLES[role]}`}
    >
      {role}
    </span>
  );
}

type MarkupKind = 'html' | 'svg' | 'xml';
type MarkupView = 'render' | 'source' | 'tree';
type XmlTreeNode = {
  name: string;
  attributes: [string, string][];
  children: XmlTreeNode[];
  text?: string;
};

function escapeHtml(source: string) {
  return source
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function buildMarkupSrcDoc(kind: MarkupKind, source: string) {
  const trimmed = (source || '').trim();
  if (!trimmed) return '';

  if (kind === 'svg') {
    return `<!doctype html><html><head><meta charset="utf-8" /><style>html,body{margin:0;padding:0;background:radial-gradient(1000px 600px at 20% 20%, rgba(34,211,238,.18), transparent 60%),radial-gradient(900px 500px at 80% 30%, rgba(168,85,247,.16), transparent 55%),#050816;color:#e5e7eb;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans SC','Source Han Sans SC','SimSun','SimHei',sans-serif;}a{color:#22d3ee;}code,pre{font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;}</style></head><body style="display:flex;align-items:center;justify-content:center;min-height:100vh;"><div style="padding:14px;border:1px solid rgba(34,211,238,.25);border-radius:12px;background:rgba(3,7,18,.65);box-shadow:0 0 0 1px rgba(168,85,247,.12),0 0 24px rgba(34,211,238,.12),0 0 42px rgba(168,85,247,.10);">${trimmed}</div></body></html>`;
  }

  if (kind !== 'html') return '';

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
    hasRenderableElements = Boolean(
      doc.body?.querySelector(
        'img,svg,canvas,video,iframe,object,embed,table,button,input,select,textarea,hr,ul,ol,li,blockquote'
      )
    );
  } catch {
    headHtml = '';
    bodyHtml = trimmed;
    textContent = '';
    hasRenderableElements = false;
  }

  const normalizedText = textContent.replace(/\s+/g, ' ').trim();
  const visible = normalizedText.length > 0 || hasRenderableElements;
  const fallback = `<pre style="white-space:pre-wrap;word-break:break-word;margin:0;font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;font-size:12px;line-height:1.5;color:#e5e7eb;">${escapeHtml(
    trimmed
  )}</pre>`;
  const finalBody = visible ? sanitizeHtml(bodyHtml) : fallback;

  return `<!doctype html><html><head><meta charset="utf-8" /><base target="_blank" />${headHtml}<style>html,body{margin:0;padding:0;background:radial-gradient(1100px 650px at 15% 20%, rgba(34,211,238,.16), transparent 62%),radial-gradient(900px 520px at 85% 25%, rgba(168,85,247,.14), transparent 58%),radial-gradient(900px 600px at 60% 90%, rgba(251,113,133,.10), transparent 60%),#050816;color:#e5e7eb;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans SC','Source Han Sans SC','SimSun','SimHei',sans-serif;}a{color:#22d3ee;}a:hover{color:#a855f7;}code,pre{font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;}*{box-sizing:border-box;}hr{border:0;border-top:1px solid rgba(148,163,184,.22);}table{border-collapse:collapse;}td,th{border:1px solid rgba(148,163,184,.18);padding:6px 8px;}blockquote{border-left:3px solid rgba(34,211,238,.35);margin:8px 0;padding:6px 10px;background:rgba(3,7,18,.35);}img{max-width:100%;height:auto;} </style></head><body><div style="padding:14px;"><div style="border:1px solid rgba(34,211,238,.22);border-radius:12px;background:rgba(3,7,18,.62);box-shadow:0 0 0 1px rgba(168,85,247,.10),0 0 26px rgba(34,211,238,.12),0 0 46px rgba(168,85,247,.10);padding:12px;min-height:100%;backdrop-filter:blur(6px);">${finalBody}</div></div></body></html>`;
}

function detectMarkupKind(source: string, pathHint?: string): MarkupKind | null {
  if (!source) return null;
  const hint = (pathHint || '').toLowerCase();
  if (hint.endsWith('.vue')) return null;
  if (hint.endsWith('.svg')) return 'svg';
  if (hint.endsWith('.html') || hint.endsWith('.htm')) return 'html';
  if (hint.endsWith('.xml')) return 'xml';
  const trimmed = source.trim();
  if (!trimmed.startsWith('<')) return null;
  if (/^<!doctype\s+html/i.test(trimmed) || /<html[\s>]/i.test(trimmed)) return 'html';
  if (/<svg[\s>]/i.test(trimmed)) return 'svg';
  if (/^<\?xml/i.test(trimmed)) return 'xml';
  if (
    /<\s*(div|span|p|a|img|table|tr|td|th|ul|ol|li|section|article|header|footer|main|nav|pre|code|h[1-6]|br|hr|input|button|form|label|textarea|select)\b/i.test(
      trimmed
    )
  ) {
    return 'html';
  }
  if (typeof window !== 'undefined' && 'DOMParser' in window) {
    try {
      const parser = new DOMParser();
      const xml = parser.parseFromString(trimmed, 'text/xml');
      if (!xml.querySelector('parsererror')) {
        const root = (xml.documentElement?.tagName || '').toLowerCase();
        if (root && root !== 'html' && root !== 'svg') return 'xml';
      }
    } catch {
      // ignore parse failures
    }
  }
  return null;
}

function MarkupCard({
  title,
  source,
  kind,
  badge,
  meta,
}: {
  title: string;
  source: string;
  kind: MarkupKind;
  badge?: JSX.Element | null;
  meta?: string;
}) {
  const renderProbablyBlank = useMemo(() => {
    if (kind !== 'html') return false;
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

  const [view, setView] = useState<MarkupView>(() => {
    if (kind === 'xml') return 'tree';
    if (kind === 'html' && renderProbablyBlank) return 'source';
    return 'render';
  });
  const canRender = kind === 'html' || kind === 'svg';
  const canTree = kind === 'xml';
  const xmlTree = useMemo(() => {
    if (!canTree) return null;
    try {
      const parser = new DOMParser();
      const xml = parser.parseFromString(source, 'text/xml');
      if (xml.querySelector('parsererror')) return null;
      const root = xml.documentElement;
      return root ? buildXmlTree(root) : null;
    } catch {
      return null;
    }
  }, [canTree, source]);
  const srcDoc = useMemo(() => {
    if (!canRender) return '';
    return buildMarkupSrcDoc(kind, source);
  }, [canRender, kind, source]);

  return (
    <div className="rounded border border-gray-700 bg-gray-900/40 p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-gray-400">
          {badge}
          <span>{title}</span>
          <span className="rounded bg-gray-800 px-2 py-0.5 text-[10px] text-gray-300 uppercase">{kind}</span>
          {meta ? <span className="text-gray-500">{meta}</span> : null}
        </div>
        <div className="flex items-center gap-1 text-[10px]">
          {canRender ? (
            <button
              className={`rounded px-2 py-0.5 ${view === 'render' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'}`}
              onClick={() => setView('render')}
            >
              渲染
            </button>
          ) : null}
          {canTree ? (
            <button
              className={`rounded px-2 py-0.5 ${view === 'tree' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'}`}
              onClick={() => setView('tree')}
            >
              树视图
            </button>
          ) : null}
          <button
            className={`rounded px-2 py-0.5 ${view === 'source' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'}`}
            onClick={() => setView('source')}
          >
            源文
          </button>
        </div>
      </div>
      {view === 'render' && canRender ? (
        <iframe
          className="mt-2 h-60 w-full rounded border border-gray-700 bg-transparent"
          sandbox=""
          srcDoc={srcDoc}
          title={title}
        />
      ) : view === 'tree' && canTree ? (
        xmlTree ? (
          <div className="mt-2">
            <XmlTreeNodeView node={xmlTree} depth={0} />
          </div>
        ) : (
          <pre className="mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all">{source || '(空)'}</pre>
        )
      ) : (
        <pre className="mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all">{source || '(空)'}</pre>
      )}
    </div>
  );
}

function buildXmlTree(node: Node): XmlTreeNode | null {
  if (node.nodeType === Node.TEXT_NODE || node.nodeType === Node.CDATA_SECTION_NODE) {
    const text = (node.textContent || '').trim();
    if (!text) return null;
    return { name: '#text', attributes: [], children: [], text };
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  const el = node as Element;
  const attributes: [string, string][] = Array.from(el.attributes).map((attr) => [attr.name, attr.value]);
  const children: XmlTreeNode[] = [];
  Array.from(el.childNodes).forEach((child) => {
    const childNode = buildXmlTree(child);
    if (childNode) children.push(childNode);
  });
  return {
    name: el.tagName,
    attributes,
    children,
  };
}

function XmlTreeNodeView({ node, depth }: { node: XmlTreeNode; depth: number }) {
  const isText = node.name === '#text';
  if (isText) {
    return (
      <div className="ml-4 text-xs text-gray-300 italic whitespace-pre-wrap">{node.text}</div>
    );
  }
  const openByDefault = depth < 1;
  return (
    <details open={openByDefault} className="text-xs text-gray-200">
      <summary className="cursor-pointer text-gray-200">
        <span className="text-blue-200">&lt;{node.name}</span>
        {node.attributes.length
          ? node.attributes.map(([key, value]) => (
            <span key={key} className="ml-1 text-emerald-200">
              {key}="<span className="text-gray-300">{value}</span>"
            </span>
          ))
          : null}
        <span className="text-blue-200">&gt;</span>
      </summary>
      <div className="ml-4 space-y-1">
        {node.children.map((child, idx) => (
          <XmlTreeNodeView key={`${child.name}-${idx}`} node={child} depth={depth + 1} />
        ))}
        <div className="text-blue-200">&lt;/{node.name}&gt;</div>
      </div>
    </details>
  );
}

export function LogsModal({
  isOpen,
  onClose,
  initialSourceId,
  runId,
  banner,
  onDismissBanner,
}: LogsModalProps) {
  const bannerText = useMemo(() => {
    if (typeof banner === 'string') {
      return banner.trim();
    }
    if (banner == null) return '';
    try {
      return JSON.stringify(banner, null, 2);
    } catch {
      return String(banner);
    }
  }, [banner]);

  // If runId is provided, we map sources to the run directory
  const sources = useMemo(() => {
    if (!runId) return DEFAULT_LOG_SOURCES;
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
  const [lines, setLines] = useState<string[]>([]);
  const [mtime, setMtime] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [viewMode, setViewMode] = useState<'raw' | 'smart' | 'json'>('smart');
  const [filter, setFilter] = useState<'all' | 'error' | 'exec' | 'tool'>('all');
  const [query, setQuery] = useState('');
  const socketRef = useRef<WebSocket | null>(null);
  const [streamEvents, setStreamEvents] = useState<LogEvent[]>([]);
  const parserRef = useRef<CodexCliStreamParser | null>(null);

  const activeSource = useMemo(
    () => sources.find((item) => item.id === active) || sources[0],
    [active, sources]
  );

  const LLM_CHANNEL_MAP: Record<string, string> = { 'pm-subprocess': 'llm', 'director': 'llm' };
  const llmChannel = LLM_CHANNEL_MAP[active] || '';
  const hasLlmChannel = !!llmChannel;
  const isHpSmart = active === 'runlog';
  const allowSmart = hasLlmChannel || isHpSmart;
  const allowJson = active === 'pm-log';
  const allowRaw = active !== 'pm-log';
  const [llmEvents, setLlmEvents] = useState<LlmEvent[]>([]);
  const llmSeenIds = useRef<Set<string>>(new Set());

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/files/read?path=${encodeURIComponent(activeSource.path)}&tail_lines=400`);
      if (!res.ok) {
        throw new Error('读取案牍失败');
      }
      const payload = (await res.json()) as { content?: string; mtime?: string };
      setLines(payload.content ? payload.content.split('\n') : []);
      setMtime(payload.mtime || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取案牍失败');
      setLines([]);
      setMtime('');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    refresh();
  }, [isOpen, active]);

  useEffect(() => {
    if (!isOpen) return;
    if (initialSourceId) {
      const exists = sources.some((item) => item.id === initialSourceId);
      setActive(exists ? initialSourceId : sources[0].id);
    }
  }, [isOpen, initialSourceId, sources]);

  useEffect(() => {
    if (!isOpen) return;
    if (active === 'pm-subprocess' || active === 'director' || active === 'runlog') {
      setViewMode('smart');
    } else if (active === 'pm-log') {
      setViewMode('json');
    } else {
      setViewMode('raw');
    }
  }, [isOpen, active]);

  useEffect(() => {
    if (!isOpen) return;
    let activeSocket: WebSocket | null = null;
    let alive = true;

    const connect = async () => {
      try {
        activeSocket = await connectWebSocket();
      } catch {
        if (alive) setLive(false);
        return;
      }

      socketRef.current = activeSocket;
      if (!alive) return;

      activeSocket.onopen = () => {
        setLive(true);
        const channels = [activeSource.channel];
        if (llmChannel) channels.push(llmChannel);
        activeSocket?.send(JSON.stringify({ type: 'subscribe', channels, tail_lines: 200 }));
      };

      activeSocket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          const ch = String(payload.channel || '').trim();
          const msgType = String(payload.type || '').trim().toLowerCase();
          const eventText = payload.event && typeof payload.event === 'object' ? JSON.stringify(payload.event) : '';
          const lineText = typeof payload.line === 'string' ? payload.line : '';
          const text = eventText || lineText || (typeof payload.text === 'string' ? payload.text : '');

          if (ch === activeSource.channel) {
            if (msgType === 'snapshot' && Array.isArray(payload.lines)) {
              setLines(payload.lines);
              const parser = new CodexCliStreamParser();
              payload.lines.forEach((line: string) => parser.feedLine(line));
              parserRef.current = parser;
              setStreamEvents([...parser.events]);
            } else if ((msgType === 'line' || msgType === 'process_stream' || msgType === 'runtime_event' || msgType === 'dialogue_event') && text) {
              setLines((prev) => [...prev, text].slice(-1000));
              if (!parserRef.current) parserRef.current = new CodexCliStreamParser();
              parserRef.current.feedLine(text);
              setStreamEvents([...parserRef.current.events]);
            }
          }

          if (ch === llmChannel) {
            if (msgType === 'snapshot' && Array.isArray(payload.lines)) {
              const parsed = parseLlmEventLines(payload.lines);
              const ids = new Set<string>();
              for (const ev of parsed) ids.add(ev.event_id);
              llmSeenIds.current = ids;
              setLlmEvents(parsed);
            } else if ((msgType === 'line' || msgType === 'llm_stream') && text) {
              const ev = parseLlmEventLine(text);
              if (ev && !llmSeenIds.current.has(ev.event_id)) {
                llmSeenIds.current.add(ev.event_id);
                setLlmEvents(prev => [...prev, ev].slice(-500));
              }
            }
          }
        } catch {
          // ignore malformed payloads
        }
      };

      activeSocket.onclose = () => {
        if (alive) setLive(false);
      };

      activeSocket.onerror = () => {
        if (alive) setLive(false);
      };
    };

    connect();

    return () => {
      alive = false;
      if (socketRef.current) {
        socketRef.current.close();
        socketRef.current = null;
      }
    };
  }, [isOpen, activeSource.channel, llmChannel]);

  const isCodexSmart = !hasLlmChannel && (active === 'pm-subprocess' || active === 'director');
  const smartEvents = useMemo(() => {
    if (!isCodexSmart) return [];
    if (streamEvents.length > 0) return streamEvents;
    return parseCodexCliLines(lines);
  }, [isCodexSmart, lines, streamEvents]);
  const jsonEvents = useMemo(() => {
    if (active !== 'pm-log') return [];
    return lines
      .map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) return null;
        try {
          return { id: `jsonl-${idx}`, raw: trimmed, value: JSON.parse(trimmed) };
        } catch {
          return { id: `jsonl-${idx}`, raw: trimmed, value: null };
        }
      })
      .filter(Boolean) as { id: string; raw: string; value: unknown | null }[];
  }, [active, lines]);

  const isEmptyJson = (value: unknown, raw?: string) => {
    if (value == null) return !(raw && raw.trim());
    if (Array.isArray(value)) return value.length === 0;
    if (typeof value === 'object') return Object.keys(value as Record<string, unknown>).length === 0;
    return false;
  };

  const filteredEvents = useMemo(() => {
    return smartEvents.filter((event) => {
      // 1. First apply type filter (Tabs: All/Errors/Exec/Tool)
      if (filter !== 'all' && event.kind !== filter) {
        return false;
      }

      // 2. Then apply search query (Global Search)
      if (!query.trim()) return true;
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
      if (event.kind === 'error') errors += 1;
      if (event.kind === 'exec') execs += 1;
      if (event.kind === 'tool') tools += 1;
    });
    return { errors, execs, tools };
  }, [smartEvents]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-[#252526] border border-gray-700 rounded-lg w-full max-w-3xl max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div className="flex items-center gap-2">
            <FileText className="size-4 text-blue-400" />
            <h2 className="text-lg font-semibold text-gray-200">运行日志</h2>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={refresh}
              className="p-2 text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded transition-colors"
              disabled={loading}
            >
              <RefreshCw className="size-4" />
            </button>
            <button
              onClick={onClose}
              className="p-2 text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded transition-colors"
            >
              <X className="size-4" />
            </button>
          </div>
        </div>

        {bannerText ? (
          <div className="mx-4 mt-3 max-h-40 overflow-auto rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200 whitespace-pre-wrap">
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1">{bannerText}</div>
              {onDismissBanner ? (
                <button
                  onClick={onDismissBanner}
                  className="ml-2 text-red-200/70 hover:text-red-100 transition-colors"
                  aria-label="关闭提示"
                >
                  <X className="size-4" />
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        <div className="px-4 pt-3">
          <div className="flex items-center gap-2 overflow-x-auto pb-2">
            {sources.map((item) => (
              <button
                key={item.id}
                onClick={() => setActive(item.id)}
                className={`px-3 py-1.5 text-sm rounded transition-colors whitespace-nowrap ${active === item.id
                  ? 'bg-blue-500/20 text-blue-300'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                  }`}
              >
                {item.label}
              </button>
            ))}
            <span className="ml-auto text-xs text-gray-500">更新时间: {mtime || '-'}</span>
            <span className="text-xs text-gray-500 flex items-center gap-1">
              <Activity className="size-3" />
              {live ? '实时' : '离线'}
            </span>
          </div>
        </div>

        <div className="px-4 pt-3 flex items-center gap-2">
          <div className="flex items-center gap-1 rounded-md border border-gray-700 bg-gray-800/80 p-1">
            <button
              onClick={() => allowRaw && setViewMode('raw')}
              disabled={!allowRaw}
              className={`px-2 py-1 text-xs rounded ${viewMode === 'raw' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'
                } ${!allowRaw ? 'opacity-40 cursor-not-allowed' : ''}`}
            >
              原始
            </button>
            <button
              onClick={() => allowSmart && setViewMode('smart')}
              disabled={!allowSmart}
              className={`px-2 py-1 text-xs rounded ${viewMode === 'smart' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'
                } ${!allowSmart ? 'opacity-40 cursor-not-allowed' : ''}`}
            >
              智析
            </button>
            <button
              onClick={() => allowJson && setViewMode('json')}
              disabled={!allowJson}
              className={`px-2 py-1 text-xs rounded ${viewMode === 'json' ? 'bg-blue-500/30 text-blue-200' : 'text-gray-400 hover:text-gray-200'
                } ${!allowJson ? 'opacity-40 cursor-not-allowed' : ''}`}
            >
              JSON
            </button>
          </div>
          {viewMode === 'smart' && hasLlmChannel ? (
            <div className="ml-2 text-[10px] text-gray-500">{llmEvents.length} events</div>
          ) : viewMode === 'smart' && isCodexSmart ? (
            <>
              <div className="ml-2 flex items-center gap-2 text-xs text-gray-400">
                <span className="flex items-center gap-1">
                  <AlertTriangle className="size-3 text-red-300" />
                  {summary.errors}
                </span>
                <span className="flex items-center gap-1">
                  <TerminalSquare className="size-3 text-blue-300" />
                  {summary.execs}
                </span>
                <span className="flex items-center gap-1">
                  <Wrench className="size-3 text-emerald-300" />
                  {summary.tools}
                </span>
              </div>
              <select
                className="ml-auto bg-gray-800 text-xs text-gray-300 border border-gray-700 rounded px-2 py-1"
                value={filter}
                onChange={(event) => setFilter(event.target.value as typeof filter)}
              >
                <option value="all">全部</option>
                <option value="error">封驳</option>
                <option value="exec">执行</option>
                <option value="tool">器用</option>
              </select>
              <input
                className="bg-gray-800 text-xs text-gray-300 border border-gray-700 rounded px-2 py-1"
                placeholder="搜索..."
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </>
          ) : null}
        </div>

        <div className="flex-1 overflow-auto p-4">
          {error ? (
            <div className="text-sm text-red-300">{error}</div>
          ) : viewMode === 'raw' ? (
            <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-all">
              {loading ? '加载中...' : lines.join('\n') || '(空)'}
            </pre>
          ) : viewMode === 'json' ? (
            <div className="space-y-2">
              {loading ? (
                <div className="text-sm text-gray-300">加载中...</div>
              ) : jsonEvents.length === 0 ? (
                <div className="text-sm text-gray-400">(空)</div>
              ) : (
                jsonEvents.map((event) => (
                  <pre key={event.id} className="text-xs text-gray-200 font-mono whitespace-pre-wrap break-all">
                    {event.value ? JSON.stringify(event.value, null, 2) : event.raw}
                  </pre>
                ))
              )}
            </div>
          ) : (
            <div className="space-y-3">
              {loading ? (
                <div className="text-sm text-gray-300">加载中...</div>
              ) : hasLlmChannel ? (
                llmEvents.length === 0 ? (
                  <div className="text-sm text-gray-400">(空 — 等待 LLM 事件)</div>
                ) : (
                  llmEvents
                    .filter(ev => !query.trim() || JSON.stringify(ev).toLowerCase().includes(query.toLowerCase()))
                    .map(ev => (
                      <div key={ev.event_id} className="mx-1">
                        <LlmEventCard event={ev} />
                      </div>
                    ))
                )
              ) : isHpSmart ? (
                <PolarisTerminalRenderer
                  text={lines.join('\n')}
                  className="text-slate-100"
                />
              ) : filteredEvents.length === 0 ? (
                <div className="text-sm text-gray-400">(空)</div>
              ) : (
                (() => {
                  const nodes: JSX.Element[] = [];
                  let currentRole: 'user' | 'thinking' | 'exec' | null = null;
                  for (let i = 0; i < filteredEvents.length; i += 1) {
                    const event = filteredEvents[i];
                    const next = filteredEvents[i + 1];
                    const roleBadge = currentRole ? <RoleBadge role={currentRole} /> : null;
                    if (event.kind === 'commandResult' && next?.kind === 'fileContent') {
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <div className="flex items-center gap-2">
                            {roleBadge}
                            <div className={`text-xs ${event.status === 'ok' ? 'text-emerald-300' : 'text-red-300'}`}>
                              {event.status === 'ok' ? '成功' : '失败'}
                            </div>
                          </div>
                          <div className="mt-1 text-xs text-gray-400">
                            {event.cwd ? `cwd: ${event.cwd} ` : ''}
                            {typeof event.ms === 'number' ? `· ${event.ms}ms ` : ''}
                            {typeof event.exitCode === 'number' ? `· exit ${event.exitCode}` : ''}
                          </div>
                          <div className="mt-2 text-xs text-gray-400">
                            文件 {next.pathHint || ''} {next.encodingWarning ? ' · 编码告警' : ''}
                          </div>
                          <pre className="mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all">{next.content || '(空)'}</pre>
                        </div>
                      );
                      i += 1;
                      continue;
                    }
                    if (event.kind === 'commandResult' && next?.kind === 'error') {
                      nodes.push(
                        <div key={event.id} className="rounded border border-red-500/40 bg-red-500/10 p-3">
                          <div className="flex items-center gap-2">
                            {roleBadge}
                            <div className="text-xs text-red-300">失败</div>
                          </div>
                          <div className="mt-1 text-xs text-gray-400">
                            {event.cwd ? `cwd: ${event.cwd} ` : ''}
                            {typeof event.ms === 'number' ? `· ${event.ms}ms ` : ''}
                            {typeof event.exitCode === 'number' ? `· exit ${event.exitCode}` : ''}
                          </div>
                          <pre className="mt-2 text-xs text-red-100 whitespace-pre-wrap break-all">{next.raw}</pre>
                        </div>
                      );
                      i += 1;
                      continue;
                    }
                    if (event.kind === 'section') {
                      nodes.push(
                        <details key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <summary className="cursor-pointer text-sm text-blue-200 flex items-center gap-2">
                            {roleBadge}
                            <span>{event.title}</span>
                          </summary>
                          <div className="mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all">{event.body || '(空)'}</div>
                        </details>
                      );
                      continue;
                    }
                    if (event.kind === 'runStart') {
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <div className="flex items-center gap-2 text-xs text-gray-400">
                            {roleBadge}
                            <span>轮次</span>
                          </div>
                          <div className="text-sm text-gray-200">OpenAI Codex v{event.version}</div>
                          <div className="mt-1 text-xs text-gray-400">{Object.entries(event.meta).map(([k, v]) => `${k}: ${v}`).join(' · ')}</div>
                        </div>
                      );
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
                      nodes.push(
                        <details key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <summary className="cursor-pointer text-sm text-emerald-200 flex items-center gap-2">
                            {roleBadge}
                            <span>JSON</span>
                          </summary>
                          <pre className="mt-2 text-xs text-gray-200 whitespace-pre-wrap">{jsonBody}</pre>
                        </details>
                      );
                      continue;
                    }
                    if (event.kind === 'command') {
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <div className="flex items-center gap-2 text-xs text-gray-400">
                            {roleBadge}
                            {!roleBadge ? <span>执行</span> : null}
                          </div>
                          <div className="text-sm text-gray-200 break-all">{event.cmd}</div>
                          <div className="mt-1 text-xs text-gray-400">{event.shell}</div>
                          {event.lifecycle === 'open' ? (
                            <div className="mt-1 text-xs text-blue-300">流式输出中...</div>
                          ) : null}
                        </div>
                      );
                      continue;
                    }
                    if (event.kind === 'commandResult') {
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <div className="flex items-center gap-2">
                            {roleBadge}
                            <div className={`text-xs ${event.status === 'ok' ? 'text-emerald-300' : 'text-red-300'}`}>
                              {event.status === 'ok' ? '成功' : '失败'}
                            </div>
                          </div>
                          <div className="mt-1 text-xs text-gray-400">
                            {event.cwd ? `cwd: ${event.cwd} ` : ''}
                            {typeof event.ms === 'number' ? `· ${event.ms}ms ` : ''}
                            {typeof event.exitCode === 'number' ? `· exit ${event.exitCode}` : ''}
                          </div>
                          {event.lifecycle === 'open' ? (
                            <div className="mt-1 text-xs text-blue-300">流式输出中...</div>
                          ) : null}
                        </div>
                      );
                      continue;
                    }
                    if (event.kind === 'exec') {
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <div className="flex items-center gap-2 text-xs text-gray-400">
                            {roleBadge}
                            {!roleBadge ? <span>执行</span> : null}
                          </div>
                          <div className="text-sm text-gray-200 break-all">{event.cmd}</div>
                          <div className="mt-1 text-xs text-gray-400">
                            {event.cwd ? `cwd: ${event.cwd} ` : ''}
                            {typeof event.ms === 'number' ? `· ${event.ms}ms ` : ''}
                            {typeof event.exitCode === 'number' ? `· exit ${event.exitCode}` : ''}
                          </div>
                        </div>
                      );
                      continue;
                    }
                    if (event.kind === 'tool') {
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <div className="flex items-center gap-2 text-xs text-gray-400">
                            {roleBadge}
                            <span>器用</span>
                          </div>
                          <div className="text-sm text-gray-200">{event.tool} · {event.phase}</div>
                          {event.message ? <div className="mt-1 text-xs text-gray-300 whitespace-pre-wrap break-all">{event.message}</div> : null}
                        </div>
                      );
                      continue;
                    }
                    if (event.kind === 'table') {
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <div className="flex items-center gap-2 text-xs text-gray-400">
                            {roleBadge}
                            <span>目录</span>
                          </div>
                          <div className="text-xs text-gray-400">{event.title || ''}</div>
                          <div className="mt-2 overflow-auto">
                            <table className="w-full text-xs text-gray-200">
                              <thead>
                                <tr>
                                  {event.columns.map((c, ci) => (
                                    <th key={ci} className="text-left font-medium pr-4">{c}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {event.rows.map((r, ri) => (
                                  <tr key={ri}>
                                    {r.map((cell, ci) => (
                                      <td key={ci} className="pr-4 py-0.5">{cell}</td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      );
                      continue;
                    }
                    if (event.kind === 'fileContent') {
                      const markupKind = detectMarkupKind(event.content, event.pathHint);
                      if (markupKind) {
                        nodes.push(
                          <MarkupCard
                            key={event.id}
                            title={`文件 ${event.pathHint || ''}`}
                            source={event.content}
                            kind={markupKind}
                            badge={roleBadge}
                            meta={event.encodingWarning ? '编码告警' : undefined}
                          />
                        );
                        continue;
                      }
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <div className="flex items-center gap-2 text-xs text-gray-400">
                            {roleBadge}
                            <span>文件 {event.pathHint || ''} {event.encodingWarning ? ' · 编码告警' : ''}</span>
                          </div>
                          {event.lifecycle === 'open' ? (
                            <div className="mt-1 text-xs text-blue-300">流式输出中...</div>
                          ) : null}
                          <pre className="mt-2 text-xs text-gray-200 whitespace-pre-wrap break-all">{event.content || '(空)'}</pre>
                        </div>
                      );
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
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          {isTokensUsed ? (
                            <>
                              <div className="flex items-center gap-2 text-xs text-gray-400">
                                {roleBadge}
                                <span className="uppercase tracking-wider text-[10px] text-cyan-200/90">词元耗用</span>
                                {compact ? (
                                  <span className="ml-auto rounded-full border border-fuchsia-400/20 bg-fuchsia-500/10 px-2 py-0.5 text-[10px] text-fuchsia-200">
                                    {compact}
                                  </span>
                                ) : null}
                              </div>
                              <div className="mt-2 flex items-end justify-between gap-3">
                                <div className="text-2xl font-semibold leading-none text-transparent bg-clip-text bg-gradient-to-r from-cyan-300 via-fuchsia-300 to-pink-300">
                                  {formatted}
                                </div>
                                <div className="text-[11px] text-gray-400">词元</div>
                              </div>
                              <div className="mt-2 h-1.5 w-full rounded-full bg-gray-800/80 overflow-hidden">
                                <div
                                  className="h-full w-full bg-gradient-to-r from-cyan-400/60 via-fuchsia-400/50 to-pink-400/50"
                                  style={{ boxShadow: '0 0 18px rgba(34,211,238,.22), 0 0 28px rgba(168,85,247,.18)' }}
                                />
                              </div>
                            </>
                          ) : (
                            <>
                              <div className="flex items-center gap-2 text-xs text-gray-400">
                                {roleBadge}
                                <span className="uppercase tracking-wider text-[10px]">{event.label || '指标'}</span>
                              </div>
                              <div className="mt-1 text-sm text-gray-200">
                                <span className="font-semibold text-emerald-200">{event.value}</span>
                              </div>
                            </>
                          )}
                        </div>
                      );
                      continue;
                    }
                    if (event.kind === 'thinking') {
                      const cleanBody = stripLlmTags(event.body);
                      nodes.push(
                        <details key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          <summary className="cursor-pointer text-sm text-purple-200 flex items-center gap-2">
                            {roleBadge}
                            <span>{event.title || '思考'}</span>
                          </summary>
                          <div className="mt-2 text-xs text-gray-200">
                            {cleanBody ? <div className="mt-2 whitespace-pre-wrap break-all text-gray-200">{cleanBody}</div> : null}
                          </div>
                        </details>
                      );
                      continue;
                    }
                    if (event.kind === 'error') {
                      nodes.push(
                        <details key={event.id} className="rounded border border-red-500/40 bg-red-500/10 p-3">
                          <summary className="cursor-pointer text-sm text-red-200 flex items-center gap-2">
                            {roleBadge}
                            <span>{event.errorType}</span>
                          </summary>
                          <pre className="mt-2 text-xs text-red-100 whitespace-pre-wrap break-all">{event.raw}</pre>
                        </details>
                      );
                      continue;
                    }
                    if (event.kind === 'text') {
                      const cleanText = stripLlmTags(event.text);
                      if (!cleanText) continue;
                      const markupKind = detectMarkupKind(cleanText);
                      if (markupKind) {
                        nodes.push(
                          <MarkupCard
                            key={event.id}
                            title="标记内容"
                            source={cleanText}
                            kind={markupKind}
                            badge={roleBadge}
                          />
                        );
                        continue;
                      }
                      nodes.push(
                        <div key={event.id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
                          {roleBadge ? <div className="mb-1">{roleBadge}</div> : null}
                          <div className="text-xs text-gray-200 whitespace-pre-wrap break-all">{cleanText}</div>
                        </div>
                      );
                      continue;
                    }
                  }
                  return nodes;
                })()
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

