import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ContextOSWorkspace } from '../ContextOSWorkspace';
import { buildContextOSModel } from '../contextOSData';
import { buildTelemetryFromStream } from '../contextOSTelemetry';
import type { LogEntry } from '@/types/log';
import type { ContextOSWorkspaceProps } from '../ContextOSWorkspace';

vi.mock('@/runtime/transport', () => ({ useRuntimeTransport: () => ({ subscribeChannels: () => () => {}, registerMessageHandler: () => () => {} }) }));
const state: ContextOSWorkspaceProps['llmRuntimeState'] = { state: 'READY', blockedRoles: [], requiredRoles: ['pm', 'director'], lastUpdated: null };
function props(logs: LogEntry[]): ContextOSWorkspaceProps {
  return { workspace: '/readonly/r21', onBackToMain: vi.fn(), live: true, usageStats: null, currentPhase: 'idle', pmRunning: false, directorRunning: false,
    llmRuntimeState: state, dialogueEvents: [], executionLogs: [], llmStreamEvents: logs, processStreamEvents: [], snapshot: null };
}
function log(id: string, meta: Record<string, unknown>): LogEntry {
  return { id, timestamp: '2026-10-12T10:33:40Z', level: 'success', source: 'Director', message: id, meta: { channel: 'llm', role: 'director', ...meta } };
}
const audit = { final_request_token_estimate: 28169 };
const history = [log('end', { streamEvent: 'llm_call_end', callId: 'physical', final_request_context_audit: audit, promptTokens: 27000, completionTokens: 1000, totalTokens: 28000 })];

