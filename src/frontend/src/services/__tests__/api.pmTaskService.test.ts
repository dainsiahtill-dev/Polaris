import { describe, expect, it, beforeEach, vi } from 'vitest';

const apiFetchMock = vi.fn();

vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

vi.mock('@/app/utils/devLogger', () => ({
  devLogger: {
    warn: vi.fn(),
  },
}));

import { pmRequirementService, pmTaskService } from '../api';

describe('pmTaskService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('reads PM task list responses with desktop items and total fields', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          tasks: [{ id: 'TASK-0001', subject: 'Legacy-compatible task', status: 'pending' }],
          items: [{ id: 'TASK-0001', subject: 'Legacy-compatible task', status: 'pending' }],
          total: 1,
        }),
        { status: 200 },
      ),
    );

    const result = await pmTaskService.list();

    expect(result.ok).toBe(true);
    expect(result.data?.items).toHaveLength(1);
    expect(result.data?.total).toBe(1);
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/pm/tasks');
  });

  it('passes explicit workspace when listing PM tasks', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          tasks: [],
          items: [],
          total: 0,
        }),
        { status: 200 },
      ),
    );

    const result = await pmTaskService.list('C:/Temp/Product');

    expect(result.ok).toBe(true);
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/pm/tasks?workspace=C%3A%2FTemp%2FProduct');
  });

  it('creates PM tasks through POST /v2/pm/tasks', async () => {
    const payload = {
      subject: 'Close PM route gap',
      description: 'Wire task creation to the backend',
      priority: 'high',
      status: 'pending',
      acceptance: ['POST returns task detail'],
      assignee: 'pm',
      due_date: '2026-06-01',
      tags: ['desktop'],
      parent_id: 'parent-1',
    };

    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: 'TASK-0001',
          subject: 'Close PM route gap',
          title: 'Close PM route gap',
          status: 'pending',
          priority: 'high',
        }),
        { status: 200 },
      ),
    );

    const result = await pmTaskService.create(payload);

    expect(result.ok).toBe(true);
    expect(result.data?.id).toBe('TASK-0001');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/pm/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  });

  it('creates PM tasks with explicit workspace', async () => {
    const payload = {
      subject: 'Close PM route gap',
      description: 'Wire task creation to the backend',
    };

    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: 'TASK-0001',
          subject: 'Close PM route gap',
          title: 'Close PM route gap',
          status: 'pending',
        }),
        { status: 200 },
      ),
    );

    const result = await pmTaskService.create(payload, 'C:/Temp/Product');

    expect(result.ok).toBe(true);
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/pm/tasks?workspace=C%3A%2FTemp%2FProduct', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  });
});

describe('pmRequirementService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('reads PM requirement list responses with desktop items and total fields', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          requirements: [{ id: 'REQ-1', title: 'Traceable requirement', status: 'open' }],
          items: [{ id: 'REQ-1', title: 'Traceable requirement', status: 'open' }],
          total: 1,
        }),
        { status: 200 },
      ),
    );

    const result = await pmRequirementService.list();

    expect(result.ok).toBe(true);
    expect(result.data?.items).toHaveLength(1);
    expect(result.data?.total).toBe(1);
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/pm/requirements');
  });

  it('passes explicit workspace when listing PM requirements', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          requirements: [],
          items: [],
          total: 0,
        }),
        { status: 200 },
      ),
    );

    const result = await pmRequirementService.list('C:/Temp/Product');

    expect(result.ok).toBe(true);
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/pm/requirements?workspace=C%3A%2FTemp%2FProduct');
  });

  it('loads PM requirement detail with encoded id and workspace', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: 'REQ/1',
          title: 'Workspace-specific requirement',
          status: 'open',
        }),
        { status: 200 },
      ),
    );

    const result = await pmRequirementService.get('REQ/1', 'C:/Temp/Product');

    expect(result.ok).toBe(true);
    expect(result.data?.id).toBe('REQ/1');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/pm/requirements/REQ%2F1?workspace=C%3A%2FTemp%2FProduct');
  });
});
