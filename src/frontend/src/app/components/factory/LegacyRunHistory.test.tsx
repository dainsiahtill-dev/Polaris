import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { LegacyRunHistory } from './LegacyRunHistory';
const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('@/services/apiClient', () => ({ apiGet: api.get }));
describe('LegacyRunHistory', () => {
  beforeEach(() => { api.get.mockReset(); });
  it('reads scoped historical Factory evidence and preserves failure without offering write actions', async () => {
    api.get.mockImplementation(async (url: string) => ({ ok: true, data: url.startsWith('/v2/factory/runs?') ? { runs: [{ run_id: 'old-1', workspace: '/work', status: 'failed' }] } : url.includes('/artifacts') ? { summary_md: 'Original failing check', artifacts: [] } : { run_id: 'old-1', workspace: '/work', status: 'failed', gates: [{ passed: false, message: 'Original gate failure' }] } }));
    render(<LegacyRunHistory workspace="/work" onBackToMain={() => { }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'old-1 · failed' }));
    expect(await screen.findByText(/Original gate failure/)).toBeInTheDocument();
    expect(screen.getByText(/Original failing check/)).toBeInTheDocument();
    expect(api.get.mock.calls.every(([url]) => new URL(url, 'http://local').searchParams.get('workspace') === '/work')).toBe(true);
    expect(screen.queryByRole('button', { name: /启动|重试|继续|执行|取消/ })).not.toBeInTheDocument();
  });
  it('reads role session messages and audit through GET only and rejects another workspace', async () => {
    api.get.mockImplementation(async (url: string) => ({ ok: true, data: url.startsWith('/v2/factory/') ? { runs: [] } : url.startsWith('/v2/roles/sessions?') ? { sessions: [{ id: 'pm-old', title: 'Old planning', role: 'pm', state: 'failed' }] } : { session: { id: 'pm-old', workspace: '/other' }, messages: [{ content: 'Other project secret' }], audit_events: [], artifacts: [] } }));
    render(<LegacyRunHistory workspace="/work" onBackToMain={() => { }} />);
    fireEvent.change(screen.getByLabelText('历史类型'), { target: { value: 'pm' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Old planning · failed' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('历史记录 workspace 不匹配');
    expect(screen.queryByText(/Other project secret/)).not.toBeInTheDocument();
  });
  it('reads original role messages and audit for an explicitly selected host', async () => {
    api.get.mockImplementation(async (url: string) => ({ ok: true, data: url.startsWith('/v2/factory/') ? { runs: [] } : url.startsWith('/v2/roles/sessions?') ? { sessions: [{ id: 'cli-old', title: 'CLI planning', role: 'pm', state: 'failed' }] } : url.includes('/messages') ? { session: { workspace: '/work' }, messages: [{ content: 'Original unfinished advice' }] } : url.includes('/audit') ? { audit_events: [{ event_type: 'tool_failed' }] } : url.includes('/artifacts') ? { artifacts: [] } : { session: { id: 'cli-old', workspace: '/work' } } }));
    render(<LegacyRunHistory workspace="/work" onBackToMain={() => { }} />);
    fireEvent.change(screen.getByLabelText('历史类型'), { target: { value: 'pm' } });
    fireEvent.change(screen.getByLabelText('历史宿主'), { target: { value: 'cli' } });
    fireEvent.click(await screen.findByRole('button', { name: 'CLI planning · failed' }));
    expect(await screen.findByText(/Original unfinished advice/)).toHaveTextContent('tool_failed');
    expect(api.get.mock.calls.some(([url]) => url.startsWith('/v2/roles/sessions?') && new URL(url, 'http://local').searchParams.get('host_kind') === 'cli')).toBe(true);
    expect(screen.queryByRole('button', { name: /启动|重试|继续|执行|取消/ })).not.toBeInTheDocument();
  });
  it('treats a malformed historical list as unavailable evidence', async () => {
    api.get.mockResolvedValue({ ok: true, data: { runs: null } });
    render(<LegacyRunHistory workspace="/work" onBackToMain={() => { }} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('历史列表格式不可用');
    expect(screen.queryByText('当前工作区暂无此类历史记录')).not.toBeInTheDocument();
  });
  it('shows read failures and refreshes only after an explicit request', async () => {
    api.get.mockResolvedValue({ ok: false, error: 'history offline' });
    render(<LegacyRunHistory workspace="/work" onBackToMain={() => { }} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('history offline');
    expect(api.get).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '刷新历史' }));
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  });
});
