import React from 'react';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ subscribe: vi.fn(() => vi.fn()), register: vi.fn(() => vi.fn()), get: vi.fn(), start: vi.fn(), cancel: vi.fn(), resume: vi.fn(), discuss: vi.fn() }));
let receive: (message: unknown) => void = () => { };
let connected = true;
function registerMessageHandler(fn: (message: unknown) => void) { receive = fn; return mocks.register(fn); }
vi.mock('@/runtime/transport', () => ({ useTransportActions: () => ({ subscribeChannels: mocks.subscribe }), useMessageHandler: () => ({ registerMessageHandler }), useConnectionState: () => ({ connected, reconnecting: !connected, error: null, attemptCount: 0 }) }));
vi.mock('@/services/engineeringRunService', () => ({ getEngineeringRun: mocks.get, startEngineeringRun: mocks.start, cancelEngineeringRun: mocks.cancel, resumeEngineeringRun: mocks.resume, discussEngineeringRole: mocks.discuss }));
import { useEngineeringRun } from './useEngineeringRun';
import { EngineeringRunWorkspace } from '@/app/components/factory/EngineeringRunWorkspace';
const saved = { session_id: 'r1', run_id: 'r1', engine_version: 'engineering_v2' as const, workspace: '/work', workflow: 'direct' as const, role: 'director' as const, status: 'running' as const, active_roles: [], roles: { pm: 'not_called' as const, chief_engineer: 'not_called' as const, director: 'running' as const, qa: 'not_called' as const }, text: '', verification: 'not_run' as const, checks: [], required_checks: [], permission_profile_id: null, resource_url: '/v2/engineering/runs/r1', last_seq: 1, events: [] };
function message(seq: number, kind: string, payload: Record<string, unknown>, workspace = '/work') { return { type: 'EVENT', protocol: 'runtime.v2', cursor: 900000 + seq, event: { channel: 'event.engineering', run_id: 'r1', payload: { session_id: 'r1', workspace, seq, role: 'director', kind, payload } } }; }
beforeEach(() => { vi.clearAllMocks(); connected = true; mocks.get.mockResolvedValue({ ok: true, data: saved }); mocks.cancel.mockResolvedValue({ ok: true, data: { ...saved, status: 'cancelled', last_seq: 4 } }); mocks.resume.mockResolvedValue({ ok: true, data: { ...saved, last_seq: 5 } }); });
describe('useEngineeringRun', () => {
  it('marks cached passed verification unavailable after a failed resync read until a full snapshot recovers', async () => {
    mocks.get.mockResolvedValueOnce({ ok: true, data: { ...saved, status: 'completed', verification: 'passed' } });
    mocks.get.mockResolvedValue({ ok: false, error: 'snapshot offline' });
    const observed: { binding: ReturnType<typeof useEngineeringRun> | null } = { binding: null };
    function Workspace() {
      const binding = useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled: true });
      observed.binding = binding;
      return <EngineeringRunWorkspace workspace="/work" binding={binding} onBackToMain={() => { }} />;
    }
    render(<Workspace />);
    await waitFor(() => expect(screen.getByTestId('engineering-verification')).toHaveTextContent('已通过'));
    act(() => receive({ type: 'RESYNC_REQUIRED', protocol: 'runtime.v2' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('snapshot offline'));
    expect(screen.getByTestId('engineering-verification')).toHaveTextContent('未知（快照不可用）');
    expect(observed.binding?.projection.snapshot?.verification).toBe('passed');
    act(() => receive(message(2, 'tool_observed', { tool: 'read_file', ok: true, content: 'read result' })));
    expect(screen.getByTestId('engineering-verification')).toHaveTextContent('未知（快照不可用）');
    mocks.get.mockResolvedValue({ ok: true, data: { ...saved, status: 'failed', verification: 'failed', last_seq: 7 } });
    fireEvent.click(screen.getByRole('button', { name: '刷新工程快照' }));
    await waitFor(() => expect(screen.getByTestId('engineering-verification')).toHaveTextContent('未通过'));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledTimes(3);
  });
  it.each(['RESYNC_REQUIRED', 'reconnect'])('reads a fresh snapshot after %s arrives during an older GET', async (control) => {
    let finish: (value: unknown) => void = () => { };
    mocks.get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    mocks.get.mockResolvedValue({ ok: true, data: { ...saved, status: 'failed', verification: 'failed', last_seq: 7 } });
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled: true }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    if (control === 'RESYNC_REQUIRED') act(() => receive({ type: 'RESYNC_REQUIRED', protocol: 'runtime.v2', cursor: 97000 }));
    else { connected = false; view.rerender(); connected = true; view.rerender(); }
    expect(mocks.get).toHaveBeenCalledTimes(1);
    await act(async () => finish({ ok: true, data: { ...saved, status: 'completed', verification: 'passed' } }));
    await waitFor(() => expect(view.result.current.projection.snapshot?.verification).toBe('failed'));
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(view.result.current.projection.lastSeq).toBe(7);
  });
  it.each([true, false])('coalesces controls into one later query and stops after failure (initial success=%s)', async (initialSuccess) => {
    let finish: (value: unknown) => void = () => { };
    mocks.get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    mocks.get.mockResolvedValue({ ok: false, error: 'snapshot offline' });
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled: true }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    act(() => { receive({ type: 'RESYNC_REQUIRED', protocol: 'runtime.v2' }); receive({ type: 'RESYNC_REQUIRED', protocol: 'runtime.v2' }); });
    connected = false; view.rerender(); connected = true; view.rerender();
    await act(async () => finish(initialSuccess ? { ok: true, data: saved } : { ok: false, error: 'first snapshot offline' }));
    await waitFor(() => expect(view.result.current.error).toBe('snapshot offline'));
    await act(async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); });
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(view.result.current.loading).toBe(false);
  });
  it('discards a queued control read after switching workspace and run', async () => {
    let finish: (value: unknown) => void = () => { };
    mocks.get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    mocks.get.mockResolvedValue({ ok: true, data: { ...saved, workspace: '/new', session_id: 'r2', run_id: 'r2', last_seq: 9 } });
    const view = renderHook(({ workspace, sessionId }) => useEngineeringRun({ workspace, sessionId, enabled: true }), { initialProps: { workspace: '/work', sessionId: 'r1' } });
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    act(() => receive({ type: 'RESYNC_REQUIRED', protocol: 'runtime.v2' }));
    view.rerender({ workspace: '/new', sessionId: 'r2' });
    await waitFor(() => expect(view.result.current.projection.snapshot?.session_id).toBe('r2'));
    const callsBeforeOldResponse = mocks.get.mock.calls.map((call) => [...call]);
    await act(async () => finish({ ok: true, data: saved }));
    expect(mocks.get.mock.calls).toEqual(callsBeforeOldResponse);
    expect(mocks.get).toHaveBeenLastCalledWith('r2', '/new');
    expect(view.result.current.projection.lastSeq).toBe(9);
  });
  it('does not start a queued control read after unmounting', async () => {
    let finish: (value: unknown) => void = () => { };
    mocks.get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled: true }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    act(() => receive({ type: 'RESYNC_REQUIRED', protocol: 'runtime.v2' }));
    view.unmount();
    await act(async () => finish({ ok: true, data: saved }));
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });
  it('does not query again when an in-flight snapshot already covers a SQL marker', async () => {
    let finish: (value: unknown) => void = () => { };
    mocks.get.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled: true }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    act(() => receive(message(3, 'verification_completed', { snapshot_required: true, checks_count: 2000, required_checks_count: null })));
    await act(async () => finish({ ok: true, data: { ...saved, status: 'failed', verification: 'failed', last_seq: 3 } }));
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(view.result.current.projection.snapshotRequiredSeq).toBeNull();
    expect(view.result.current.projection.snapshot?.verification).toBe('failed');
  });
  it('uses one same-run GET for a deduplicated oversized marker and replays after an older in-flight snapshot', async () => {
    let initial: (value: unknown) => void = () => { };
    mocks.get.mockReturnValueOnce(new Promise((resolve) => { initial = resolve; }));
    mocks.get.mockResolvedValue({ ok: true, data: { ...saved, status: 'failed', verification: 'failed', last_seq: 3 } });
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled: true }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    act(() => { receive(message(3, 'run_updated', { snapshot_required: true, payload_omitted: true, checks_count: 2000 })); receive(message(3, 'run_updated', { snapshot_required: true, payload_omitted: true, checks_count: 2000 })); });
    await act(async () => initial({ ok: true, data: saved }));
    await waitFor(() => expect(view.result.current.projection.snapshot?.status).toBe('failed'));
    expect(mocks.get).toHaveBeenCalledTimes(2); expect(mocks.get).toHaveBeenLastCalledWith('r1', '/work');
    expect(view.result.current.projection.snapshotRequiredSeq).toBeNull();
  });
  it('retains the original discussion goal and ordinary advice for an explicit follow-up execution', async () => {
    mocks.discuss.mockResolvedValue({ ok: true, data: { response: '先补充CLI参数测试，再实现。' } });
    mocks.start.mockResolvedValue({ ok: true, data: saved });
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', enabled: true }));
    await act(async () => { await view.result.current.discuss('pm', '实现一个支持取消的CLI入口'); });
    await act(async () => { await view.result.current.start({ instruction: '按上面方案做', role: 'director' }); });
    expect(mocks.start.mock.calls[0][0].instruction).toContain('实现一个支持取消的CLI入口');
    expect(mocks.start.mock.calls[0][0].instruction).toContain('先补充CLI参数测试，再实现。');
    expect(mocks.start.mock.calls[0][0].instruction).toContain('按上面方案做');
    expect(mocks.start.mock.calls[0][0]).not.toHaveProperty('scope');
  });
  it('keeps pure discussion metadata without creating or subscribing to an engineering run', async () => {
    mocks.discuss.mockResolvedValue({ ok: true, data: { response: '先核对需求。', metadata: { context_snapshot_ref: 'a'.repeat(24), native_request_observation: { snapshot_status: 'available' } } } });
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', enabled: true }));
    await act(async () => { await view.result.current.discuss('pm', '讨论计划'); });
    expect(view.result.current.discussion).toBe('先核对需求。');
    expect(view.result.current.discussionMetadata?.context_snapshot_ref).toBe('a'.repeat(24));
    expect(mocks.start).not.toHaveBeenCalled(); expect(mocks.get).not.toHaveBeenCalled(); expect(mocks.subscribe).not.toHaveBeenCalled();
  });
  it('does nothing when disabled and uses one scoped shared subscription with cleanup', async () => {
    const view = renderHook(({ enabled }) => useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled }), { initialProps: { enabled: false } });
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.subscribe).not.toHaveBeenCalled();
    view.rerender({ enabled: true });
    await waitFor(() => expect(view.result.current.projection.snapshot?.run_id).toBe('r1'));
    expect(mocks.subscribe).toHaveBeenCalledWith([{ channel: 'event.engineering', tailLines: 0 }]);
    const unsubscribe = mocks.subscribe.mock.results[0].value;
    view.unmount();
    expect(unsubscribe).toHaveBeenCalledOnce();
  });
  it('buffers WS facts until the initial snapshot watermark, rejects foreign events, and keeps verification honest', async () => {
    let complete: (value: unknown) => void = () => { };
    mocks.get.mockReturnValue(new Promise((resolve) => { complete = resolve; }));
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled: true }));
    act(() => { receive(message(2, 'role_finished', { status: 'completed' })); receive(message(3, 'run_updated', { status: 'completed', text: 'passed', verification: 'not_run' }, '/other')); });
    await act(async () => complete({ ok: true, data: saved }));
    expect(view.result.current.projection.snapshot?.roles.director).toBe('completed');
    expect(view.result.current.projection.snapshot?.verification).toBe('not_run');
    expect(view.result.current.projection.lastSeq).toBe(2);
  });
  it('refreshes once per reconnect or RESYNC_REQUIRED, never via a timer', async () => {
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled: true }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    connected = false;
    view.rerender();
    connected = true;
    view.rerender();
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    act(() => receive({ type: 'RESYNC_REQUIRED', protocol: 'runtime.v2', cursor: 999999 }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(3));
    expect(view.result.current.projection.lastSeq).toBe(1);
  });
  it('cancel and resume keep the exact run resource identity', async () => {
    const view = renderHook(() => useEngineeringRun({ workspace: '/work', sessionId: 'r1', enabled: true }));
    await waitFor(() => expect(view.result.current.projection.snapshot).not.toBeNull());
    await act(async () => { await view.result.current.cancel(); });
    expect(mocks.cancel).toHaveBeenCalledWith('r1', '/work');
    await act(async () => { await view.result.current.resume(); });
    expect(mocks.resume).toHaveBeenCalledWith('r1', '/work');
    expect(view.result.current.projection.snapshot?.run_id).toBe('r1');
  });
});
