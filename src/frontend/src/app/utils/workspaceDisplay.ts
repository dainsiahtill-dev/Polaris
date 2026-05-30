export function workspaceName(workspace?: string | null): string {
  const value = String(workspace || '').trim();
  if (!value) return '';

  const normalized = value.replace(/[\\/]+$/, '');
  const parts = normalized.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || normalized || value;
}

export function workspaceLabel(workspace?: string | null, fallback = '未选择 Workspace'): string {
  return workspaceName(workspace) || fallback;
}

export function workspaceFileLabel(workspace?: string | null, fileName = 'AGENTS.md'): string {
  const name = workspaceName(workspace);
  return name ? `${name}\\${fileName}` : `workspace\\${fileName}`;
}
