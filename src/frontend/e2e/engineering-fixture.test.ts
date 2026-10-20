import { describe, expect, it } from 'vitest';
import type { Page } from '@playwright/test';
import { engineeringBootstrap, engineeringEvents, inspectEngineeringNetwork, observeEngineeringNetwork, parseEngineeringAcceptanceUrl, redactEngineeringUrl } from './engineering-fixture';
const valid = 'http://127.0.0.1:53144/?instance=private-fixture&backend=http%3A%2F%2F127.0.0.1%3A53143&token=private-token&workspace=%2Fprivate%2Fworkspace&engine=engineering_v2&engineering_run=r1';
function capturedNetwork(options: { requestOrigin?: string; socketOrigin?: string; requestWorkspace?: string; socketWorkspace?: string; bearer?: string | null; socketToken?: string | null; path?: string; method?: string; body?: unknown; fullHeadersOnly?: boolean } = {}) {
  const binding = parseEngineeringAcceptanceUrl(valid);
  const listeners = new Map<string, (value: unknown) => void>();
  const page = { on: (event: string, callback: (value: unknown) => void) => { listeners.set(event, callback); } } as unknown as Page;
  const network = observeEngineeringNetwork(page, binding);
  const method = options.method || 'GET';
  const url = new URL(options.path || '/v2/engineering/runs/r1', options.requestOrigin || binding.backend);
  if (method === 'GET') url.searchParams.set('workspace', options.requestWorkspace ?? binding.workspace);
  const socketUrl = new URL('/v2/ws/runtime', options.socketOrigin || binding.backend.replace(/^http/, 'ws'));
  socketUrl.searchParams.set('protocol', 'runtime.v2');
  socketUrl.searchParams.set('workspace', options.socketWorkspace ?? binding.workspace);
  if (options.socketToken !== null) socketUrl.searchParams.set('token', options.socketToken ?? binding.token);
  const request = { url: () => url.href, method: () => method, resourceType: () => 'fetch', headers: () => options.bearer === null ? {} : { authorization: `Bearer ${options.bearer ?? binding.token}` }, allHeaders: async () => options.fullHeadersOnly ? { authorization: `Bearer ${binding.token}` } : options.bearer === null ? {} : { authorization: `Bearer ${options.bearer ?? binding.token}` }, postDataJSON: () => options.body ?? { workspace: options.requestWorkspace ?? binding.workspace } };
  listeners.get('request')!(request);
  const socketListeners = new Map<string, (value: unknown) => void>();
  listeners.get('websocket')!({ url: () => socketUrl.href, on: (event: string, callback: (value: unknown) => void) => { socketListeners.set(event, callback); } });
  return { binding, network, listeners, socketListeners, request };
}
describe('engineering acceptance fixture guards', () => {
  it('captures a credential match from complete Playwright headers without serializing that header', async () => {
    const { binding, network } = capturedNetwork({ bearer: null, fullHeadersOnly: true });
    await Promise.resolve();
    expect(inspectEngineeringNetwork(network, binding, 1)).toEqual([]);
    expect(network.requests[0].credentialMatches).toBe(true);
    expect(JSON.stringify(network)).not.toContain(binding.token);
  });
  it.each([
    ['HTTP backend', { requestOrigin: 'http://127.0.0.1:49977' }, 'Engineering/context API backend mismatch'],
    ['WS backend', { socketOrigin: 'ws://127.0.0.1:49977' }, 'Runtime WebSocket backend mismatch'],
    ['HTTP workspace', { requestWorkspace: '/foreign/workspace' }, 'Engineering/context API workspace mismatch'],
    ['WS workspace', { socketWorkspace: '/foreign/workspace' }, 'Runtime WebSocket workspace mismatch'],
    ['HTTP bearer', { bearer: 'wrong-token' }, 'Engineering/context API credential mismatch'],
    ['WS token', { socketToken: 'wrong-token' }, 'Runtime WebSocket credential mismatch'],
    ['missing HTTP bearer', { bearer: null }, 'Engineering/context API credential mismatch'],
    ['missing WS token', { socketToken: null }, 'Runtime WebSocket credential mismatch'],
  ] as const)('rejects observed wrong %s using the real wire carrier', (_name, options, expected) => {
    const { binding, network } = capturedNetwork(options);
    expect(inspectEngineeringNetwork(network, binding, 1)).toContain(expected);
    expect(JSON.stringify(network)).not.toContain(binding.token);
    expect(JSON.stringify(network)).not.toContain('wrong-token');
  });
  it('accepts actual Bearer/query GET, JSON workspace POST, context GET and unauthenticated preflight/static/HMR shapes', () => {
    for (const options of [{}, { path: '/v2/engineering/runs/r1/cancel', method: 'POST' }, { path: `/v2/context/${'a'.repeat(24)}/final-request` }]) {
      const { binding, network } = capturedNetwork(options);
      network.requests.push({ url: 'http://127.0.0.1:53144/src/app.tsx', method: 'GET', resourceType: 'script' });
      network.requests.push({ url: `${binding.backend}/v2/engineering/runs/r1/cancel`, method: 'OPTIONS', resourceType: 'other' });
      network.sockets.push({ url: 'ws://127.0.0.1:53144/?token=[redacted]', frames: [{ type: 'connected' }] });
      expect(inspectEngineeringNetwork(network, binding, options.path ? 0 : 1)).toEqual([]);
      expect(JSON.stringify(network)).not.toContain(binding.token);
    }
  });
  it('checks the POST workspace body and context origin/workspace/auth rather than treating them as static requests', () => {
    const post = capturedNetwork({ method: 'POST', path: '/v2/engineering/runs/r1/cancel', body: { workspace: '/foreign/workspace' } });
    expect(inspectEngineeringNetwork(post.network, post.binding, 0)).toContain('Engineering/context API workspace mismatch');
    const context = capturedNetwork({ path: `/v2/context/${'a'.repeat(24)}`, requestOrigin: 'http://127.0.0.1:49977', requestWorkspace: '/foreign/workspace', bearer: 'wrong-token' });
    expect(inspectEngineeringNetwork(context.network, context.binding, 0)).toEqual(expect.arrayContaining(['Engineering/context API backend mismatch', 'Engineering/context API workspace mismatch', 'Engineering/context API credential mismatch']));
  });
  it('fails closed when the before-redaction credential observation is missing', () => {
    const { binding, network } = capturedNetwork();
    delete network.requests[0].credentialMatches;
    delete network.sockets[0].credentialMatches;
    expect(inspectEngineeringNetwork(network, binding, 1)).toEqual(expect.arrayContaining(['Engineering/context API credential mismatch', 'Runtime WebSocket credential mismatch']));
  });
  it('requires an explicit acceptance URL and complete instance authority binding', () => {
    expect(() => parseEngineeringAcceptanceUrl(undefined)).toThrow(/ENGINEERING_ACCEPTANCE_URL/);
    expect(parseEngineeringAcceptanceUrl(valid)).toMatchObject({ instance: 'private-fixture', workspace: '/private/workspace', sessionId: 'r1', backend: 'http://127.0.0.1:53143' });
  });
  it.each(['instance', 'backend', 'token', 'workspace', 'engineering_run'])('rejects a missing %s binding', (key) => {
    const url = new URL(valid); url.searchParams.delete(key);
    expect(() => parseEngineeringAcceptanceUrl(url.href)).toThrow(/binding/);
  });
  it.each([49977, 5173, 59977])('refuses reserved main port %s', (port) => {
    const frontend = new URL(valid); frontend.port = String(port);
    expect(() => parseEngineeringAcceptanceUrl(frontend.href)).toThrow(/reserved/);
    const backend = new URL(valid); backend.searchParams.set('backend', `http://127.0.0.1:${port}`);
    expect(() => parseEngineeringAcceptanceUrl(backend.href)).toThrow(/reserved/);
  });
  it('requires the explicit new engine and hides access tokens in public evidence', () => {
    expect(() => parseEngineeringAcceptanceUrl(valid.replace('engineering_v2', 'legacy'))).toThrow(/engineering_v2/);
    expect(redactEngineeringUrl(valid)).not.toContain('private-token');
  });
  it('rejects polling-equivalent extra snapshot reads, SSE, second runtime socket and global bench requests', () => {
    const binding = parseEngineeringAcceptanceUrl(valid);
    const { network } = capturedNetwork();
    expect(inspectEngineeringNetwork(network, binding, 1)).toEqual([]);
    expect(inspectEngineeringNetwork({ ...network, requests: [...network.requests, ...network.requests] }, binding, 1)).toContain('Unexpected engineering snapshot reads: 2, expected 1');
    expect(inspectEngineeringNetwork({ ...network, sockets: [...network.sockets, ...network.sockets] }, binding, 1)).toContain('Expected one runtime WebSocket, received 2');
    expect(inspectEngineeringNetwork({ ...network, responses: [{ url: binding.backend, contentType: 'text/event-stream' }] }, binding, 1)).toContain('SSE response observed');
    expect(inspectEngineeringNetwork({ ...network, requests: [...network.requests, { url: `${binding.backend}/v2/factory/bench/sessions`, method: 'GET', resourceType: 'fetch' }] }, binding, 1)).toContain('Global bench request observed');
  });
  it('uses payload SQL seq and same workspace/run scope rather than transport cursor', () => {
    const binding = parseEngineeringAcceptanceUrl(valid);
    const frame = { type: 'EVENT', protocol: 'runtime.v2', cursor: 99999, event: { channel: 'event.engineering', run_id: 'r1', payload: { session_id: 'r1', workspace: binding.workspace, seq: 2, kind: 'role_started', role: 'director', payload: {} } } };
    expect(engineeringEvents([frame], binding).map((event) => event.seq)).toEqual([2]);
    expect(engineeringEvents([{ ...frame, event: { ...frame.event, payload: { ...frame.event.payload, workspace: '/foreign' } } }], binding)).toEqual([]);
  });
});

