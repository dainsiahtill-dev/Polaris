import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ContextOSWorkspace } from './ContextOSWorkspace';
import { hydrateEngineeringRun, newEngineeringProjection } from '@/runtime/engineeringRunProjection';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import type { EngineeringRunSnapshot } from '@/services/api.types';
const fetch = vi.hoisted(() => vi.fn(async (url: string) => url.includes('/final-request') ? { ok: false, status: 404, json: async () => ({ detail: { code: 'CONTEXT_NOT_FOUND', message: 'unavailable' } }) } : { ok: true, status: 200, json: async () => ({ messages: [{ role: 'system', content: 'You are Director.' }], token_estimate: 1200 }) }));
vi.mock('@/api', async (original) => ({ ...await original<typeof import('@/api')>(), apiFetch: fetch }));
const saved: EngineeringRunSnapshot = {
  session_id: 'r1', run_id: 'r1', engine_version: 'engineering_v2', workspace: '/work', workflow: 'direct', role: 'director', status: 'running', active_roles: ['director'], roles: { pm: 'not_called', chief_engineer: 'not_called', director: 'running', qa: 'not_called' }, text: '', verification: 'unknown', checks: [], required_checks: [], permission_profile_id: null, resource_url: '/v2/engineering/runs/r1', last_seq: 2, events: [
    { session_id: 'r1', workspace: '/work', seq: 1, role: 'director', kind: 'llm_request_observed', payload: { capture_id: 'old', phase: 'terminal', status: 'completed', snapshot_status: 'available', context_snapshot_ref: 'a'.repeat(24), audit: { final_request_token_estimate: 1200 }, usage: { total_tokens: 50 }, usage_status: 'reported' } },
    { session_id: 'r1', workspace: '/work', seq: 2, role: 'director', kind: 'llm_request_observed', payload: { capture_id: 'new', phase: 'terminal', status: 'failed', snapshot_status: 'unavailable', context_snapshot_ref: null, audit: {}, usage: null, usage_status: 'unknown' } },
  ]
};
function binding(): EngineeringRunBinding { return { projection: hydrateEngineeringRun(newEngineeringProjection('/work', 'r1'), saved), sessionId: 'r1', loading: false, pending: null, error: null, discussion: '', discussionMetadata: null, connected: true, reconnecting: false, connectionError: null, start: vi.fn(), cancel: vi.fn(), resume: vi.fn(), refresh: vi.fn(), discuss: vi.fn() }; }
describe('engineering ContextOS binding', () => {
  it('keeps current unavailable separate from the historical readable capture and actual usage', async () => {
    render(<ContextOSWorkspace engineeringEnabled engineering={binding()} workspace="/work" onBackToMain={() => { }} live usageStats={null} currentPhase="legacy" pmRunning={false} directorRunning={false} llmRuntimeState={{ state: 'BLOCKED', blockedRoles: ['pm', 'chief_engineer'], requiredRoles: ['pm', 'chief_engineer'], lastUpdated: null }} dialogueEvents={[]} executionLogs={[]} llmStreamEvents={[]} processStreamEvents={[]} snapshot={null} />);
    expect(screen.getByTestId('engineering-current-context')).toHaveTextContent('不可用');
    expect(screen.getAllByRole('button', { name: '查看最终请求' })).toHaveLength(1);
    expect(screen.getByText(/最终请求 token：1200/)).toBeInTheDocument();
    expect(screen.getByText(/50 · Provider 报告/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看最终请求' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/v2/context/aaaaaaaaaaaaaaaaaaaaaaaa/final-request?workspace=%2Fwork'));
    expect(screen.getByRole('button', { name: /^最终请求/ })).toHaveAttribute('aria-pressed', 'true');
  });
});
