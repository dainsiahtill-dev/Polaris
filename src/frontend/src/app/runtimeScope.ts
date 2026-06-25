export function shouldEnableGlobalBenchObserver(
  internalBenchEnabled: boolean,
  initialWorkspaceBinding: string,
): boolean {
  return internalBenchEnabled && !String(initialWorkspaceBinding || '').trim();
}
