const SOURCE = 'Factory Bench';
function eventTimestamp(event) {
    const raw = typeof event.ts === 'string' ? event.ts.trim() : '';
    if (raw)
        return raw;
    return '1970-01-01T00:00:00.000Z';
}
function toEpoch(timestamp) {
    const value = Date.parse(timestamp);
    return Number.isFinite(value) ? value : 0;
}
function eventLevel(event) {
    const type = String(event.type || '').toLowerCase();
    if (event.ok === false || type.includes('failed') || type.includes('error'))
        return 'error';
    if (type.includes('cancelled') || type.includes('canceled') || type.includes('warning'))
        return 'warning';
    if (event.ok === true || type.includes('completed') || type.includes('passed'))
        return 'success';
    return 'info';
}
function eventTitle(event) {
    const name = typeof event.name === 'string' ? event.name.trim() : '';
    if (name)
        return name;
    return String(event.type || 'factory_bench.event');
}
function eventMessage(event) {
    const summary = typeof event.summary === 'string' ? event.summary.trim() : '';
    if (summary)
        return summary;
    return eventTitle(event);
}
function tagFromMetaValue(prefix, value) {
    if (value === undefined || value === null || value === '')
        return null;
    return `${prefix}${String(value)}`;
}
export function benchEventToProcessLog(event) {
    const timestamp = eventTimestamp(event);
    const sessionId = String(event.session_id || 'session');
    const seq = event.seq !== undefined ? String(event.seq) : String(timestamp);
    const meta = event.meta && typeof event.meta === 'object' ? event.meta : {};
    const tags = [
        'bench',
        tagFromMetaValue('', meta.project_id),
        tagFromMetaValue('L', meta.level),
    ].filter((tag) => Boolean(tag));
    return {
        id: `bench-${sessionId}-${seq}`,
        timestamp,
        level: eventLevel(event),
        source: SOURCE,
        title: eventTitle(event),
        message: eventMessage(event),
        details: JSON.stringify({
            type: event.type,
            name: event.name ?? null,
            actor: event.actor ?? null,
            session_id: event.session_id ?? null,
            meta,
        }),
        meta: {
            ...meta,
            bench_session_id: event.session_id ?? null,
            bench_event_type: event.type,
            bench_seq: event.seq ?? null,
        },
        tags,
    };
}
export function mergeProcessAndBenchLogs(processLogs, benchEvents, max = 240) {
    const merged = new Map();
    let index = 0;
    for (const log of processLogs) {
        merged.set(log.id, { log, index });
        index += 1;
    }
    for (const event of benchEvents) {
        const log = benchEventToProcessLog(event);
        if (!merged.has(log.id)) {
            merged.set(log.id, { log, index });
            index += 1;
        }
    }
    return Array.from(merged.values())
        .sort((left, right) => {
        const delta = toEpoch(left.log.timestamp) - toEpoch(right.log.timestamp);
        return delta !== 0 ? delta : left.index - right.index;
    })
        .slice(-max)
        .map((entry) => entry.log);
}
