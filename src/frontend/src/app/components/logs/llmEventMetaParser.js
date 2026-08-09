function extractProviderType(providerId) {
    const trimmed = (providerId || '').trim();
    if (!trimmed)
        return '';
    const withoutSuffix = trimmed.replace(/-\d{8,}$/g, '');
    const split = withoutSuffix.split(/[-_:]/).filter(Boolean);
    return split[0] || withoutSuffix;
}
export function parseLlmConfigMessage(message) {
    const fields = {};
    const raw = (message || '').trim();
    if (raw) {
        for (const chunk of raw.split(',')) {
            const piece = chunk.trim();
            if (!piece)
                continue;
            const eq = piece.indexOf('=');
            if (eq <= 0)
                continue;
            const key = piece.slice(0, eq).trim().toLowerCase();
            const value = piece.slice(eq + 1).trim();
            if (key && value)
                fields[key] = value;
        }
    }
    const provider = fields.provider || '';
    const model = fields.model || '';
    const backend = fields.backend || '';
    const providerType = extractProviderType(provider);
    const backendType = backend ? backend.split(':')[0].trim().toLowerCase() : '';
    const modelType = backendType || providerType;
    return {
        fields,
        provider,
        model,
        backend,
        providerType,
        modelType,
    };
}
