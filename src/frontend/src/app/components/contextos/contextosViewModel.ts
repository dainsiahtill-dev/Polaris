/**
 * contextosViewModel — 纯函数视图模型（无框架依赖）。
 *
 * 用途：ContextViewerModal 的派生数据/格式工具，保持组件文件只做渲染，
 *       纯逻辑可单测。
 *
 * Token 估算口径：1 token ≈ 3.5 字符（CJK 友好，比后端 taxonomy.py:181 的 1/4 略保守）。
 *   后端采用 1 token ≈ 4 字符；此处 1/3.5 偏大、对中文更准。chip 已标注 (估算)。
 */

export type CodeFenceKind = 'text' | 'fence';

export interface CodeFenceSegment {
  kind: CodeFenceKind;
  lang?: string;
  body: string;
}

export type HighlightKind = 'kw' | 'str' | 'num' | 'cmt' | 'punct' | 'plain';

export interface HighlightToken {
  kind: HighlightKind;
  v: string;
}

// ---------------------------------------------------------------------------
// Mirror of ContextViewerModal's wire types (kept here so view-model is fully
// self-contained for tests; component re-uses its own inline interfaces).
// ---------------------------------------------------------------------------

export interface ViewModelMessage {
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

export interface ViewModelPayload {
  schema_version: number;
  hash: string;
  trace_id: string | null;
  call_id: string | null;
  messages: ViewModelMessage[];
  stored_at: string | null;
  message_count: number;
  total_chars: number;
}

// ---------------------------------------------------------------------------
// estimateTokens — 字符数 / 3.5 向上取整，至少 1（CJK 友好）。
// ---------------------------------------------------------------------------

export function estimateTokens(text: string): number {
  if (!text) return 1;
  const len = text.length;
  if (len <= 0) return 1;
  return Math.max(1, Math.ceil(len / 3.5));
}

// ---------------------------------------------------------------------------
// parseCodeFences — 匹配 ```lang\n...\n```，其余按 plain text 段返回。
// ---------------------------------------------------------------------------

const FENCE_RE = /```([A-Za-z0-9_+\-#.]*)\n([\s\S]*?)\n```/g;

export function parseCodeFences(text: string): CodeFenceSegment[] {
  if (!text) return [];
  const out: CodeFenceSegment[] = [];
  let lastIndex = 0;
  FENCE_RE.lastIndex = 0;
  let match: RegExpExecArray | null = FENCE_RE.exec(text);
  while (match !== null) {
    if (match.index > lastIndex) {
      out.push({ kind: 'text', body: text.slice(lastIndex, match.index) });
    }
    const lang = match[1] ?? '';
    const body = match[2] ?? '';
    out.push({ kind: 'fence', lang: lang || undefined, body });
    lastIndex = match.index + match[0].length;
    match = FENCE_RE.exec(text);
  }
  if (lastIndex < text.length) {
    out.push({ kind: 'text', body: text.slice(lastIndex) });
  }
  return out;
}

// ---------------------------------------------------------------------------
// highlightInline — per-language regex map；不在 map 内则返回 [{plain: text}]。
// ---------------------------------------------------------------------------

interface LangRule {
  keywords?: RegExp;
  string?: RegExp;
  number?: RegExp;
  comment?: RegExp;
}

const LANG_RULES: Record<string, LangRule> = {
  json: {
    string: /"(?:\\.|[^"\\])*"/g,
    number: /\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g,
    keywords: /\b(?:true|false|null)\b/g,
  },
  python: {
    keywords: /\b(?:def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|lambda|yield|in|not|and|or|is|None|True|False|pass|break|continue|raise)\b/g,
    string: /(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/g,
    number: /\b-?\d+(?:\.\d+)?\b/g,
    comment: /#.*/g,
  },
  bash: {
    keywords: /\b(?:if|then|else|elif|fi|for|in|do|done|while|case|esac|function|return|export|local|set|unset)\b/g,
    string: /(?:"(?:\\.|[^"\\])*"|'[^']*')/g,
    comment: /#.*/g,
  },
  sql: {
    keywords: /\b(?:SELECT|FROM|WHERE|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|INDEX|VIEW|DROP|ALTER|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|GROUP|BY|ORDER|LIMIT|OFFSET|UNION|DISTINCT|COUNT|SUM|AVG|MAX|MIN|HAVING|NULL|IS|NOT|AND|OR|IN|LIKE|BETWEEN)\b/gi,
    string: /'(?:''|[^'])*'/g,
    number: /\b-?\d+(?:\.\d+)?\b/g,
    comment: /--.*/g,
  },
};

const PUNCT_RE = /[{}[\](),:;.<>=+\-*/%!?&|~^]/g;

