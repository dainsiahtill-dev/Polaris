import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

/**
 * Polaris terminal smart renderer
 * - Incremental parsing (streaming chunks)
 * - Detects run headers, bracketed tags ([cmd],[director],[exit]), JSON blocks
 * - Groups into Run cards
 */

/* ----------------------------- Types ----------------------------- */

export type HPToken =
  | { kind: 'meta'; text: string }
  | { kind: 'run_header'; raw: string; ts?: string; iteration?: number; phase?: string }
  | { kind: 'tag_line'; tag: string; text: string; raw: string }
  | { kind: 'json'; raw: string; parsed?: unknown; open: boolean; error?: string }
  | { kind: 'text'; text: string }
  | { kind: 'blank' };

export type HPRun = {
  header: Extract<HPToken, { kind: 'run_header' }>;
  entries: HPToken[];
  exitCode?: number;
};

type JsonScanState = {
  open: boolean;
  depth: number; // brace depth
  inString: boolean;
  escape: boolean;
  buf: string[];
};

export type HPParserState = {
  // carry-over for partial line boundary across chunks
  carry: string;
  // currently scanning a JSON block
  json: JsonScanState;
  // optional: last meta captured (like leading "JSON")
  metaEmitted: boolean;
};

/* ----------------------------- Helpers ----------------------------- */

const RUN_HEADER_RE =
  /^##\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+\(iteration\s+(\d+)\)\s+-\s+(.*)\s*$/;
const RUN_HEADER_RE_ALT = /^##\s+Run\s+(\d+)\s+-\s+(.+)\s*$/;

const TAG_LINE_RE = /^\[([a-zA-Z0-9_-]+)\]\s*(.*)$/;

function safeJsonParse(raw: string): { value?: unknown; error?: string } {
  try {
    return { value: JSON.parse(raw) };
  } catch (e: unknown) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
}

/**
 * Streaming brace scanner that ignores braces inside JSON strings.
 * This is sufficient for pretty-printed JSON blocks like your sample.
 */
function scanJsonLineByLine(state: JsonScanState, line: string): { done: boolean } {
  // append line (with newline) to buffer
  state.buf.push(line);

  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];

    if (state.escape) {
      state.escape = false;
      continue;
    }

    if (state.inString) {
      if (ch === '\\') {
        state.escape = true;
      } else if (ch === '"') {
        state.inString = false;
      }
      continue;
    }

    if (ch === '"') {
      state.inString = true;
      continue;
    }

    if (ch === '{') state.depth += 1;
    else if (ch === '}') state.depth -= 1;
  }

  // If depth hits 0 AND we have opened at least once, JSON is complete.
  // (depth can go negative if log is corrupted; clamp to avoid infinite open)
  if (state.depth <= 0) {
    state.depth = 0;
    state.open = false;
    state.inString = false;
    state.escape = false;
    return { done: true };
  }

  return { done: false };
}

function initParserState(): HPParserState {
  return {
    carry: '',
    json: { open: false, depth: 0, inString: false, escape: false, buf: [] },
    metaEmitted: false,
  };
}

function cloneParserState(state: HPParserState): HPParserState {
  return {
    carry: state.carry,
    metaEmitted: state.metaEmitted,
    json: {
      open: state.json.open,
      depth: state.json.depth,
      inString: state.json.inString,
      escape: state.json.escape,
      buf: [...state.json.buf],
    },
  };
}

/* ----------------------------- Parser ----------------------------- */

/**
 * Parse a chunk incrementally.
 * - Keeps partial line in state.carry
 * - If JSON scanning is open, consumes lines until closed
 */
