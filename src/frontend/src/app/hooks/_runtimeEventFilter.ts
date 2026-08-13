/**
 * Runtime event-filtering + workspace-matching helpers.
 *
 * Extracted losslessly from useRuntime.ts. Pure functions that classify
 * runtime event channels (factory/bench) and match raw events/lines to the
 * active workspace by scanning known workspace-path field names. No React or
 * hook state — safe to unit-test in isolation.
 */

import * as Parsing from './runtimeParsing';
import type { WebSocketMessage } from './useRuntime';

export const DEFAULT_RUNTIME_ROLES: Array<'pm' | 'chief_engineer' | 'director' | 'qa'> = [
  'pm',
  'chief_engineer',
  'director',
  'qa',
];

const FACTORY_EVENT_CHANNEL = 'event.factory';
const BENCH_EVENT_CHANNEL = 'event.bench';

export function isInternalBenchEventChannel(channel: string): boolean {
  return channel === BENCH_EVENT_CHANNEL || channel.startsWith(`${BENCH_EVENT_CHANNEL}:`);
}

export function isRuntimeFactoryOrBenchEventChannel(channel: string): boolean {
  return (
    channel === FACTORY_EVENT_CHANNEL ||
    channel.startsWith(`${FACTORY_EVENT_CHANNEL}:`) ||
    isInternalBenchEventChannel(channel)
  );
}

const RUNTIME_WORKSPACE_FIELD_NAMES = new Set([
  'workspace',
  'workspace_path',
  'workspacePath',
  'project_workspace',
  'projectWorkspace',
  'project_root',
  'projectRoot',
  'polaris_workspace',
  'polarisWorkspace',
  'runtime_workspace',
  'runtimeWorkspace',
]);

export function normalizeRuntimeWorkspacePath(value: unknown): string {
  const token = Parsing.toStringValue(value).trim();
  if (!token) return '';
  return token.replace(/\\/g, '/').replace(/\/+$/g, '');
}

export function collectRuntimeWorkspacePaths(
  value: unknown,
  paths: Set<string>,
  seen: WeakSet<object>,
  depth = 0,
): void {
  if (depth > 5 || !Parsing.isRecord(value)) return;
  if (seen.has(value)) return;
  seen.add(value);

  for (const [key, nested] of Object.entries(value)) {
    if (RUNTIME_WORKSPACE_FIELD_NAMES.has(key)) {
      const normalized = normalizeRuntimeWorkspacePath(nested);
      if (normalized) paths.add(normalized);
    }

    if (Parsing.isRecord(nested)) {
      collectRuntimeWorkspacePaths(nested, paths, seen, depth + 1);
    } else if (Array.isArray(nested)) {
      for (const item of nested.slice(0, 12)) {
        collectRuntimeWorkspacePaths(item, paths, seen, depth + 1);
      }
    }
  }
}

export function runtimeRecordMatchesWorkspace(raw: unknown, activeWorkspace: string): boolean {
  const active = normalizeRuntimeWorkspacePath(activeWorkspace);
  if (!active || !Parsing.isRecord(raw)) return true;

  const candidates = new Set<string>();
  collectRuntimeWorkspacePaths(raw, candidates, new WeakSet<object>());
  if (candidates.size === 0) return true;
  return candidates.has(active);
}

export function runtimeLineMatchesWorkspace(line: string, activeWorkspace: string): boolean {
  const parsed = Parsing.tryParseJsonObject(line);
  return parsed ? runtimeRecordMatchesWorkspace(parsed, activeWorkspace) : true;
}

export function isSettingsChangedEvent(raw: Record<string, unknown>): boolean {
  const eventName = String(raw.event_name || raw.event || raw.name || raw.kind || '').trim().toLowerCase();
  return eventName === 'settings_changed' || eventName.endsWith('.settings_changed');
}

// re-export so existing call sites that destructured from useRuntime keep working
export { FACTORY_EVENT_CHANNEL, BENCH_EVENT_CHANNEL };

// WebSocketMessage is re-exported for consumers that imported it transitively.
export type { WebSocketMessage };
