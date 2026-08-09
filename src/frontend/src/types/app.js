export function resolveRunning(status) {
    if (!status)
        return false;
    if (status.running)
        return true;
    const nested = status.status;
    if (!nested || typeof nested !== 'object')
        return false;
    const raw = nested.running;
    if (typeof raw === 'boolean')
        return raw;
    if (typeof raw === 'number')
        return raw !== 0;
    if (typeof raw === 'string') {
        return ['1', 'true', 'yes', 'on', 'running'].includes(raw.trim().toLowerCase());
    }
    return false;
}
