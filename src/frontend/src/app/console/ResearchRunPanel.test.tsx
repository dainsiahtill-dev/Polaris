import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ApiResult } from '@/services/api.types';
import { apiGet } from '@/services/apiClient';
import { ResearchRunPanel } from './ResearchRunPanel';

vi.mock('@/services/apiClient', () => ({ apiGet: vi.fn() }));
const get = vi.mocked(apiGet);
const workspace = '/workspace/study';
const current = { project: 'case', lab: 'review', plan_sha256: 'a'.repeat(64) };
const previous = { ...current, plan_sha256: 'b'.repeat(64) };
const makeRow = (sessionId: string, sha: string) => ({ session_id: sessionId, lab: current.lab, status: 'completed', verification: 'passed', plan_sha256: sha, created_at: 100 });
const fixed = { schema: 'engineering.research-plan.v1', project: 'case', experiments: [{ lab: 'review', id: 'first', scope: '历史版本' }] };

describe('research run inspection', () => {
  it('looks up history only on demand and opens the pinned plan of the clicked historical run', async () => {
    get.mockReset();
    render(<ResearchRunPanel workspace={workspace} sessionId="new" identity={current} events={[]} />);
    expect(get).not.toHaveBeenCalled();
    expect(screen.getByText(current.plan_sha256)).toBeInTheDocument();
    get.mockResolvedValueOnce({ ok: true, data: { project: 'case', workspace, lab: 'review', runs: [makeRow('old', previous.plan_sha256)], next_offset: null } });
    fireEvent.click(screen.getByRole('button', { name: '本实验室历史' }));
    const history = await screen.findByRole('region', { name: '实验室运行记录' });
    expect(await within(history).findByText('old')).toBeInTheDocument();
    get.mockResolvedValueOnce({ ok: true, data: { workspace, session_id: 'old', research: { ...previous, plan: fixed } } });
    fireEvent.click(within(history).getByRole('button', { name: '查看 old 的固定计划' }));
    const detail = await screen.findByRole('region', { name: '固定研究计划' });
    expect(await within(detail).findByText(/历史版本/)).toBeInTheDocument();
    expect(within(detail).getByText(previous.plan_sha256)).toBeInTheDocument();
    expect(get.mock.calls[1][0]).toContain('/runs/old/research-plan?workspace=');
  });

  it('ignores responses superseded by a different query', async () => {
    get.mockReset();
    let finish!: (value: ApiResult<unknown>) => void;
    get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    render(<ResearchRunPanel workspace={workspace} sessionId="new" identity={current} events={[]} />);
    fireEvent.click(screen.getByRole('button', { name: '项目实验室' }));
    get.mockResolvedValueOnce({ ok: true, data: { workspace, project: 'case', lab: 'review', runs: [makeRow('history-run', previous.plan_sha256)], next_offset: null } });
    fireEvent.click(screen.getByRole('button', { name: '本实验室历史' }));
    expect(await screen.findByText('history-run')).toBeInTheDocument();
    await act(async () => finish({ ok: true, data: { workspace, project: 'case', laboratories: [makeRow('stale-directory-run', current.plan_sha256)], next_offset: null } }));
    expect(screen.queryByText('stale-directory-run')).toBeNull();
    expect(screen.getByText('history-run')).toBeInTheDocument();
  });

  it('paginates a laboratory directory and scopes drill-down to the selected laboratory', async () => {
    get.mockReset();
    render(<ResearchRunPanel workspace={workspace} sessionId="new" identity={current} events={[]} />);
    const rows = Array.from({ length: 20 }, (_, i) => ({ ...makeRow(`run-${i}`, current.plan_sha256), lab: `lab-${i}` }));
    get.mockResolvedValueOnce({ ok: true, data: { workspace, project: 'case', laboratories: rows, next_offset: 20 } });
    fireEvent.click(screen.getByRole('button', { name: '项目实验室' }));
    const next = await screen.findByRole('button', { name: '下一页' });
    const extra = { ...makeRow('last-run', previous.plan_sha256), lab: 'last-lab' };
    get.mockResolvedValueOnce({ ok: true, data: { workspace, project: 'case', laboratories: [extra], next_offset: null } });
    fireEvent.click(next);
    expect(await screen.findByText('last-run')).toBeVisible();
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
    expect(new URL(get.mock.calls[1][0], 'http://localhost').searchParams.get('offset')).toBe('20');
    get.mockResolvedValueOnce({ ok: true, data: { workspace, project: 'case', lab: 'last-lab', runs: [extra], next_offset: null } });
    fireEvent.click(screen.getByRole('button', { name: '查看 last-lab 的历史' }));
    expect(await screen.findByRole('heading', { name: 'last-lab 的运行历史' })).toBeVisible();
    expect(new URL(get.mock.calls[2][0], 'http://localhost').searchParams.get('lab')).toBe('last-lab');
  });

  it('clears open records when the workspace or run binding changes', async () => {
    get.mockReset();
    const view = render(<ResearchRunPanel workspace={workspace} sessionId="new" identity={current} events={[]} />);
    get.mockResolvedValueOnce({ ok: true, data: { workspace, project: 'case', lab: current.lab, runs: [makeRow('previous-workspace-run', current.plan_sha256)], next_offset: null } });
    fireEvent.click(screen.getByRole('button', { name: '本实验室历史' }));
    expect(await screen.findByText('previous-workspace-run')).toBeVisible();
    view.rerender(<ResearchRunPanel workspace="/another-workspace" sessionId="another" identity={current} events={[]} />);
    expect(screen.queryByRole('region', { name: '实验室运行记录' })).toBeNull();
    expect(get).toHaveBeenCalledTimes(1);
  });
});
