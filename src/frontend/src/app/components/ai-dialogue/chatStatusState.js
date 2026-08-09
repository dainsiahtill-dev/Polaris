export function resolveDialogueStatusKind(status, statusLoading) {
    if (statusLoading) {
        return 'loading';
    }
    if (status?.ready) {
        return 'ready';
    }
    if (status?.configured === false) {
        return 'unconfigured';
    }
    return 'error';
}
