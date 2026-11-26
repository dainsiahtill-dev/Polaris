import type { EngineeringEvent } from '@/services/api.types';

/** The key of a persisted provider request in the current workspace. */
export function isContextSnapshotRef(value: unknown): value is string {
  return typeof value === 'string' && value.length === 24 && /^[a-f0-9]{24}$/.test(value);
}

/** Legacy event streams can pad or capitalize keys. The store uses lowercase. */
export function normalizeContextSnapshotRef(value: unknown): string {
  const key = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return isContextSnapshotRef(key) ? key : '';
}

export function engineeringSnapshotRef(payload: Record<string, unknown>): string | null {
  const key = payload.context_snapshot_ref;
  return payload.snapshot_status === 'available' && isContextSnapshotRef(key) ? key : null;
}

export function discussionSnapshotRef(metadata: Record<string, unknown> | null | undefined): string | null {
  const observation = metadata?.native_request_observation;
  if (!observation || typeof observation !== 'object' || !('snapshot_status' in observation)) return null;
  return engineeringSnapshotRef({
    context_snapshot_ref: metadata?.context_snapshot_ref,
    snapshot_status: observation.snapshot_status,
  });
}

/** Engineering-native capture observations; never inferred physical HTTP counts. */
export function engineeringContextRecords(events: readonly EngineeringEvent[]) {
  const captures = new Map<string, EngineeringEvent>();
  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    if (event.kind !== 'llm_request_observed') continue;
    const id = typeof event.payload.capture_id === 'string' ? event.payload.capture_id : `event:${event.seq}`;
    captures.set(id, event);
  }
  return [...captures.values()].sort((a, b) => b.seq - a.seq).map((event) => {
    const payload = event.payload;
    const audit = payload.audit && typeof payload.audit === 'object' && !Array.isArray(payload.audit) ? payload.audit as Record<string, unknown> : {};
    const usage = payload.usage && typeof payload.usage === 'object' && !Array.isArray(payload.usage) ? payload.usage as Record<string, unknown> : {};
    const contextSnapshotRef = engineeringSnapshotRef(payload);
    return {
      event, audit, usage, contextSnapshotRef,
      status: typeof payload.status === 'string' ? payload.status : 'unknown',
      usageStatus: ['reported', 'estimated', 'unknown'].includes(String(payload.usage_status)) ? String(payload.usage_status) : 'unknown',
      finalRequestTokens: typeof audit.final_request_token_estimate === 'number' ? audit.final_request_token_estimate : null,
      usageTokens: typeof usage.total_tokens === 'number' ? usage.total_tokens : null,
    };
  });
}