export function highlightInline(text: string, lang?: string): HighlightToken[] {
  if (!text) return [];
  const rule = lang ? LANG_RULES[lang.toLowerCase()] : undefined;
  if (!rule) {
    return [{ kind: 'plain', v: text }];
  }

  // Build a list of matches with priorities (lower = higher priority).
  type Tagged = { start: number; end: number; kind: HighlightKind };
  const tags: Tagged[] = [];

  if (rule.string) {
    rule.string.lastIndex = 0;
    let m: RegExpExecArray | null = rule.string.exec(text);
    while (m !== null) {
      tags.push({ start: m.index, end: m.index + m[0].length, kind: 'str' });
      m = rule.string.exec(text);
    }
  }
  if (rule.comment) {
    rule.comment.lastIndex = 0;
    let m: RegExpExecArray | null = rule.comment.exec(text);
    while (m !== null) {
      tags.push({ start: m.index, end: m.index + m[0].length, kind: 'cmt' });
      m = rule.comment.exec(text);
    }
  }
  if (rule.number) {
    rule.number.lastIndex = 0;
    let m: RegExpExecArray | null = rule.number.exec(text);
    while (m !== null) {
      tags.push({ start: m.index, end: m.index + m[0].length, kind: 'num' });
      m = rule.number.exec(text);
    }
  }
  if (rule.keywords) {
    rule.keywords.lastIndex = 0;
    let m: RegExpExecArray | null = rule.keywords.exec(text);
    while (m !== null) {
      tags.push({ start: m.index, end: m.index + m[0].length, kind: 'kw' });
      m = rule.keywords.exec(text);
    }
  }

  if (tags.length === 0) {
    return [{ kind: 'plain', v: text }];
  }

  // Sort by start, then drop overlaps (keep first).
  tags.sort((a, b) => a.start - b.start || b.end - a.end);
  const filtered: Tagged[] = [];
  let cursor = 0;
  for (const tag of tags) {
    if (tag.start < cursor) continue;
    filtered.push(tag);
    cursor = tag.end;
  }

  const out: HighlightToken[] = [];
  let pos = 0;
  for (const tag of filtered) {
    if (tag.start > pos) {
      out.push({ kind: 'plain', v: text.slice(pos, tag.start) });
    }
    out.push({ kind: tag.kind, v: text.slice(tag.start, tag.end) });
    pos = tag.end;
  }
  if (pos < text.length) {
    out.push({ kind: 'plain', v: text.slice(pos) });
  }

  // Tag punctuation as a separate pass — only within 'plain' tokens (cheap).
  return out.map((tok) => {
    if (tok.kind !== 'plain') return tok;
    const local: HighlightToken[] = [];
    let i = 0;
    PUNCT_RE.lastIndex = 0;
    let m: RegExpExecArray | null = PUNCT_RE.exec(tok.v);
    while (m !== null) {
      if (m.index > i) {
        local.push({ kind: 'plain', v: tok.v.slice(i, m.index) });
      }
      local.push({ kind: 'punct', v: m[0] });
      i = m.index + m[0].length;
      m = PUNCT_RE.exec(tok.v);
    }
    if (i < tok.v.length) {
      local.push({ kind: 'plain', v: tok.v.slice(i) });
    }
    return local.length === 1 && local[0] && local[0].kind === 'plain' ? tok : local;
  }).flat();
}

// ---------------------------------------------------------------------------
// prettyJsonOrNull — 尝试 JSON.parse，pretty-print；失败返回 null。
// ---------------------------------------------------------------------------

export function prettyJsonOrNull(raw: string): string | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// buildMessageMarkdown — 单条消息的 Markdown 片段。
// ---------------------------------------------------------------------------

const ROLE_LABELS: Record<string, string> = {
  system: '系统提示',
  user: '用户',
  assistant: '助手',
  tool: '工具结果',
};

function labelOf(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

function fenced(content: string, lang?: string): string {
  return '```' + (lang ?? '') + '\n' + content + '\n```';
}

export function buildMessageMarkdown(idx: number, m: ViewModelMessage, et: number): string {
  const header = `#${idx + 1} [${labelOf(m.role)}] (~${et} tokens)`;
  const parts: string[] = [header];
  if (m.content !== null && m.content !== undefined) {
    parts.push(fenced(m.content));
  }
  if (m.tool_calls && m.tool_calls.length > 0) {
    for (const tc of m.tool_calls) {
      const name = tc.function?.name ?? tc.type ?? 'tool_call';
      const args = tc.function?.arguments;
      const block: string[] = [`tool_call: ${name}`];
      if (args !== undefined && args !== null && args !== '') {
        const pretty = prettyJsonOrNull(args);
        block.push(pretty !== null ? fenced(pretty, 'json') : fenced(args));
      }
      parts.push(block.join('\n'));
    }
  }
  if (m.name) parts.push(`name: ${m.name}`);
  if (m.tool_call_id) parts.push(`tool_call_id: ${m.tool_call_id}`);
  return parts.join('\n\n');
}

// ---------------------------------------------------------------------------
// buildFullMarkdown — 整个 payload 的 Markdown 文档。
// ---------------------------------------------------------------------------

export function buildFullMarkdown(payload: ViewModelPayload): string {
  const metaLines = [
    `# Context Snapshot ${payload.hash}`,
    '',
    `- schema_version: ${payload.schema_version}`,
    `- call_id: ${payload.call_id ?? '—'}`,
    `- trace_id: ${payload.trace_id ?? '—'}`,
    `- stored_at: ${payload.stored_at ?? '—'}`,
    `- message_count: ${payload.message_count}`,
    `- total_chars: ${payload.total_chars.toLocaleString()}`,
    '',
  ];
  const meta = metaLines.join('\n');

  const blocks = payload.messages.map((m, idx) => buildMessageMarkdown(idx, m, estimateTokens(m.content ?? '')));
  return meta + blocks.join('\n\n---\n\n');
}