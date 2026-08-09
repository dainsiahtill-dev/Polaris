const MIN_REASONABLE_EPOCH_SECONDS = 946684800; // 2000-01-01T00:00:00Z
export function isStructuredRuntimeFragmentText(value) {
    const message = String(value || '').trim();
    if (!message)
        return true;
    if (/\[object(?:\s+object)?\]/i.test(message))
        return true;
    if (/^[{}\[\],]+$/.test(message))
        return true;
    if (/^["']?[}\]],?$/.test(message))
        return true;
    if (/^:\d{2}(?:\.\d+)?z["']?,?$/i.test(message))
        return true;
    return /^["']?[a-z0-9_.-]+["']?\s*:\s*(?:$|["'{\[\]\d]|true\b|false\b|null\b)/i.test(message);
}
export function cleanRuntimeDisplayText(value) {
    const text = String(value || '').trim();
    if (!text || isStructuredRuntimeFragmentText(text)) {
        return null;
    }
    return text.replace(/\s+/g, ' ');
}
export function normalizeStartedAtSeconds(value) {
    if (value === null || value === undefined || value === '') {
        return null;
    }
    if (typeof value === 'number') {
        if (!Number.isFinite(value) || value <= 0) {
            return null;
        }
        const seconds = value > 1000000000000 ? value / 1000 : value;
        return seconds >= MIN_REASONABLE_EPOCH_SECONDS ? seconds : null;
    }
    if (typeof value !== 'string') {
        return null;
    }
    const trimmed = value.trim();
    if (!trimmed) {
        return null;
    }
    const numeric = Number(trimmed);
    if (Number.isFinite(numeric)) {
        return normalizeStartedAtSeconds(numeric);
    }
    const parsed = Date.parse(trimmed);
    if (!Number.isFinite(parsed)) {
        return null;
    }
    return normalizeStartedAtSeconds(parsed);
}
