function safeJsonParse(text) {
    if (!text)
        return null;
    try {
        return JSON.parse(text);
    }
    catch {
        return null;
    }
}
export function unwrapJsonFence(text) {
    const trimmed = (text || '').trim();
    const matched = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
    return matched ? matched[1].trim() : trimmed;
}
export function extractFirstJsonValue(text) {
    const firstObject = text.indexOf('{');
    const firstArray = text.indexOf('[');
    let start = -1;
    if (firstObject >= 0 && firstArray >= 0)
        start = Math.min(firstObject, firstArray);
    else
        start = Math.max(firstObject, firstArray);
    if (start < 0)
        return { value: null, trailing: text.trim() };
    const opener = text[start];
    const closer = opener === '{' ? '}' : ']';
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let i = start; i < text.length; i += 1) {
        const ch = text[i];
        if (inString) {
            if (escaped) {
                escaped = false;
                continue;
            }
            if (ch === '\\') {
                escaped = true;
                continue;
            }
            if (ch === '"')
                inString = false;
            continue;
        }
        if (ch === '"') {
            inString = true;
            continue;
        }
        if (ch === opener) {
            depth += 1;
            continue;
        }
        if (ch === closer) {
            depth -= 1;
            if (depth === 0) {
                return {
                    value: text.slice(start, i + 1),
                    trailing: text.slice(i + 1).trim(),
                };
            }
        }
    }
    return { value: null, trailing: text.trim() };
}
export function parseJsonLikeOutputWithMeta(raw) {
    const unfenced = unwrapJsonFence(raw || '');
    if (!unfenced)
        return { value: null, note: '' };
    const direct = safeJsonParse(unfenced);
    if (direct !== null)
        return { value: direct, note: '' };
    const first = extractFirstJsonValue(unfenced);
    if (!first.value)
        return { value: null, note: 'json_parse_failed' };
    const parsed = safeJsonParse(first.value);
    if (parsed === null)
        return { value: null, note: 'json_parse_failed' };
    if (first.trailing) {
        return { value: parsed, note: `normalized_first_json_value; trailing_chars=${first.trailing.length}` };
    }
    return { value: parsed, note: 'normalized_first_json_value' };
}
export function parseJsonLikeOutput(raw) {
    return parseJsonLikeOutputWithMeta(raw).value;
}
