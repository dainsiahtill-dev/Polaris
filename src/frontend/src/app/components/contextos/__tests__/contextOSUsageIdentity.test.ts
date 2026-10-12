import { describe, expect, it } from 'vitest';
import { buildTelemetryFromStream } from '../contextOSTelemetry';
import { buildContextOSModel } from '../contextOSData';
import type { LogEntry } from '@/types/log';

function event(id: string, meta: Record<string, unknown>): LogEntry {
  return { id, timestamp: '2026-10-12T10:33:40Z', level: 'success', source: 'Director', message: id,
    meta: { channel: 'llm', role: 'director', ...meta } };
}
const audit = { final_request_token_estimate: 28169, tool_schema_token_estimate: 1500 };
const usage = { promptTokens: 27000, completionTokens: 1000, totalTokens: 28000 };

describe('physical-call usage accounting', () => {
  it('retains request estimates without reporting provider usage', () => {
    const t = buildTelemetryFromStream([event('projection', { streamEvent: 'final_request_context_audit', callId: 'p1', final_request_context_audit: audit })], [], []);
    expect(t.totalTokens).toBe(0);
    expect(t.contextTokensLatest).toBe(28169);
    expect(t.totalCalls).toBe(0);
  });
  it('counts a projection and terminal usage once for the same physical call', () => {
    const t = buildTelemetryFromStream([
      event('projection', { streamEvent: 'final_request_context_audit', callId: 'p1', final_request_context_audit: audit }),
      event('end', { streamEvent: 'llm_call_end', callId: 'p1', final_request_context_audit: audit, ...usage }),
    ], [], []);
    expect(t.totalTokens).toBe(28000);
    expect(t.promptTokens).toBe(27000);
    expect(t.byRole.director.totalTokens).toBe(28000);
    expect(t.totalCalls).toBe(1);
  });
  it('deduplicates stream-final and terminal mirrors with one physical identity', () => {
    const t = buildTelemetryFromStream([
      event('stream-final', { streamEvent: 'complete', callId: 'p1', _internal_provider_usage: true, ...usage }),
      event('end', { streamEvent: 'llm_call_end', callId: 'p1', context_snapshot_ref: 'a'.repeat(24), final_request_context_audit: audit, ...usage }),
    ], [], []);
    expect(t.totalTokens).toBe(28000);
    expect(t.totalCalls).toBe(1);
  });
  it('sums genuinely different physical calls', () => {
    const t = buildTelemetryFromStream(['p1', 'p2'].map(callId => event(callId, { streamEvent: 'llm_call_end', callId, ...usage })), [], []);
    expect(t.totalTokens).toBe(56000);
    expect(t.totalCalls).toBe(2);
  });
  it('preserves retries with the same logical id and distinct captured request identities', () => {
    const t = buildTelemetryFromStream(['a', 'b'].map(ref => event(ref, { streamEvent: 'llm_call_end', callId: 'logical', context_snapshot_ref: ref.repeat(24), ...usage })), [], []);
    expect(t.totalTokens).toBe(56000);
    expect(t.totalCalls).toBe(2);
  });
  it('keeps separate legacy observations when physical identity is absent', () => {
    const t = buildTelemetryFromStream(['legacy1', 'legacy2'].map(id => event(id, { streamEvent: 'llm_call_end', ...usage })), [], []);
    expect(t.totalTokens).toBe(56000);
    expect(t.totalCalls).toBe(2);
  });
  it('uses deduplicated calls for a binding prompt-average fallback', () => {
    const logs = [
      event('stream-final', { streamEvent: 'complete', callId: 'p1', providerId: 'mini', _internal_provider_usage: true, promptTokens: 100, totalTokens: 100 }),
      event('end', { streamEvent: 'llm_call_end', callId: 'p1', providerId: 'mini', context_snapshot_ref: 'a'.repeat(24), promptTokens: 100, totalTokens: 100 }),
    ];
    const model = buildContextOSModel({ usageStats: null, dialogueEvents: [], executionLogs: [], snapshot: null,
      llmRuntimeState: { state: 'READY', requiredRoles: ['director'], blockedRoles: [], lastUpdated: null,
        roleDetails: { director: { providerId: 'mini', providerName: 'MiniMax', providerType: 'anthropic_compat', model: 'MiniMax-M3', maxContextTokens: 1000000, maxOutputTokens: 60000,
          bindings: [], ready: true, runtimeSupported: true, runtimeIssue: '', readinessIssue: '', readinessSource: 'role_index', testedProviderId: 'mini', testedModel: 'MiniMax-M3', testedTimestamp: null, timestamp: null } } },
      currentPhase: 'idle', pmRunning: false, directorRunning: false, telemetry: buildTelemetryFromStream(logs, [], []) });
    expect(model.roles.find(role => role.id === 'director')?.internalContext.bindingBudgets[0].windowOccupancyTokens).toBe(100);
  });
});
