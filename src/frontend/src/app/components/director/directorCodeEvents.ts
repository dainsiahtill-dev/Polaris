import type { FileEditEvent } from '@/app/hooks/useRuntimeStore';

export function hasRenderablePatch(event: FileEditEvent): boolean {
  return typeof event.patch === 'string' && event.patch.trim().length > 0;
}

export function fileEditEventTime(event: FileEditEvent): number {
  const parsed = Date.parse(event.timestamp);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function compareFileEditEventsForCodePanel(a: FileEditEvent, b: FileEditEvent): number {
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

export function selectDefaultCodePanelEvent(events: FileEditEvent[]): FileEditEvent | null {
  return events.find(hasRenderablePatch) ?? events[0] ?? null;
}
