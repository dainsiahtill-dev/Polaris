import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetchMock = vi.fn();

vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

vi.mock('@/app/utils/devLogger', () => ({
  devLogger: {
    warn: vi.fn(),
  },
}));

import { processService, statusService } from '../api';

describe('processService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('labels Director start fallback errors as Director failures', async () => {
    apiFetchMock.mockResolvedValueOnce(new Response(null, { status: 500 }));

    const result = await processService.startDirector();

    expect(result.ok).toBe(false);
    expect(result.error).toBe('Failed to start Director');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/start', { method: 'POST' });
  });

  it('labels Director stop fallback errors as Director failures', async () => {
    apiFetchMock.mockResolvedValueOnce(new Response(null, { status: 500 }));

    const result = await processService.stopDirector();

    expect(result.ok).toBe(false);
    expect(result.error).toBe('Failed to stop Director');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/stop', { method: 'POST' });
  });

  it('passes workspace through process fallback controls', async () => {
    apiFetchMock.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));

    await processService.startPm(true, 'C:/Temp/Product');
    await processService.stopPm('C:/Temp/Product');
    await processService.runPmOnce('C:/Temp/Product');
    await processService.startDirector('C:/Temp/Product');
    await processService.stopDirector('C:/Temp/Product');

    expect(apiFetchMock).toHaveBeenNthCalledWith(
      1,
      '/v2/pm/start?resume=true&workspace=C%3A%2FTemp%2FProduct',
      { method: 'POST' },
    );
    expect(apiFetchMock).toHaveBeenNthCalledWith(
      2,
      '/v2/pm/stop?workspace=C%3A%2FTemp%2FProduct',
      { method: 'POST' },
    );
    expect(apiFetchMock).toHaveBeenNthCalledWith(
      3,
      '/v2/pm/run_once?workspace=C%3A%2FTemp%2FProduct',
      { method: 'POST' },
    );
    expect(apiFetchMock).toHaveBeenNthCalledWith(
      4,
      '/v2/director/start?workspace=C%3A%2FTemp%2FProduct',
      { method: 'POST' },
    );
    expect(apiFetchMock).toHaveBeenNthCalledWith(
      5,
      '/v2/director/stop?workspace=C%3A%2FTemp%2FProduct',
      { method: 'POST' },
    );
  });
});

describe('statusService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('passes workspace through PM and Director status reads', async () => {
    apiFetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ running: false }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ running: true, pid: 42, source: 'status_file' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ running: false }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: 'RUNNING', source: 'workflow' }), { status: 200 }));

    await statusService.getPm('C:/Temp/Product');
    await statusService.getDirector('C:/Temp/Product');
    await statusService.getAll('C:/Temp/Product');

    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/v2/pm/status?workspace=C%3A%2FTemp%2FProduct');
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/v2/director/status?workspace=C%3A%2FTemp%2FProduct');
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/v2/pm/status?workspace=C%3A%2FTemp%2FProduct');
    expect(apiFetchMock).toHaveBeenNthCalledWith(4, '/v2/director/status?workspace=C%3A%2FTemp%2FProduct');
  });
});
