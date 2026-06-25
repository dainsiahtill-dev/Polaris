function isAbsoluteWorkspacePath(value: string): boolean {
  return value.startsWith('/') || value.startsWith('\\\\') || /^[A-Za-z]:[\\/]/.test(value);
}

export function resolveBenchObservedWorkspace(value: string, baseWorkspace: string): string {
  const normalized = String(value || '').trim();
  if (!normalized || isAbsoluteWorkspacePath(normalized)) return normalized;
  const base = String(baseWorkspace || '').trim();
  if (!base || !isAbsoluteWorkspacePath(base)) return normalized;
  return `${base.replace(/[\\/]+$/, '')}/${normalized.replace(/^\.?[\\/]+/, '')}`;
}

interface ApplyBenchObservedWorkspaceChangeOptions<TSnapshot> {
  nextWorkspace: string;
  settingsWorkspace: string;
  currentWorkspace: string;
  setProgressSnapshot: (snapshot: TSnapshot | null) => void;
  setBenchObservedWorkspace: (workspace: string) => void;
}

export function applyBenchObservedWorkspaceChange<TSnapshot>({
  nextWorkspace,
  settingsWorkspace,
  currentWorkspace,
  setProgressSnapshot,
  setBenchObservedWorkspace,
}: ApplyBenchObservedWorkspaceChangeOptions<TSnapshot>): string {
  const normalized = resolveBenchObservedWorkspace(nextWorkspace, settingsWorkspace);
  if (!normalized || normalized === currentWorkspace) return '';
  setProgressSnapshot(null);
  setBenchObservedWorkspace(normalized);
  return normalized;
}