describe('current context versus historical observation', () => {
  it('does not assemble or activate an idle pipeline from retained history', () => {
    render(<ContextOSWorkspace {...props(history)} />);
    expect(screen.queryByText('装配中')).toBeNull();
    expect(screen.getByTestId('contextos-stage-request')).toHaveAttribute('data-state', 'idle');
    expect(screen.getByTestId('contextos-stage-projection')).toHaveAttribute('data-state', 'idle');
    expect(screen.getByTestId('contextos-telemetry-freshness').textContent).toContain('WS LIVE');
  });
  it('shows the latest final request as primary and keeps cumulative provider usage separate', () => {
    render(<ContextOSWorkspace {...props(history)} />);
    expect(screen.getByTestId('contextos-context-budget-primary').textContent).toContain('28,169');
    expect(screen.getByTestId('contextos-context-budget-primary').textContent).toContain('最新最终请求');
    expect(screen.getByTestId('contextos-cumulative-usage').textContent).toContain('28,000');
    expect(screen.getByTestId('contextos-window-occupancy-source').textContent).toContain('最新最终请求');
  });
  it('does not call an estimate-only request provider usage', () => {
    render(<ContextOSWorkspace {...props([log('audit', { streamEvent: 'final_request_context_audit', final_request_context_audit: audit })])} />);
    expect(screen.queryByText('provider usage · 实时')).toBeNull();
    expect(screen.getByTestId('contextos-context-budget-primary').textContent).toContain('28,169');
    expect(screen.queryByTestId('contextos-cumulative-usage')).toBeNull();
  });
  it('shows an actual unresolved physical start as active and clears it on its terminal event', () => {
    const start = log('start', { streamEvent: 'llm_call_start', callId: 'physical' });
    const m = buildContextOSModel({ ...props([start]), telemetry: buildTelemetryFromStream([start], [], []) });
    expect(m.running).toBe(true);
    const ended = buildContextOSModel({ ...props([start, ...history]), telemetry: buildTelemetryFromStream([start, ...history], [], []) });
    expect(ended.running).toBe(false);
  });
  it('shows a terminal Chief Engineer as idle and one call row per mirrored physical failure', () => {
    const logs = [
      { ...log('failed-application', { streamEvent: 'llm_error', role: 'chief_engineer', callId: 'first', context_snapshot_ref: 'a'.repeat(24) }), level: 'error' as const },
      log('failed-journal', { streamEvent: 'llm_failed', role: 'chief_engineer', callId: 'first', context_snapshot_ref: 'a'.repeat(24) }),
      log('ce-completed', { streamEvent: 'llm_completed', role: 'chief_engineer', callId: 'second', context_snapshot_ref: 'b'.repeat(24), promptTokens: 21620, completionTokens: 16099, totalTokens: 37719 }),
    ];
    const telemetry = buildTelemetryFromStream(logs, [], []);
    const model = buildContextOSModel({ ...props(logs), telemetry });
    expect(model.calls).toBe(2);
    expect(model.roles.find(role => role.id === 'chief_engineer')?.state).toBe('idle');
    expect(telemetry.events.filter(event => event.callId === 'first')).toHaveLength(2);
    render(<ContextOSWorkspace {...props(logs)} />);
    fireEvent.click(screen.getByTestId('contextos-roletab-chief_engineer'));
    expect(screen.getByTestId('contextos-recent-role-calls').children).toHaveLength(2);
  });
  it('keeps a distinct captured retry in flight when an older request terminates late', () => {
    const logs = [
      { ...log('older-start', { streamEvent: 'llm_call_start', callId: 'logical', context_snapshot_ref: 'a'.repeat(24) }), timestamp: '2026-10-12T10:33:38Z' },
      { ...log('retry-start', { streamEvent: 'llm_call_start', callId: 'logical', context_snapshot_ref: 'b'.repeat(24) }), timestamp: '2026-10-12T10:33:39Z' },
      log('older-end', { streamEvent: 'llm_call_end', callId: 'logical', context_snapshot_ref: 'a'.repeat(24) }),
    ];
    const build = (events: LogEntry[]) => buildContextOSModel({ ...props(events), telemetry: buildTelemetryFromStream(events, [], []) });
    expect(build(logs).running).toBe(true);
    expect(build([...logs, { ...log('retry-end', { streamEvent: 'llm_call_end', callId: 'logical', context_snapshot_ref: 'b'.repeat(24) }), timestamp: '2026-10-12T10:33:41Z' }]).running).toBe(false);
  });
  it('keeps the selected role decision banner count scoped to that role', () => {
    const logs = [
      log('pm', { streamEvent: 'llm_completed', role: 'pm', callId: 'pm', promptTokens: 100, totalTokens: 100 }),
      log('ce1', { streamEvent: 'llm_error', role: 'chief_engineer', callId: 'ce1' }),
      log('ce2', { streamEvent: 'llm_completed', role: 'chief_engineer', callId: 'ce2', promptTokens: 100, totalTokens: 100 }),
    ];
    render(<ContextOSWorkspace {...props(logs)} />);
    expect(screen.getByTestId('contextos-telemetry-source').textContent).toContain('3 调用');
    fireEvent.click(screen.getByTestId('contextos-roletab-chief_engineer'));
    expect(screen.getByTestId('contextos-telemetry-source').textContent).toContain('2 调用');
  });
  it('filters canonical role hints before the decision limit and keeps the original Factory actor', () => {
    const failure: LogEntry = { id: 'real-ce-gate-failure', timestamp: '2026-10-12T10:37:53Z', level: 'error', source: 'Factory',
      message: 'Chief Engineer portfolio review generated 0/9 blueprints; invalid_project_completion_contract',
      meta: { channel: 'system', streamEvent: 'chief_engineer_review_failed', role: 'chief_engineer' } };
    const unrelated = Array.from({ length: 13 }, (_, index): LogEntry => ({ id: `director-${index}`, timestamp: `2026-10-12T10:38:${String(index).padStart(2, '0')}Z`,
      level: 'info', source: 'Director', message: `Director observation ${index}`, meta: { channel: 'system', role: 'director' } }));
    render(<ContextOSWorkspace {...props([])} executionLogs={[failure, ...unrelated]} />);
    fireEvent.click(screen.getByTestId('contextos-roletab-chief_engineer'));
    expect(screen.queryByTestId('contextos-decision-empty')).toBeNull();
    const decision = screen.getByTitle(/^Factory ·/).parentElement!;
    expect(decision.textContent).toContain('0/9 blueprints');
    expect(decision.textContent).toContain('Factory');
    expect(decision.textContent).not.toContain('Director observation');
    expect(decision.textContent).not.toContain('快照');
  });
});
