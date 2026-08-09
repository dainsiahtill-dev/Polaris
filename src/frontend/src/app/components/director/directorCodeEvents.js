export function hasRenderablePatch(event) {
    return typeof event.patch === 'string' && event.patch.trim().length > 0;
}
export function fileEditEventTime(event) {
    const parsed = Date.parse(event.timestamp);
    return Number.isFinite(parsed) ? parsed : 0;
}
export function compareFileEditEventsForCodePanel(a, b) {
    const patchDelta = Number(hasRenderablePatch(b)) - Number(hasRenderablePatch(a));
    if (patchDelta !== 0) {
        return patchDelta;
    }
    const timeDelta = fileEditEventTime(b) - fileEditEventTime(a);
    if (timeDelta !== 0) {
        return timeDelta;
    }
    return String(b.id).localeCompare(String(a.id));
}
export function selectDefaultCodePanelEvent(events) {
    return events.find(hasRenderablePatch) ?? events[0] ?? null;
}
