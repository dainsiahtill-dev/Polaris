import { apiFetch } from '@/api';
function formatDetailValue(value, fallback) {
    if (typeof value === 'string') {
        const text = value.trim();
        return text || fallback;
    }
    if (Array.isArray(value)) {
        const items = value
            .map((item) => formatDetailValue(item, ''))
            .map((item) => item.trim())
            .filter((item) => item.length > 0);
        return items.length > 0 ? items.join('\n') : fallback;
    }
    if (value && typeof value === 'object') {
        const payload = value;
        const error = String(payload.error || '').trim();
        const requiredRoles = Array.isArray(payload.required_roles)
            ? payload.required_roles.map((role) => String(role || '').trim()).filter((role) => role.length > 0)
            : [];
        const missingRoles = Array.isArray(payload.missing_roles)
            ? payload.missing_roles.map((role) => String(role || '').trim()).filter((role) => role.length > 0)
            : [];
        if (error || requiredRoles.length > 0 || missingRoles.length > 0) {
            const lines = [];
            lines.push(error || 'runtime roles not ready');
            if (requiredRoles.length > 0) {
                lines.push(`required_roles: ${requiredRoles.join(', ')}`);
            }
            if (missingRoles.length > 0) {
                lines.push(`missing_roles: ${missingRoles.join(', ')}`);
            }
            return lines.join('\n');
        }
        try {
            return JSON.stringify(value, null, 2);
        }
        catch {
            return fallback;
        }
    }
    if (value == null)
        return fallback;
    return String(value);
}
export async function extractErrorDetail(res, fallback) {
    try {
        const payload = (await res.json());
        return formatDetailValue(payload.detail, fallback);
    }
    catch {
        return fallback;
    }
}
export async function fetchLogTail(logPath, maxLines = 20) {
    try {
        const res = await apiFetch(`/v2/files/read?path=${encodeURIComponent(logPath)}&tail_lines=200`);
        if (!res.ok)
            return '';
        const payload = (await res.json());
        if (!payload.content)
            return '';
        const lines = payload.content.split('\n');
        return lines.slice(-maxLines).join('\n');
    }
    catch {
        return '';
    }
}
export async function getLogTailWithError(status, defaultLogPath, errorDetail, maxLogLines = 20) {
    if (!status)
        return errorDetail;
    const logPath = status.log_path || defaultLogPath;
    const tail = await fetchLogTail(logPath, maxLogLines);
    if (tail) {
        return `${errorDetail}\n\n${tail}`;
    }
    return errorDetail;
}
export async function fetchProcessLogOnError(endpoint, defaultLogPath, errorMessage) {
    const res = await apiFetch(endpoint, { method: 'POST' });
    if (res.ok) {
        return { detail: errorMessage };
    }
    const detail = await extractErrorDetail(res, errorMessage);
    const logTail = await fetchLogTail(defaultLogPath);
    return {
        detail: logTail ? `${detail}\n\n${logTail}` : detail,
        logTail,
    };
}
