import type { ContextOSEvent } from './contextOSTelemetry';

const HEARTBEAT_TYPES = new Set([
  'heartbeat', 'stage_heartbeat', 'heartbeat_renewed', 'task_heartbeat',
  'worker_heartbeat', 'execution_heartbeat', 'lease_heartbeat_renewed',
]);
const FAILURE_SIGNAL = /failed|failure|error|timeout|expired|lost|denied|blocked|异常|失败|超时|失效|丢失|拒绝|阻塞/i;

/** User timeline selection only. Raw telemetry and lease health remain intact. */
export function isPureContextOSHeartbeat(event: ContextOSEvent): boolean {
  if (event.category === 'error' || event.error || event.errorCode) return false;
  if (['warning', 'warn', 'error', 'fatal', 'critical'].includes(event.sourceSeverity ?? '')) return false;
  if (event.isCall || event.isProjection || ['call', 'tool', 'projection'].includes(event.category)) return false;
  if (FAILURE_SIGNAL.test(event.summary)) return false;

  const type = (event.sourceEventType || event.name).trim().toLowerCase();
  const terminalType = type.split(/[.:]/).pop() || type;
  if (HEARTBEAT_TYPES.has(terminalType)) return true;

  // Older Factory log bridges replaced the structured type with a display title.
  // Match only exact routine notices from a runtime producer, never LLM prose.
  const actor = event.actor.toLowerCase().replace(/[\s_-]/g, '');
  if (!['factory', 'director', 'taskruntime', 'runtime'].includes(actor)) return false;
  return /^Stage [a-z0-9_.-]+ is still running$/i.test(event.summary)
    || /^Director task \d+ heartbeat_renewed$/i.test(event.summary);
}

export function meaningfulContextOSEvents(events: readonly ContextOSEvent[]): ContextOSEvent[] {
  return events.filter((event) => !isPureContextOSHeartbeat(event));
}
