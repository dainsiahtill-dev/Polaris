import { beforeEach, describe, expect, it, vi } from 'vitest';

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
});