export function parsePolarisChunk(
  chunk: string,
  prevState?: HPParserState
): { tokens: HPToken[]; state: HPParserState } {
  const state = prevState ? cloneParserState(prevState) : initParserState();
  const tokens: HPToken[] = [];

  const text = state.carry + chunk;
  const lines = text.split(/\r?\n/);

  // if chunk doesn't end with newline, keep last as carry
  const endsWithNewline = /\r?\n$/.test(text);
  state.carry = endsWithNewline ? '' : lines.pop() ?? '';

  for (const rawLine of lines) {
    const line = rawLine; // keep as-is (no trimming) for pre blocks

    // If currently inside JSON block, keep scanning until close.
    if (state.json.open) {
      const { done } = scanJsonLineByLine(state.json, `${line}\n`);
      if (done) {
        const raw = state.json.buf.join('');
        const parsedAttempt = safeJsonParse(raw.trim());
        tokens.push({
          kind: 'json',
          raw,
          parsed: parsedAttempt.value,
          open: false,
          error: parsedAttempt.error,
        });
        state.json.buf = [];
      }
      continue;
    }

    // Blank line
    if (line.length === 0) {
      tokens.push({ kind: 'blank' });
      continue;
    }

    // Special meta: first non-empty line equals "JSON"
    if (!state.metaEmitted && line.trim() === 'JSON') {
      tokens.push({ kind: 'meta', text: 'JSON' });
      state.metaEmitted = true;
      continue;
    }

    // Run header
    const mh = line.match(RUN_HEADER_RE);
    if (mh) {
      tokens.push({
        kind: 'run_header',
        raw: line,
        ts: mh[1],
        iteration: Number(mh[2]),
        phase: mh[3],
      });
      continue;
    }
    const mhAlt = line.match(RUN_HEADER_RE_ALT);
    if (mhAlt) {
      tokens.push({
        kind: 'run_header',
        raw: line,
        ts: mhAlt[2],
        iteration: Number(mhAlt[1]),
        phase: 'run',
      });
      continue;
    }

    // Tagged lines like [cmd] [director] [exit]
    const mt = line.match(TAG_LINE_RE);
    if (mt) {
      const tag = mt[1];
      const textValue = mt[2] ?? '';
      tokens.push({ kind: 'tag_line', tag, text: textValue, raw: line });
      continue;
    }

    // JSON block start heuristic:
    // - In your log, JSON starts on a line that is exactly "{" OR starts with "{"
    // - Keep it strict to avoid treating object-like text as JSON accidentally.
    const trimmed = line.trimStart();
    if (trimmed === '{' || trimmed.startsWith('{')) {
      // open JSON scanning
      state.json.open = true;
      state.json.depth = 0;
      state.json.inString = false;
      state.json.escape = false;
      state.json.buf = [];

      // scan this first line
      const { done } = scanJsonLineByLine(state.json, `${line}\n`);
      if (done) {
        const raw = state.json.buf.join('');
        const parsedAttempt = safeJsonParse(raw.trim());
        tokens.push({
          kind: 'json',
          raw,
          parsed: parsedAttempt.value,
          open: false,
          error: parsedAttempt.error,
        });
        state.json.buf = [];
      } else {
        tokens.push({ kind: 'json', raw: `${line}\n`, open: true });
      }
      continue;
    }

    // Fallback plain text
    tokens.push({ kind: 'text', text: line });
  }

  return { tokens, state };
}

/* ----------------------------- Grouping ----------------------------- */

function groupRuns(tokens: HPToken[]): { meta?: HPToken; runs: HPRun[]; tail: HPToken[] } {
  let meta: HPToken | undefined;
  const runs: HPRun[] = [];
  let current: HPRun | null = null;
  const tail: HPToken[] = [];

  for (const t of tokens) {
    if (t.kind === 'meta' && !meta) {
      meta = t;
      continue;
    }

    if (t.kind === 'run_header') {
      // close previous
      if (current) runs.push(current);
      current = { header: t, entries: [] };
      continue;
    }

    // if no header yet, keep in tail (pre-run noise)
    if (!current) {
      tail.push(t);
      continue;
    }

    current.entries.push(t);

    // track exit code if possible
    if (t.kind === 'tag_line' && t.tag.toLowerCase() === 'exit') {
      const n = Number((t.text ?? '').trim());
      if (Number.isFinite(n)) current.exitCode = n;
    }
  }

  if (current) runs.push(current);
  return { meta, runs, tail };
}

/* ----------------------------- UI Components ----------------------------- */

