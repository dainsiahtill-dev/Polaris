export function shouldEnableGlobalBenchObserver(internalBenchEnabled, initialWorkspaceBinding) {
    return internalBenchEnabled && !String(initialWorkspaceBinding || '').trim();
}
