function isAbsoluteWorkspacePath(value) {
    return value.startsWith('/') || value.startsWith('\\\\') || /^[A-Za-z]:[\\/]/.test(value);
}
export function resolveBenchObservedWorkspace(value, baseWorkspace) {
    const normalized = String(value || '').trim();
    if (!normalized || isAbsoluteWorkspacePath(normalized))
        return normalized;
    const base = String(baseWorkspace || '').trim();
    if (!base || !isAbsoluteWorkspacePath(base))
        return normalized;
    return `${base.replace(/[\\/]+$/, '')}/${normalized.replace(/^\.?[\\/]+/, '')}`;
}
export function applyBenchObservedWorkspaceChange({ nextWorkspace, settingsWorkspace, currentWorkspace, setProgressSnapshot, setBenchObservedWorkspace, }) {
    const normalized = resolveBenchObservedWorkspace(nextWorkspace, settingsWorkspace);
    if (!normalized || normalized === currentWorkspace)
        return '';
    setProgressSnapshot(null);
    setBenchObservedWorkspace(normalized);
    return normalized;
}
