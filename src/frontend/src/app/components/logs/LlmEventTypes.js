// ---------------------------------------------------------------------------
// Canonical LlmEvent types — matches backend emit_llm_event() schema v1
// ---------------------------------------------------------------------------
let _parseSeq = 0;
function normalizeSource(value) {
    const token = String(value || '').trim().toLowerCase();
    if (token === 'api' || token === 'cli' || token === 'system') {
        return token;
    }
    return 'system';
}
function toRecord(value) {
    return value && typeof value === 'object' && !Array.isArray(value)
        ? value
        : null;
}
export function parseLlmEventLine(line) {
    if (!line || !line.trim())
        return null;
    try {
        const raw = JSON.parse(line);
        if (!raw || typeof raw !== 'object')
            return null;
        if (raw.event) {
            if (!raw.event_id)
                raw.event_id = `fe-${++_parseSeq}`;
            return raw;
        }
        const rawObj = toRecord(raw.raw);
        const streamEvent = String(rawObj?.stream_event || rawObj?.event || raw.kind || '').trim();
        if (!streamEvent)
            return null;
        const refs = toRecord(raw.refs);
        const role = String(raw.actor || raw.role || rawObj?.role || 'assistant').trim() || 'assistant';
        const fallbackData = {
            message: String(rawObj?.content || raw.message || '').trim(),
            tool: String(rawObj?.tool || '').trim(),
            args: toRecord(rawObj?.args) || {},
            success: rawObj?.success,
            result: toRecord(rawObj?.result) || rawObj?.result || {},
            error: String(rawObj?.error || '').trim(),
            kind: String(raw.kind || '').trim(),
            channel: String(raw.channel || '').trim(),
        };
        const converted = {
            schema_version: Number(raw.schema_version || 2),
            event_id: String(raw.event_id || `fe-${++_parseSeq}`),
            run_id: String(raw.run_id || ''),
            iteration: Number(refs?.iteration || 0),
            role,
            ts: String(raw.ts || new Date().toISOString()),
            seq: Number(raw.seq || 0),
            source: normalizeSource(raw.source),
            event: streamEvent,
            data: fallbackData,
        };
        return converted;
    }
    catch {
        return null;
    }
}
export function parseLlmEventLines(lines) {
    const events = [];
    for (const line of lines) {
        const ev = parseLlmEventLine(line);
        if (ev)
            events.push(ev);
    }
    return events;
}
