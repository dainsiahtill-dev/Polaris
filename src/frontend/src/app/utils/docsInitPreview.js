function asRecord(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return null;
    }
    return value;
}
function asString(value) {
    return typeof value === 'string' ? value : '';
}
function normalizeFile(value) {
    const record = asRecord(value);
    if (!record) {
        return null;
    }
    const path = asString(record.path).trim();
    if (!path) {
        return null;
    }
    return {
        path,
        content: typeof record.content === 'string' ? record.content : String(record.content ?? ''),
        exists: typeof record.exists === 'boolean' ? record.exists : undefined,
    };
}
export function normalizeDocsInitPreviewPayload(value) {
    const record = asRecord(value);
    if (!record) {
        return null;
    }
    const targetRoot = asString(record.target_root).trim();
    if (!targetRoot) {
        return null;
    }
    const rawFiles = Array.isArray(record.files) ? record.files : [];
    const files = rawFiles
        .map((item) => normalizeFile(item))
        .filter((item) => item !== null);
    if (files.length === 0) {
        return null;
    }
    const project = asRecord(record.project) ?? undefined;
    return {
        mode: asString(record.mode).trim() || 'minimal',
        target_root: targetRoot,
        docs_exists: Boolean(record.docs_exists),
        files,
        project,
    };
}