function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'ok' | 'warn' | 'fail';
}) {
  const cls =
    tone === 'ok'
      ? 'bg-green-600/15 text-green-300 border-green-600/30'
      : tone === 'warn'
        ? 'bg-yellow-600/15 text-yellow-300 border-yellow-600/30'
        : tone === 'fail'
          ? 'bg-red-600/15 text-red-300 border-red-600/30'
          : 'bg-slate-600/15 text-slate-200 border-slate-600/30';
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${cls}`}>{children}</span>;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="rounded-md border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 900);
        } catch {
          // ignore clipboard errors
        }
      }}
      title="复制"
    >
      {copied ? '已复制' : '复制'}
    </button>
  );
}

function JsonViewer({ token }: { token: Extract<HPToken, { kind: 'json' }> }) {
  const [open, setOpen] = useState(true);

  const isOpenJson = token.open === true;
  const hasError = !!token.error;

  // Prefer pretty stringify if parsed is available and no error; else show raw
  const display = useMemo(() => {
    if (token.parsed !== undefined && !hasError) {
      try {
        return JSON.stringify(token.parsed, null, 2);
      } catch {
        return token.raw;
      }
    }
    return token.raw;
  }, [token.parsed, token.raw, hasError]);

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/60">
      <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-2">
        <div className="flex items-center gap-2">
          <Badge tone={hasError ? 'fail' : isOpenJson ? 'warn' : 'ok'}>JSON</Badge>
          {isOpenJson ? <span className="text-xs text-slate-400">解析中...</span> : null}
          {hasError ? <span className="text-xs text-red-300">JSON 无效：{token.error}</span> : null}
        </div>
        <div className="flex items-center gap-2">
          <CopyButton text={display} />
          <button className="text-xs text-slate-300 hover:text-white" onClick={() => setOpen((v) => !v)}>
            {open ? '收起' : '展开'}
          </button>
        </div>
      </div>
      {open ? (
        <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap px-3 py-2 text-xs leading-relaxed text-slate-200">
          {display}
        </pre>
      ) : null}
    </div>
  );
}

function TagLine({ t }: { t: Extract<HPToken, { kind: 'tag_line' }> }) {
  const tag = t.tag.toLowerCase();
  const tone = tag === 'exit' ? (t.text.trim() === '0' ? 'ok' : 'fail') : tag === 'cmd' ? 'neutral' : 'neutral';

  if (tag === 'cmd') {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <Badge tone="neutral">cmd</Badge>
          <CopyButton text={t.text} />
        </div>
        <pre className="overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-200">{t.text}</pre>
      </div>
    );
  }

  if (tag === 'exit') {
    const code = t.text.trim();
    return (
      <div className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2">
        <Badge tone={tone}>{`exit ${code}`}</Badge>
        {code !== '0' ? (
          <span className="text-xs text-red-300">进程失败</span>
        ) : (
          <span className="text-xs text-slate-400">正常</span>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2">
      <div className="flex items-center gap-2">
        <Badge tone="neutral">{t.tag}</Badge>
        <span className="text-xs text-slate-200">{t.text}</span>
      </div>
    </div>
  );
}

function RunCard({ run }: { run: HPRun }) {
  const exitTone = run.exitCode === undefined ? 'neutral' : run.exitCode === 0 ? 'ok' : 'fail';

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-100">{run.header.raw.replace(/^##\s+/, '')}</h3>
          {typeof run.header.iteration === 'number' ? <Badge>it {run.header.iteration}</Badge> : null}
          {run.header.phase ? <Badge>{run.header.phase}</Badge> : null}
        </div>
        {run.exitCode !== undefined ? <Badge tone={exitTone}>exit {run.exitCode}</Badge> : <Badge>运行中</Badge>}
      </div>

      <div className="space-y-3">
        {run.entries.map((t, idx) => renderToken(t, idx))}
      </div>
    </div>
  );
}

function renderToken(t: HPToken, idx: number) {
  if (t.kind === 'blank') return null;
  if (t.kind === 'tag_line') return <TagLine key={idx} t={t} />;
  if (t.kind === 'json') return <JsonViewer key={idx} token={t} />;
  if (t.kind === 'text')
    return (
      <pre key={idx} className="rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-200">
        {t.text}
      </pre>
    );
  return null;
}

/* ----------------------------- Public React Component ----------------------------- */

export type PolarisTerminalRendererProps = {
  /** Raw terminal output (can be the full log, or growing text) */
  text: string;
  /** Optional className wrapper */
  className?: string;
};

/**
 * For streaming logs: you can keep appending to `text`.
 * This component re-parses incrementally to avoid O(n^2) for huge logs.
 */
export function PolarisTerminalRenderer({ text, className }: PolarisTerminalRendererProps) {
  const [tokens, setTokens] = useState<HPToken[]>([]);
  const stateRef = useRef<HPParserState>(initParserState());
  const lastLenRef = useRef(0);

  useEffect(() => {
    // incremental: only parse the newly appended part
    const lastLen = lastLenRef.current;
    const nextLen = text.length;
    const chunk = nextLen >= lastLen ? text.slice(lastLen) : text; // if reset, parse all

    const { tokens: newTokens, state } = parsePolarisChunk(
      chunk,
      nextLen >= lastLen ? stateRef.current : initParserState()
    );
    stateRef.current = state;
    lastLenRef.current = nextLen;

    setTokens((prev) => (nextLen >= lastLen ? [...prev, ...newTokens] : newTokens));
  }, [text]);

  const { meta, runs, tail } = useMemo(() => groupRuns(tokens), [tokens]);

  return (
    <div className={className ?? ''}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Badge>Polaris</Badge>
        {meta?.kind === 'meta' ? <Badge tone="neutral">{meta.text}</Badge> : null}
        {runs.length > 0 ? <Badge tone="neutral">{runs.length} 轮</Badge> : null}
      </div>

      {tail.length > 0 ? (
        <div className="mb-4 space-y-2 rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2">
          <div className="text-xs text-slate-400">启动前输出</div>
          {tail.map((t, i) => renderToken(t, i))}
        </div>
      ) : null}

      <div className="space-y-4">
        {runs.map((r, idx) => (
          <RunCard key={idx} run={r} />
        ))}
      </div>
    </div>
  );
}
