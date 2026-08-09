export function workspaceName(workspace) {
    const value = String(workspace || '').trim();
    if (!value)
        return '';
    const normalized = value.replace(/[\\/]+$/, '');
    const parts = normalized.split(/[\\/]/).filter(Boolean);
    return parts.at(-1) || normalized || value;
}
export function workspaceLabel(workspace, fallback = '未选择 Workspace') {
    return workspaceName(workspace) || fallback;
}
export function workspaceFileLabel(workspace, fileName = 'AGENTS.md') {
    const name = workspaceName(workspace);
    return name ? `${name}\\${fileName}` : `workspace\\${fileName}`;
}
