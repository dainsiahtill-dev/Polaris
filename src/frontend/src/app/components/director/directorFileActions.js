export function resolveDirectorOpenTarget(workspace, filePath) {
    const target = String(filePath ?? '').trim();
    if (!target) {
        return null;
    }
    if (/^[a-zA-Z]:[\\/]/.test(target) || target.startsWith('\\\\') || target.startsWith('/')) {
        return target;
    }
    const workspaceRoot = String(workspace || '').trim().replace(/[\\/]+$/, '');
    if (!workspaceRoot) {
        return null;
    }
    const segments = target.split(/[\\/]+/).filter(Boolean);
    if (segments.some((segment) => segment === '..')) {
        return null;
    }
    return `${workspaceRoot}\\${segments.join('\\')}`;
}
