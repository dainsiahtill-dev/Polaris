import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type TestWindow = Window & {
  __DEV_BACKEND__?: {
    baseUrl?: string;
    token?: string;
  };
  polaris?: unknown;
};

describe('getBackendInfo web fallback', () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    window.history.pushState({}, '', '/');
    delete (window as TestWindow).__DEV_BACKEND__;
    delete (window as TestWindow).polaris;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the local Polaris backend by default in Vite web mode', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { getBackendInfo } = await import('./api');

    await expect(getBackendInfo()).resolves.toMatchObject({
      baseUrl: 'http://127.0.0.1:49977',
      token: 'polaris-local-dev',
      port: null,
    });

    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Using default development token "polaris-local-dev"')
    );
    warnSpy.mockRestore();
  });

  it('does not warn when a persisted custom token is used', async () => {
    localStorage.setItem('polaris.token', 'custom-token');
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { getBackendInfo } = await import('./api');

    await expect(getBackendInfo()).resolves.toMatchObject({
      baseUrl: 'http://127.0.0.1:49977',
      token: 'custom-token',
    });

    expect(warnSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it('honors persisted backend overrides', async () => {
    localStorage.setItem('polaris.baseUrl', 'http://127.0.0.1:49988');
    localStorage.setItem('polaris.token', 'custom-token');
    const { getBackendInfo } = await import('./api');

    await expect(getBackendInfo()).resolves.toMatchObject({
      baseUrl: 'http://127.0.0.1:49988',
      token: 'custom-token',
    });
  });

  it('honors instance URL backend overrides before persisted global settings', async () => {
    localStorage.setItem('polaris.baseUrl', 'http://127.0.0.1:49988');
    localStorage.setItem('polaris.token', 'custom-token');
    window.history.pushState(
      {},
      '',
      '/?instance=bench-l1-01&backend=http://127.0.0.1:50017&token=instance-token',
    );
    const { getBackendInfo } = await import('./api');

    await expect(getBackendInfo()).resolves.toMatchObject({
      baseUrl: 'http://127.0.0.1:50017',
      token: 'instance-token',
    });
  });

  it('uses instance-scoped persisted backend overrides when instance is present', async () => {
    localStorage.setItem('polaris.baseUrl', 'http://127.0.0.1:49988');
    localStorage.setItem('polaris.token', 'global-token');
    localStorage.setItem('polaris.instances.alpha.polaris.baseUrl', 'http://127.0.0.1:50021');
    localStorage.setItem('polaris.instances.alpha.polaris.token', 'alpha-token');
    window.history.pushState({}, '', '/?instance=alpha');
    const { getBackendInfo } = await import('./api');

    await expect(getBackendInfo()).resolves.toMatchObject({
      baseUrl: 'http://127.0.0.1:50021',
      token: 'alpha-token',
    });
  });

  it('falls back to the default backend when a persisted HTTP base URL is unreachable', async () => {
    localStorage.setItem('polaris.baseUrl', 'http://127.0.0.1:49988');
    localStorage.setItem('polaris.token', 'custom-token');
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('connection refused'))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./api');

    const response = await apiFetch('/settings');

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:49988/settings');
    expect(fetchMock.mock.calls[1][0]).toBe('http://127.0.0.1:49977/settings');
    expect(localStorage.getItem('polaris.baseUrl')).toBeNull();
    expect(localStorage.getItem('polaris.token')).toBeNull();
  });

  it('uses the default backend for WebSocket when the persisted base URL probe fails', async () => {
    localStorage.setItem('polaris.baseUrl', 'http://127.0.0.1:49988');
    localStorage.setItem('polaris.token', 'custom-token');
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('connection refused'))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    class MockWebSocket {
      readonly url: string;

      constructor(url: string) {
        this.url = url;
      }
    }

    vi.stubGlobal('WebSocket', MockWebSocket);
    const { connectWebSocket } = await import('./api');

    const socket = await connectWebSocket();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:49988/health');
    expect(fetchMock.mock.calls[1][0]).toBe('http://127.0.0.1:49977/health');
    expect((socket as unknown as MockWebSocket).url).toBe(
      'ws://127.0.0.1:49977/v2/ws/runtime?token=polaris-local-dev'
    );
    expect(localStorage.getItem('polaris.baseUrl')).toBeNull();
    expect(localStorage.getItem('polaris.token')).toBeNull();
  });
});

describe('apiFetch auth token discovery', () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    window.history.pushState({}, '', '/');
    delete (window as TestWindow).__DEV_BACKEND__;
    delete (window as TestWindow).polaris;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('succeeds with default polaris-local-dev token when backend accepts it', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), { status: 200 })
    );
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./api');

    const response = await apiFetch('/settings');

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://127.0.0.1:49977/settings');
    const headers = init.headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer polaris-local-dev');
  });

  it('does not call /v2/auth/token when first request succeeds', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), { status: 200 })
    );
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./api');

    await apiFetch('/settings');

    const calledUrls = fetchMock.mock.calls.map((c: unknown[]) => c[0] as string);
    expect(calledUrls.every((u: string) => !u.includes('/v2/auth/token'))).toBe(true);
  });

  it('handles 401 then 404 from /v2/auth/token (discovery disabled) gracefully', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('Unauthorized', { status: 401 }))
      .mockResolvedValueOnce(new Response('Not Found', { status: 404 }))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./api');

    const response = await apiFetch('/settings');

    expect(response.status).toBe(200);
    const calledUrls = fetchMock.mock.calls.map((c: unknown[]) => c[0] as string);
    expect(calledUrls).toContain('http://127.0.0.1:49977/v2/auth/token');
  });

  it('does not leak token in URL query when discovery returns 404', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('Unauthorized', { status: 401 }))
      .mockResolvedValueOnce(new Response('Not Found', { status: 404 }))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./api');

    await apiFetch('/settings');

    const discoveryCall = fetchMock.mock.calls.find((c: unknown[]) =>
      (c[0] as string).includes('/v2/auth/token')
    );
    expect(discoveryCall).toBeDefined();
    const discoveryUrl = discoveryCall![0] as string;
    expect(discoveryUrl).not.toContain('token=');
    expect(discoveryUrl).not.toContain('polaris-local-dev');
  });

  it('uses discovered token when /v2/auth/token succeeds with a different token', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('Unauthorized', { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ token: 'discovered-secret' }), { status: 200 })
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { apiFetch } = await import('./api');

    const response = await apiFetch('/settings');

    expect(response.status).toBe(200);
    const secondAttemptCall = fetchMock.mock.calls[2];
    const headers = secondAttemptCall![1].headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer discovered-secret');
  });
});
