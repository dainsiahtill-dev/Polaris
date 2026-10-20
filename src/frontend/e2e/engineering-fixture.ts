/** Test-only observation. No routers, execution controller, or synthetic WS data. */
import type { Page } from '@playwright/test';
import type { EngineeringEvent } from '../src/services/api.types';
export interface EngineeringAcceptanceBinding {
  url: string; instance: string; backend: string; token: string; workspace: string; sessionId: string;
}
export interface EngineeringNetworkAudit {
  requests: Array<{ url: string; method: string; resourceType: string; credentialMatches?: boolean; workspaceMatches?: boolean; credentialObservationFailed?: boolean; startedAt?: number; responseAt?: number; completedAt?: number; snapshotSeq?: number }>;
  responses: Array<{ url: string; contentType: string }>;
  sockets: Array<{ url: string; frames: unknown[]; sent?: unknown[]; credentialMatches?: boolean; engineeringSubscribedAt?: number }>;
  bootstrap?: ReturnType<typeof engineeringBootstrap>;
  pageErrors: string[];
  failedRequests: Array<{ url: string; error: string }>;
}
const isRecord = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const RESERVED = new Set(['49977', '5173', '59977']);
export function parseEngineeringAcceptanceUrl(input: string | undefined): EngineeringAcceptanceBinding {
  if (!input) throw new Error('ENGINEERING_ACCEPTANCE_URL is required; no default instance is allowed');
  const url = new URL(input);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Invalid acceptance URL');
  if (RESERVED.has(url.port)) throw new Error('Acceptance cannot use a reserved main port');
  const values = Object.fromEntries(['instance', 'backend', 'token', 'workspace', 'engineering_run'].map((key) => [key, url.searchParams.get(key)?.trim()]));
  if (Object.values(values).some((value) => !value)) throw new Error('Explicit instance/backend/token/workspace/run binding is required');
  if (url.searchParams.get('engine') !== 'engineering_v2') throw new Error('Acceptance requires explicit engineering_v2');
  const backend = new URL(values.backend!);
  if (!['http:', 'https:'].includes(backend.protocol) || backend.username || backend.password || backend.pathname !== '/' || backend.search || backend.hash) throw new Error('Invalid backend binding');
  if (RESERVED.has(backend.port)) throw new Error('Acceptance backend cannot use a reserved main port');
  return { url: url.href, instance: values.instance!, backend: backend.origin, token: values.token!, workspace: values.workspace!, sessionId: values.engineering_run! };
}
export function redactEngineeringUrl(value: string): string {
  try {
    const url = new URL(value);
    for (const key of [...url.searchParams.keys()]) if (/token|key|password|secret/i.test(key)) url.searchParams.set(key, '[redacted]');
    return url.href;
  } catch { return '[invalid URL]'; }
}
function isScopedApi(url: URL): boolean {
  return /^\/v2\/engineering\/runs(?:\/|$)/.test(url.pathname) || /^\/v2\/context(?:\/|$)/.test(url.pathname);
}
function matchesBearer(headers: Record<string, string>, token: string): boolean {
  const value = Object.entries(headers).find(([key]) => key.toLowerCase() === 'authorization')?.[1];
  return value === `Bearer ${token}`;
}
export function observeEngineeringNetwork(page: Page, binding = parseEngineeringAcceptanceUrl(process.env.ENGINEERING_ACCEPTANCE_URL)): EngineeringNetworkAudit {
  const audit: EngineeringNetworkAudit = { requests: [], responses: [], sockets: [], pageErrors: [], failedRequests: [] };
  const requests = new WeakMap<object, EngineeringNetworkAudit['requests'][number]>();
  page.on('request', (request) => {
    const url = new URL(request.url());
    const observed: EngineeringNetworkAudit['requests'][number] = { url: redactEngineeringUrl(request.url()), method: request.method(), resourceType: request.resourceType(), startedAt: performance.now() };
    requests.set(request, observed);
    if (isScopedApi(url) && observed.method !== 'OPTIONS') {
      let workspace: unknown = url.searchParams.get('workspace');
      if (!['GET', 'HEAD'].includes(observed.method)) {
        try { const body: unknown = request.postDataJSON(); workspace = isRecord(body) ? body.workspace : undefined; }
        catch { workspace = undefined; }
      }
      observed.workspaceMatches = workspace === binding.workspace;
      // Compare actual carriers before redaction; never retain their secret values.
      observed.credentialMatches = matchesBearer(request.headers(), binding.token);
      void request.allHeaders().then((headers) => { observed.credentialMatches = matchesBearer(headers, binding.token); }).catch(() => { observed.credentialObservationFailed = true; });
    }
    audit.requests.push(observed);
  });
  page.on('response', (response) => {
    audit.responses.push({ url: redactEngineeringUrl(response.url()), contentType: response.headers()['content-type'] || '' });
    const observed = requests.get(response.request());
    if (!observed || observed.method !== 'GET' || new URL(observed.url).pathname !== `/v2/engineering/runs/${encodeURIComponent(binding.sessionId)}`) return;
    observed.responseAt = performance.now();
    void response.json().then((snapshot: unknown) => {
      if (response.status() === 200 && isRecord(snapshot) && snapshot.session_id === binding.sessionId && snapshot.workspace === binding.workspace && snapshot.engine_version === 'engineering_v2' && Number.isSafeInteger(snapshot.last_seq) && Number(snapshot.last_seq) >= 0) observed.snapshotSeq = Number(snapshot.last_seq);
    }).catch(() => { /* Missing valid snapshot data fails bootstrap below. */ }).finally(() => { observed.completedAt = performance.now(); });
  });
  page.on('requestfailed', (request) => audit.failedRequests.push({ url: redactEngineeringUrl(request.url()), error: request.failure()?.errorText || 'unknown' }));
  page.on('pageerror', (error) => audit.pageErrors.push(error.message));
  page.on('websocket', (socket) => {
    const url = new URL(socket.url());
    const observed = { url: redactEngineeringUrl(socket.url()), frames: [] as unknown[], sent: [] as unknown[], engineeringSubscribedAt: undefined as number | undefined, ...(url.pathname === '/v2/ws/runtime' ? { credentialMatches: url.searchParams.get('token') === binding.token } : {}) };
    audit.sockets.push(observed);
    socket.on('framereceived', ({ payload }) => { try { observed.frames.push(JSON.parse(String(payload))); } catch { observed.frames.push({ non_json_frame: true }); } });
    socket.on('framesent', ({ payload }) => {
      try {
        const frame: unknown = JSON.parse(String(payload)); observed.sent.push(frame);
        // A sent scoped SUBSCRIBE proves the native socket is open; no wrapper or fake frame.
        if (url.pathname === '/v2/ws/runtime' && isRecord(frame) && frame.type === 'SUBSCRIBE' && Array.isArray(frame.channels) && frame.channels.includes('event.engineering')) observed.engineeringSubscribedAt ??= performance.now();
      } catch { observed.sent.push({ non_json_frame: true }); }
    });
  });
  return audit;
}
export function engineeringSnapshotReads(audit: EngineeringNetworkAudit, binding: EngineeringAcceptanceBinding): number {
  return audit.requests.filter((request) => request.method === 'GET' && new URL(request.url).pathname === `/v2/engineering/runs/${encodeURIComponent(binding.sessionId)}`).length;
}
/** Test-only bootstrap boundary, derived from the observed first scoped connection. */
export function engineeringBootstrap(audit: EngineeringNetworkAudit, binding: EngineeringAcceptanceBinding) {
  const reads = audit.requests.filter((request) => request.method === 'GET' && new URL(request.url).pathname === `/v2/engineering/runs/${encodeURIComponent(binding.sessionId)}`);
  const sockets = audit.sockets.filter((socket) => new URL(socket.url).pathname === '/v2/ws/runtime');
  const subscribedAt = sockets.length === 1 ? sockets[0].engineeringSubscribedAt : undefined;
  const first = reads[0];
  const connectedAfterHydrationStarted = subscribedAt !== undefined && first?.startedAt !== undefined && subscribedAt >= first.startedAt;
  const demand = connectedAfterHydrationStarted ? 'first_scoped_connection' : 'initial_hydration';
  const expected = connectedAfterHydrationStarted ? 2 : 1;
  const freshness = reads[1];
  const explained = !connectedAfterHydrationStarted || (freshness?.startedAt !== undefined && first.responseAt !== undefined && freshness.startedAt >= subscribedAt! && freshness.startedAt >= first.responseAt);
  const ready = subscribedAt !== undefined && first?.startedAt !== undefined && reads.length === expected && explained && reads.every((read) => read.completedAt !== undefined && read.snapshotSeq !== undefined) && inspectEngineeringNetwork(audit, binding, expected).length === 0;
  return { ready, demand, readCount: reads.length, maxSqlSeq: ready ? Math.max(...reads.map((read) => read.snapshotSeq!)) : null, subscribedAt };
}
export function inspectEngineeringNetwork(audit: EngineeringNetworkAudit, binding: EngineeringAcceptanceBinding, expectedSnapshotReads: number): string[] {
  const failures: string[] = [];
  const runtime = audit.sockets.filter((socket) => new URL(socket.url).pathname === '/v2/ws/runtime');
  if (runtime.length !== 1) failures.push(`Expected one runtime WebSocket, received ${runtime.length}`);
  const expectedHttpOrigin = new URL(binding.backend).origin;
  const expectedWsOrigin = new URL(binding.backend.replace(/^http/, 'ws')).origin;
  for (const request of audit.requests) {
    const url = new URL(request.url);
    if (!isScopedApi(url)) continue;
    if (url.origin !== expectedHttpOrigin) failures.push('Engineering/context API backend mismatch');
    if (request.method === 'OPTIONS') continue; // Browser preflight has no application credential/body.
    const workspaceMatches = ['GET', 'HEAD'].includes(request.method) ? url.searchParams.get('workspace') === binding.workspace : request.workspaceMatches === true;
    if (!workspaceMatches) failures.push('Engineering/context API workspace mismatch');
    if (request.credentialMatches !== true) failures.push('Engineering/context API credential mismatch');
  }
  for (const socket of runtime) {
    const url = new URL(socket.url);
    if (url.origin !== expectedWsOrigin) failures.push('Runtime WebSocket backend mismatch');
    if (url.searchParams.get('workspace') !== binding.workspace) failures.push('Runtime WebSocket workspace mismatch');
    if (socket.credentialMatches !== true) failures.push('Runtime WebSocket credential mismatch');
  }
  const reads = engineeringSnapshotReads(audit, binding);
  if (reads !== expectedSnapshotReads) failures.push(`Unexpected engineering snapshot reads: ${reads}, expected ${expectedSnapshotReads}`);
  if (audit.responses.some((response) => response.contentType.includes('text/event-stream'))) failures.push('SSE response observed');
  if (audit.requests.some((request) => request.resourceType === 'eventsource')) failures.push('EventSource request observed');
  if (audit.requests.some((request) => new URL(request.url).pathname.startsWith('/v2/factory/bench'))) failures.push('Global bench request observed');
  if (runtime.some((socket) => socket.sent?.some((frame) => isRecord(frame) && frame.type === 'SUBSCRIBE' && Array.isArray(frame.channels) && frame.channels.includes('event.bench')))) failures.push('Global bench subscription observed');
  failures.push(...audit.pageErrors.map((message) => `Browser error: ${message}`));
  return failures;
}
export function engineeringEvents(frames: unknown[], binding: EngineeringAcceptanceBinding): EngineeringEvent[] {
  return frames.flatMap((frame) => {
    if (!isRecord(frame) || frame.type !== 'EVENT' || frame.protocol !== 'runtime.v2' || !isRecord(frame.event) || frame.event.channel !== 'event.engineering' || !isRecord(frame.event.payload)) return [];
    const payload = frame.event.payload;
    if (payload.session_id !== binding.sessionId || payload.workspace !== binding.workspace || !Number.isSafeInteger(payload.seq) || Number(payload.seq) <= 0 || typeof payload.kind !== 'string' || typeof payload.role !== 'string' || !isRecord(payload.payload)) return [];
    return [payload as unknown as EngineeringEvent];
  });
}
