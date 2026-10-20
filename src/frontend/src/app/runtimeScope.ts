export function shouldEnableGlobalBenchObserver(
  internalBenchEnabled: boolean,
  initialWorkspaceBinding: string,
  explicitGlobalObserverRequested = false,
  initialInstanceBinding = '',
): boolean {
  return internalBenchEnabled
    && explicitGlobalObserverRequested
    && !String(initialWorkspaceBinding || '').trim()
    && !String(initialInstanceBinding || '').trim();
}
