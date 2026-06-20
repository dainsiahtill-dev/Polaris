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
    delete (window as TestWindow).__DEV_BACKEND__;
    delete (window as TestWindow).polaris;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the local Polaris backend by default in Vite web mode', async () => {
    const { getBackendInfo } = await import('./api');

    await expect(getBackendInfo()).resolves.toMatchObject({
      baseUrl: 'http://127.0.0.1:49977',
      token: 'polaris-local-dev',
      port: null,
    });
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
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:49988/v2/live');
    expect(fetchMock.mock.calls[1][0]).toBe('http://127.0.0.1:49977/v2/live');
    expect((socket as unknown as MockWebSocket).url).toBe(
      'ws://127.0.0.1:49977/v2/ws/runtime?token=polaris-local-dev'
    );
    expect(localStorage.getItem('polaris.baseUrl')).toBeNull();
    expect(localStorage.getItem('polaris.token')).toBeNull();
  });
});