describe('causal engineering bootstrap accounting', () => {
  it('records the actual observer request/response and first scoped SUBSCRIBE callbacks', async () => {
    const { binding, network, listeners, socketListeners, request } = capturedNetwork();
    socketListeners.get('framesent')!({ payload: JSON.stringify({ type: 'SUBSCRIBE', channels: ['event.engineering'] }) });
    const subscribedAt = network.sockets[0].engineeringSubscribedAt;
    socketListeners.get('framesent')!({ payload: JSON.stringify({ type: 'SUBSCRIBE', channels: ['event.engineering'] }) });
    expect(network.sockets[0].engineeringSubscribedAt).toBe(subscribedAt);
    listeners.get('response')!({ url: request.url, request: () => request, headers: () => ({ 'content-type': 'application/json' }), status: () => 200, json: async () => ({ session_id: binding.sessionId, workspace: binding.workspace, engine_version: 'engineering_v2', last_seq: 8 }) });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(network.requests[0]).toMatchObject({ startedAt: expect.any(Number), responseAt: expect.any(Number), completedAt: expect.any(Number), snapshotSeq: 8 });
    expect(subscribedAt).toEqual(expect.any(Number));
  });
  function bootstrap(subscriptionAt: number, reads = 1) {
    const { binding, network } = capturedNetwork();
    Object.assign(network.requests[0], { startedAt: 10, responseAt: 20, completedAt: 21, snapshotSeq: 8 });
    Object.assign(network.sockets[0], { engineeringSubscribedAt: subscriptionAt });
    if (reads >= 2) network.requests.push({ ...network.requests[0], startedAt: 30, responseAt: 40, completedAt: 41, snapshotSeq: 11 });
    if (reads >= 3) network.requests.push({ ...network.requests[0], startedAt: 50, responseAt: 60, completedAt: 61, snapshotSeq: 12 });
    return { binding, network };
  }
  it('allows only initial hydration when the scoped subscription predates it', () => {
    const { binding, network } = bootstrap(5);
    expect(engineeringBootstrap(network, binding)).toMatchObject({ ready: true, readCount: 1, maxSqlSeq: 8, demand: 'initial_hydration' });
  });
  it('waits for the first-connect demand and uses both completed SQL watermarks', () => {
    const { binding, network } = bootstrap(15);
    expect(engineeringBootstrap(network, binding).ready).toBe(false);
    network.requests.push({ ...network.requests[0], startedAt: 30, responseAt: 40, completedAt: 41, snapshotSeq: 6 });
    expect(engineeringBootstrap(network, binding)).toMatchObject({ ready: true, readCount: 2, maxSqlSeq: 8, demand: 'first_scoped_connection' });
    delete network.requests[1].completedAt;
    expect(engineeringBootstrap(network, binding).ready).toBe(false);
  });
  it.each(['third', 'pre-subscription second', 'already-connected second', 'missing subscription', 'invalid snapshot'] as const)('rejects unexplained bootstrap: %s', (reason) => {
    const { binding, network } = bootstrap(reason === 'already-connected second' ? 5 : 15, reason === 'third' ? 3 : 2);
    if (reason === 'pre-subscription second') network.requests[1].startedAt = 12;
    if (reason === 'missing subscription') delete network.sockets[0].engineeringSubscribedAt;
    if (reason === 'invalid snapshot') delete network.requests[1].snapshotSeq;
    expect(engineeringBootstrap(network, binding).ready).toBe(false);
  });
});
