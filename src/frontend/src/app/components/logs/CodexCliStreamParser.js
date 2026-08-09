const SECTION_TITLES = ['GLOBAL REQUIREMENTS', 'CURRENT PLAN', 'GAP REPORT'];
function stripAnsi(text) {
    return text.replace(/\u001b\[[0-9;]*m/g, '');
}
const LLM_XML_TAG_RE = /<\/?(?:minimax:)?(?:tool_call|think(?:ing)?|function_calls?|invoke|parameter|antml:[a-z_]+)\b[^>]*>/gi;
const LLM_SEPARATOR_RE = /^[=\-]{20,}$/;
export function stripLlmTags(text) {
    if (!text)
        return '';
    return text.replace(LLM_XML_TAG_RE, '').trim();
}
function isSectionHeader(line) {
    const trimmed = line.trim();
    if (!trimmed)
        return false;
    if (/^##\s+/.test(trimmed))
        return true;
    if (/^([A-Z0-9][A-Z0-9 _/()-]{2,}):\s*$/.test(trimmed))
        return true;
    return SECTION_TITLES.some((title) => trimmed.toUpperCase().startsWith(title));
}
function isRoleLine(t) {
    return /^(user|thinking|exec)\s*$/i.test(t);
}
function isPsCommandLine(t) {
    return /^"([^"]*powershell\.exe)"\s+-Command\s+/i.test(t);
}
function isCmdRunningLine(t) {
    return /^\[(?:cmd|CMD)\]\s+Running:/i.test(t);
}
function isExecResultLine(t) {
    return (/^(.*) in (.+) (succeeded|failed) in (\d+)ms:?\s*$/i.test(t) ||
        /\bexited\s+(-?\d+)\s+in\s+(\d+)ms\b/i.test(t));
}
function isMcpLine(t) {
    return /^mcp:/i.test(t) || /^mcp startup:/i.test(t);
}
function isSectionHeaderLine(t) {
    return isSectionHeader(t);
}
function isLlmSectionHeader(t) {
    return /^\[(LLM|LLM Response|Thinking\/Reasoning|Output Preview|Token Usage|MiniMax Debug|pm|history|RuntimeConfig|MiniMax)\b/.test(t);
}
function isBoundaryForCaptures(t) {
    const trimmed = t.trim();
    return (isRoleLine(trimmed) ||
        isSectionHeaderLine(trimmed) ||
        isPsCommandLine(trimmed) ||
        isCmdRunningLine(trimmed) ||
        isExecResultLine(trimmed) ||
        isMcpLine(trimmed) ||
        isLlmSectionHeader(trimmed) ||
        LLM_SEPARATOR_RE.test(trimmed) ||
        /^OpenAI Codex v[\d.]+\b/.test(trimmed));
}
export class CodexCliStreamParser {
    constructor() {
        this.events = [];
        this.mode = 'idle';
        this.lastCmd = null;
        this.expectOutput = null;
        this.expectMetricLabel = null;
        this.json = null;
        this.table = null;
        this.file = null;
        this.text = null;
        this.thinking = null;
    }
    push(e) {
        this.events.push(e);
        return this.events.length - 1;
    }
    open(e) {
        e.lifecycle = 'open';
        const idx = this.push(e);
        return { id: e.id, idx };
    }
    closeByIdx(idx) {
        if (idx >= 0 && idx < this.events.length) {
            this.events[idx] = { ...this.events[idx], lifecycle: 'closed' };
        }
    }
    ensureText() {
        if (!this.text) {
            const id = `text-${this.events.length}`;
            const idx = this.push({ id, kind: 'text', text: '', lifecycle: 'open' });
            this.text = { id, idx, buffer: [] };
        }
    }
    flushText() {
        if (!this.text)
            return;
        const { idx, buffer } = this.text;
        const text = buffer.join('\n');
        this.events[idx] = { ...this.events[idx], kind: 'text', text, lifecycle: 'closed' };
        this.text = null;
    }
    flushThinking() {
        if (!this.thinking)
            return;
        const { idx, title, buffer } = this.thinking;
        const body = stripLlmTags(buffer.join('\n'));
        this.events[idx] = { ...this.events[idx], kind: 'thinking', title, body, lifecycle: 'closed' };
        this.thinking = null;
    }
    isThinkingTitleLine(trimmed) {
        return /^\*\*(.+?)\*\*\s*$/.test(trimmed);
    }
    parseMetricValue(text) {
        const m = text.match(/([0-9][0-9,._]*)/);
        return m ? m[1] : null;
    }
    scanJsonFragment(fragment, st) {
        for (let i = 0; i < fragment.length; i += 1) {
            const ch = fragment[i];
            if (!st.started) {
                if (ch === '{' || ch === '[') {
                    st.started = true;
                    st.depth = 1;
                }
                continue;
            }
            if (st.escape) {
                st.escape = false;
                continue;
            }
            if (st.inString) {
                if (ch === '\\')
                    st.escape = true;
                else if (ch === '"')
                    st.inString = false;
                continue;
            }
            if (ch === '"') {
                st.inString = true;
                continue;
            }
            if (ch === '{' || ch === '[')
                st.depth += 1;
            else if (ch === '}' || ch === ']')
                st.depth -= 1;
        }
    }
    isJsonStartLine(stripped) {
        const t = stripped.trimStart();
        if (t === '{' || t === '[')
            return true;
        if (t.startsWith('{"') || t.startsWith('{ "'))
            return true;
        if (t.startsWith('[{') || t.startsWith('[ "'))
            return true;
        return false;
    }
    feedLine(line) {
        const stripped = stripAnsi(line);
        const trimmed = stripped.trim();
        let reprocess = true;
        let currentStripped = stripped;
        let currentTrimmed = trimmed;
        while (reprocess) {
            reprocess = false;
            if (this.thinking) {
                if (isBoundaryForCaptures(currentStripped) || this.isThinkingTitleLine(currentTrimmed)) {
                    this.closeByIdx(this.thinking.idx);
                    this.thinking = null;
                    reprocess = true;
                    continue;
                }
                this.thinking.buffer.push(currentStripped);
                const body = stripLlmTags(this.thinking.buffer.join('\n'));
                this.events[this.thinking.idx] = {
                    ...this.events[this.thinking.idx],
                    title: this.thinking.title,
                    body,
                };
                return;
            }
            if (this.file) {
                if (isBoundaryForCaptures(currentStripped)) {
                    this.closeByIdx(this.file.idx);
                    this.file = null;
                    this.expectOutput = null;
                    reprocess = true;
                    continue;
                }
                this.file.content += (this.file.content ? '\n' : '') + currentStripped;
                const content = this.file.content;
                const hasReplacement = /\uFFFD/.test(content);
                const hasGarbled = /[\u9353\ue06c\u6d30\u9352\u6b0f\u5f72\u95b2\u5c84\u4ebe\u9286?]/.test(content);
                const encodingWarning = hasReplacement || hasGarbled;
                this.events[this.file.idx] = {
                    ...this.events[this.file.idx],
                    content,
                    encodingWarning,
                };
                return;
            }
            if (this.table) {
                if (isBoundaryForCaptures(currentStripped) || currentTrimmed === '') {
                    this.closeByIdx(this.table.idx);
                    this.table = null;
                    this.expectOutput = null;
                    reprocess = currentTrimmed !== '';
                    continue;
                }
                if (this.table.stage === 'awaitHeader') {
                    this.table.columns = currentStripped.trim().split(/\s{2,}/);
                    this.table.stage = 'awaitSep';
                    return;
                }
                if (this.table.stage === 'awaitSep') {
                    if (/-{2,}/.test(currentTrimmed)) {
                        this.table.stage = 'rows';
                        this.events[this.table.idx] = {
                            ...this.events[this.table.idx],
                            columns: [...this.table.columns],
                            rows: [...this.table.rows],
                        };
                        return;
                    }
                    this.closeByIdx(this.table.idx);
                    this.table = null;
                    this.expectOutput = null;
                    reprocess = true;
                    continue;
                }
                const cols = currentStripped.trim().split(/\s{2,}/);
                if (cols.length >= Math.min(2, this.table.columns.length || 2)) {
                    this.table.rows.push(cols);
                    this.events[this.table.idx] = {
                        ...this.events[this.table.idx],
                        columns: [...this.table.columns],
                        rows: [...this.table.rows],
                    };
                    return;
                }
                this.closeByIdx(this.table.idx);
                this.table = null;
                this.expectOutput = null;
                reprocess = true;
                continue;
            }
            if (this.json) {
                if (isBoundaryForCaptures(currentStripped)) {
                    const raw = this.json.raw;
                    try {
                        const val = JSON.parse(raw);
                        this.events[this.json.idx] = {
                            ...this.events[this.json.idx],
                            value: val,
                            raw,
                            lifecycle: 'closed',
                        };
                    }
                    catch {
                        this.events[this.json.idx] = {
                            ...this.events[this.json.idx],
                            raw,
                            lifecycle: 'closed',
                        };
                    }
                    this.json = null;
                    reprocess = true;
                    continue;
                }
                if (this.json.lines > 400) {
                    const raw = this.json.raw;
                    this.events[this.json.idx] = { id: this.json.id, kind: 'text', text: raw, lifecycle: 'closed' };
                    this.json = null;
                    reprocess = true;
                    continue;
                }
                this.json.raw += `\n${currentStripped}`;
                this.json.lines += 1;
                this.scanJsonFragment(`\n${currentStripped}`, this.json);
                this.events[this.json.idx] = { ...this.events[this.json.idx], raw: this.json.raw };
                if (this.json.started && this.json.depth <= 0 && !this.json.inString) {
                    try {
                        const val = JSON.parse(this.json.raw);
                        this.events[this.json.idx] = {
                            ...this.events[this.json.idx],
                            value: val,
                            raw: this.json.raw,
                            lifecycle: 'closed',
                        };
                    }
                    catch {
                        this.events[this.json.idx] = {
                            ...this.events[this.json.idx],
                            raw: this.json.raw,
                            lifecycle: 'closed',
                        };
                    }
                    this.json = null;
                }
                return;
            }
            if (this.expectMetricLabel) {
                if (isBoundaryForCaptures(currentStripped)) {
                    this.expectMetricLabel = null;
                    reprocess = true;
                    continue;
                }
                const val = this.parseMetricValue(currentTrimmed);
                if (val) {
                    this.flushThinking();
                    this.flushText();
                    this.push({ id: `metric-${this.events.length}`, kind: 'metric', label: this.expectMetricLabel, value: val, lifecycle: 'closed' });
                    this.expectMetricLabel = null;
                    return;
                }
                this.expectMetricLabel = null;
                reprocess = true;
                continue;
            }
            if (currentTrimmed === '') {
                this.flushThinking();
                this.ensureText();
                this.text.buffer.push('');
                this.events[this.text.idx] = { ...this.events[this.text.idx], text: this.text.buffer.join('\n') };
                return;
            }
            if (isRoleLine(currentTrimmed)) {
                this.flushThinking();
                this.flushText();
                const role = currentTrimmed.toLowerCase();
                this.push({ id: `role-${this.events.length}`, kind: 'role', role, lifecycle: 'closed' });
                this.mode = role;
                return;
            }
            const tokensUsed = currentTrimmed.match(/^(tokens?\s+used)\s*[:：]?\s*(.*)$/i);
            if (tokensUsed) {
                const rest = (tokensUsed[2] || '').trim();
                if (!rest) {
                    this.expectMetricLabel = 'tokens used';
                    return;
                }
                const val = this.parseMetricValue(rest);
                if (val) {
                    this.flushThinking();
                    this.flushText();
                    this.push({ id: `metric-${this.events.length}`, kind: 'metric', label: 'tokens used', value: val, lifecycle: 'closed' });
                    return;
                }
            }
            if (this.mode === 'thinking' && this.isThinkingTitleLine(currentTrimmed)) {
                this.flushText();
                const title = currentTrimmed.replace(/^\*\*(.+?)\*\*\s*$/, '$1').trim();
                const id = `thinking-${this.events.length}`;
                const idx = this.push({ id, kind: 'thinking', title, body: '', lifecycle: 'open' });
                this.thinking = { id, idx, title, buffer: [] };
                return;
            }
            const ps = currentTrimmed.match(/^"([^"]*powershell\.exe)"\s+-Command\s+(.*)$/i);
            if (ps) {
                this.flushThinking();
                this.flushText();
                const shell = ps[1];
                let payload = ps[2].trim();
                const q = payload.match(/^'(.*)'$/) || payload.match(/^"(.*)"$/);
                if (q)
                    payload = q[1];
                this.lastCmd = { shell, payload };
                this.push({ id: `cmd-${this.events.length}`, kind: 'command', shell, cmd: payload, lifecycle: 'closed' });
                return;
            }
            const execMatch = currentTrimmed.match(/^\[(?:cmd|CMD)\]\s+Running:\s+(.+)$/);
            if (execMatch) {
                this.flushThinking();
                this.flushText();
                this.push({ id: `exec-${this.events.length}`, kind: 'exec', cmd: execMatch[1], lifecycle: 'closed' });
                return;
            }
            const ok = currentTrimmed.match(/\bin\s+(.+?)\s+succeeded\s+in\s+(\d+)ms:?\s*$/);
            const fail = currentTrimmed.match(/\bexited\s+(-?\d+)\s+in\s+(\d+)ms:?\s*$/);
            if (ok) {
                this.flushThinking();
                this.flushText();
                const cwd = ok[1].trim();
                const ms = Number(ok[2]);
                this.push({ id: `res-${this.events.length}`, kind: 'commandResult', status: 'ok', ms, cwd, exitCode: 0, lifecycle: 'closed' });
                const payload = this.lastCmd?.payload || '';
                if (/Get-ChildItem\b/i.test(payload))
                    this.expectOutput = { kind: 'getChildItem' };
                else if (/Get-Content\b/i.test(payload)) {
                    const pathMatch = payload.match(/-Path\s+([^\s'"]+)/i) ||
                        payload.match(/-Path\s+'([^']+)'/i) ||
                        payload.match(/-Path\s+"([^"]+)"/i);
                    this.expectOutput = { kind: 'getContent', pathHint: pathMatch ? pathMatch[1] : undefined };
                }
                else
                    this.expectOutput = null;
                return;
            }
            if (fail) {
                this.flushThinking();
                this.flushText();
                const code = Number(fail[1]);
                const ms = Number(fail[2]);
                this.push({ id: `res-${this.events.length}`, kind: 'commandResult', status: 'fail', ms, exitCode: code, lifecycle: 'closed' });
                this.expectOutput = null;
                return;
            }
            if (/^mcp:/i.test(currentTrimmed)) {
                this.flushThinking();
                this.flushText();
                const msg = currentTrimmed.replace(/^mcp:\s*/i, '');
                let phase = 'info';
                if (msg.toLowerCase().includes('starting'))
                    phase = 'starting';
                if (msg.toLowerCase().includes('ready'))
                    phase = 'ready';
                this.push({ id: `tool-${this.events.length}`, kind: 'tool', tool: 'mcp', phase, message: msg, lifecycle: 'closed' });
                return;
            }
            const mcpStartup = currentTrimmed.match(/^mcp startup:\s*ready:\s*(.+)\s*$/i);
            if (mcpStartup) {
                this.flushThinking();
                this.flushText();
                const list = mcpStartup[1].split(',').map((s) => s.trim()).filter(Boolean);
                list.forEach((tool) => {
                    this.push({ id: `tool-${this.events.length}`, kind: 'tool', tool, phase: 'ready', lifecycle: 'closed' });
                });
                return;
            }
            if (this.expectOutput?.kind === 'getChildItem') {
                const dir = currentTrimmed.match(/^Directory:\s+(.+)$/);
                if (dir) {
                    this.flushThinking();
                    this.flushText();
                    const id = `table-${this.events.length}`;
                    const idx = this.push({ id, kind: 'table', title: dir[1], columns: [], rows: [], lifecycle: 'open' });
                    this.table = { id, idx, title: dir[1], stage: 'awaitHeader', columns: [], rows: [] };
                    return;
                }
            }
            if (this.expectOutput?.kind === 'getContent') {
                if (!this.file) {
                    this.flushThinking();
                    this.flushText();
                    const id = `file-${this.events.length}`;
                    const idx = this.push({
                        id,
                        kind: 'fileContent',
                        pathHint: this.expectOutput.pathHint,
                        content: '',
                        lifecycle: 'open',
                    });
                    this.file = { id, idx, pathHint: this.expectOutput.pathHint, content: '' };
                }
                reprocess = true;
                continue;
            }
            if (LLM_SEPARATOR_RE.test(currentTrimmed)) {
                return;
            }
            const llmThinkingHeader = currentTrimmed.match(/^\[Thinking\/Reasoning\]\s*$/);
            if (llmThinkingHeader) {
                this.flushText();
                this.flushThinking();
                const id = `thinking-${this.events.length}`;
                const idx = this.push({ id, kind: 'thinking', title: 'LLM Thinking', body: '', lifecycle: 'open' });
                this.thinking = { id, idx, title: 'LLM Thinking', buffer: [] };
                return;
            }
            const llmSection = currentTrimmed.match(/^\[(LLM Response|LLM|MiniMax Debug|Output Preview|Token Usage|MiniMax Streaming|MiniMax)\b[^\]]*\]\s*(.*)$/);
            if (llmSection) {
                this.flushThinking();
                this.flushText();
                const title = llmSection[1];
                const rest = (llmSection[2] || '').trim();
                const id = `section-${this.events.length}`;
                const idx = this.push({ id, kind: 'section', title, body: rest, lifecycle: 'open' });
                if (title === 'Token Usage') {
                    const tokenMatch = rest.match(/(\d[\d,]*)\s*tokens?/i);
                    if (tokenMatch) {
                        this.closeByIdx(idx);
                        this.push({ id: `metric-${this.events.length}`, kind: 'metric', label: 'tokens used', value: tokenMatch[1], lifecycle: 'closed' });
                        return;
                    }
                }
                this.thinking = { id, idx, title, buffer: rest ? [rest] : [] };
                return;
            }
            if (this.isJsonStartLine(currentStripped)) {
                this.flushThinking();
                this.flushText();
                const id = `json-${this.events.length}`;
                const idx = this.push({ id, kind: 'json', value: undefined, raw: '', lifecycle: 'open' });
                this.json = { id, idx, raw: currentStripped, depth: 0, inString: false, escape: false, started: false, lines: 1 };
                this.scanJsonFragment(currentStripped, this.json);
                this.events[idx] = { ...this.events[idx], raw: this.json.raw };
                if (this.json.started && this.json.depth <= 0 && !this.json.inString) {
                    try {
                        const val = JSON.parse(this.json.raw);
                        this.events[idx] = { ...this.events[idx], value: val, raw: this.json.raw, lifecycle: 'closed' };
                    }
                    catch {
                        this.events[idx] = { ...this.events[idx], raw: this.json.raw, lifecycle: 'closed' };
                    }
                    this.json = null;
                }
                return;
            }
            this.ensureText();
            this.text.buffer.push(currentStripped);
            this.events[this.text.idx] = { ...this.events[this.text.idx], text: this.text.buffer.join('\n') };
            return;
        }
    }
    flushOpenBlocks() {
        if (this.table) {
            this.closeByIdx(this.table.idx);
            this.table = null;
        }
        if (this.file) {
            this.closeByIdx(this.file.idx);
            this.file = null;
        }
        if (this.json) {
            this.closeByIdx(this.json.idx);
            this.json = null;
        }
        if (this.thinking) {
            this.closeByIdx(this.thinking.idx);
            this.thinking = null;
        }
        this.flushText();
        this.expectOutput = null;
    }
}
export function parseCodexCliLines(lines) {
    const parser = new CodexCliStreamParser();
    lines.forEach((line) => parser.feedLine(line));
    return parser.events;
}
