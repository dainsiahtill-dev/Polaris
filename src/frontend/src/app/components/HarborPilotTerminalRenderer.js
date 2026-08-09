import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from 'react';
/* ----------------------------- Helpers ----------------------------- */
const RUN_HEADER_RE = /^##\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+\(iteration\s+(\d+)\)\s+-\s+(.*)\s*$/;
const RUN_HEADER_RE_ALT = /^##\s+Run\s+(\d+)\s+-\s+(.+)\s*$/;
const TAG_LINE_RE = /^\[([a-zA-Z0-9_-]+)\]\s*(.*)$/;
function safeJsonParse(raw) {
    try {
        return { value: JSON.parse(raw) };
    }
    catch (e) {
        return { error: e instanceof Error ? e.message : String(e) };
    }
}
/**
 * Streaming brace scanner that ignores braces inside JSON strings.
 * This is sufficient for pretty-printed JSON blocks like your sample.
 */
function scanJsonLineByLine(state, line) {
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
            }
            else if (ch === '"') {
                state.inString = false;
            }
            continue;
        }
        if (ch === '"') {
            state.inString = true;
            continue;
        }
        if (ch === '{')
            state.depth += 1;
        else if (ch === '}')
            state.depth -= 1;
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
function initParserState() {
    return {
        carry: '',
        json: { open: false, depth: 0, inString: false, escape: false, buf: [] },
        metaEmitted: false,
    };
}
function cloneParserState(state) {
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
export function parsePolarisChunk(chunk, prevState) {
    const state = prevState ? cloneParserState(prevState) : initParserState();
    const tokens = [];
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
            }
            else {
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
function groupRuns(tokens) {
    let meta;
    const runs = [];
    let current = null;
    const tail = [];
    for (const t of tokens) {
        if (t.kind === 'meta' && !meta) {
            meta = t;
            continue;
        }
        if (t.kind === 'run_header') {
            // close previous
            if (current)
                runs.push(current);
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
            if (Number.isFinite(n))
                current.exitCode = n;
        }
    }
    if (current)
        runs.push(current);
    return { meta, runs, tail };
}
/* ----------------------------- UI Components ----------------------------- */
function Badge({ children, tone = 'neutral', }) {
    const cls = tone === 'ok'
        ? 'bg-green-600/[0.15] text-green-300 border-green-600/30'
        : tone === 'warn'
            ? 'bg-yellow-600/[0.15] text-yellow-300 border-yellow-600/30'
            : tone === 'fail'
                ? 'bg-red-600/[0.15] text-red-300 border-red-600/30'
                : 'bg-slate-600/[0.15] text-slate-200 border-slate-600/30';
    return _jsx("span", { className: `inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${cls}`, children: children });
}
function CopyButton({ text }) {
    const [copied, setCopied] = useState(false);
    return (_jsx("button", { className: "rounded-md border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800", onClick: async () => {
            try {
                await navigator.clipboard.writeText(text);
                setCopied(true);
                setTimeout(() => setCopied(false), 900);
            }
            catch {
                // ignore clipboard errors
            }
        }, title: "\u590D\u5236", children: copied ? '已复制' : '复制' }));
}
function JsonViewer({ token }) {
    const [open, setOpen] = useState(true);
    const isOpenJson = token.open === true;
    const hasError = !!token.error;
    // Prefer pretty stringify if parsed is available and no error; else show raw
    const display = useMemo(() => {
        if (token.parsed !== undefined && !hasError) {
            try {
                return JSON.stringify(token.parsed, null, 2);
            }
            catch {
                return token.raw;
            }
        }
        return token.raw;
    }, [token.parsed, token.raw, hasError]);
    return (_jsxs("div", { className: "soft-inset rounded-lg", children: [_jsxs("div", { className: "flex items-center justify-between gap-2 border-b border-white/5 px-3 py-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Badge, { tone: hasError ? 'fail' : isOpenJson ? 'warn' : 'ok', children: "JSON" }), isOpenJson ? _jsx("span", { className: "text-xs text-slate-400", children: "\u89E3\u6790\u4E2D..." }) : null, hasError ? _jsxs("span", { className: "text-xs text-red-300", children: ["JSON \u65E0\u6548\uFF1A", token.error] }) : null] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(CopyButton, { text: display }), _jsx("button", { className: "text-xs text-slate-300 hover:text-white", onClick: () => setOpen((v) => !v), children: open ? '收起' : '展开' })] })] }), open ? (_jsx("pre", { className: "max-h-[520px] overflow-auto whitespace-pre-wrap px-3 py-2 text-xs leading-relaxed text-slate-200", children: display })) : null] }));
}
function TagLine({ t }) {
    const tag = t.tag.toLowerCase();
    const tone = tag === 'exit' ? (t.text.trim() === '0' ? 'ok' : 'fail') : tag === 'cmd' ? 'neutral' : 'neutral';
    if (tag === 'cmd') {
        return (_jsxs("div", { className: "soft-inset rounded-lg p-3", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsx(Badge, { tone: "neutral", children: "cmd" }), _jsx(CopyButton, { text: t.text })] }), _jsx("pre", { className: "overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-200", children: t.text })] }));
    }
    if (tag === 'exit') {
        const code = t.text.trim();
        return (_jsxs("div", { className: "flex items-center gap-2 soft-inset rounded-lg px-3 py-2", children: [_jsx(Badge, { tone: tone, children: `exit ${code}` }), code !== '0' ? (_jsx("span", { className: "text-xs text-red-300", children: "\u8FDB\u7A0B\u5931\u8D25" })) : (_jsx("span", { className: "text-xs text-slate-400", children: "\u6B63\u5E38" }))] }));
    }
    return (_jsx("div", { className: "soft-inset rounded-lg px-3 py-2", children: _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Badge, { tone: "neutral", children: t.tag }), _jsx("span", { className: "text-xs text-slate-200", children: t.text })] }) }));
}
function RunCard({ run }) {
    const exitTone = run.exitCode === undefined ? 'neutral' : run.exitCode === 0 ? 'ok' : 'fail';
    return (_jsxs("div", { className: "soft-panel rounded-lg p-4", children: [_jsxs("div", { className: "mb-3 flex flex-wrap items-center justify-between gap-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("h3", { className: "text-sm font-semibold text-slate-100", children: run.header.raw.replace(/^##\s+/, '') }), typeof run.header.iteration === 'number' ? _jsxs(Badge, { children: ["it ", run.header.iteration] }) : null, run.header.phase ? _jsx(Badge, { children: run.header.phase }) : null] }), run.exitCode !== undefined ? _jsxs(Badge, { tone: exitTone, children: ["exit ", run.exitCode] }) : _jsx(Badge, { children: "\u8FD0\u884C\u4E2D" })] }), _jsx("div", { className: "space-y-3", children: run.entries.map((t, idx) => renderToken(t, idx)) })] }));
}
function renderToken(t, idx) {
    if (t.kind === 'blank')
        return null;
    if (t.kind === 'tag_line')
        return _jsx(TagLine, { t: t }, idx);
    if (t.kind === 'json')
        return _jsx(JsonViewer, { token: t }, idx);
    if (t.kind === 'text')
        return (_jsx("pre", { className: "soft-inset rounded-lg px-3 py-2 text-xs text-slate-200", children: t.text }, idx));
    return null;
}
/**
 * For streaming logs: you can keep appending to `text`.
 * This component re-parses incrementally to avoid O(n^2) for huge logs.
 */
export function PolarisTerminalRenderer({ text, className }) {
    const [tokens, setTokens] = useState([]);
    const stateRef = useRef(initParserState());
    const lastLenRef = useRef(0);
    useEffect(() => {
        // incremental: only parse the newly appended part
        const lastLen = lastLenRef.current;
        const nextLen = text.length;
        const chunk = nextLen >= lastLen ? text.slice(lastLen) : text; // if reset, parse all
        const { tokens: newTokens, state } = parsePolarisChunk(chunk, nextLen >= lastLen ? stateRef.current : initParserState());
        stateRef.current = state;
        lastLenRef.current = nextLen;
        setTokens((prev) => (nextLen >= lastLen ? [...prev, ...newTokens] : newTokens));
    }, [text]);
    const { meta, runs, tail } = useMemo(() => groupRuns(tokens), [tokens]);
    return (_jsxs("div", { className: className ?? '', children: [_jsxs("div", { className: "mb-3 flex flex-wrap items-center gap-2", children: [_jsx(Badge, { children: "Polaris" }), meta?.kind === 'meta' ? _jsx(Badge, { tone: "neutral", children: meta.text }) : null, runs.length > 0 ? _jsxs(Badge, { tone: "neutral", children: [runs.length, " \u8F6E"] }) : null] }), tail.length > 0 ? (_jsxs("div", { className: "mb-4 space-y-2 soft-inset rounded-lg px-3 py-2", children: [_jsx("div", { className: "text-xs text-slate-400", children: "\u542F\u52A8\u524D\u8F93\u51FA" }), tail.map((t, i) => renderToken(t, i))] })) : null, _jsx("div", { className: "space-y-4", children: runs.map((r, idx) => (_jsx(RunCard, { run: r }, idx))) })] }));
}
