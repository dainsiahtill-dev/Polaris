import { describe, expect, it } from 'vitest';
import { engineeringContextRecords } from './contextOSTelemetry';
import type { EngineeringEvent } from '@/services/api.types';
function observed(seq: number, payload: Record<string, unknown>): EngineeringEvent { return { session_id: 'r1', workspace: '/work', seq, role: 'director', kind: 'llm_request_observed', payload }; }
describe('engineering native context observations', () => {
  it('pairs prepared and terminal captures without counting them as physical sends', () => {
    const rows = engineeringContextRecords([observed(1, { capture_id: 'capture-1', phase: 'prepared', status: 'prepared', context_snapshot_ref: 'a'.repeat(24), snapshot_status: 'available', audit: { final_request_token_estimate: 1200 } }), observed(2, { capture_id: 'capture-1', phase: 'terminal', status: 'completed', context_snapshot_ref: 'a'.repeat(24), snapshot_status: 'available', audit: { final_request_token_estimate: 1200 }, usage: { total_tokens: 50 }, usage_status: 'reported', physical_send_count: null })]);
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe('completed');
    expect(rows[0].finalRequestTokens).toBe(1200);
    expect(rows[0].usageTokens).toBe(50);
    expect(rows[0].contextSnapshotRef).toBe('a'.repeat(24));
  });
  it('never makes invalid or unavailable snapshot refs clickable', () => {
    const rows = engineeringContextRecords([observed(1, { capture_id: 'one', context_snapshot_ref: 'request-hash', snapshot_status: 'available' }), observed(2, { capture_id: 'two', context_snapshot_ref: 'b'.repeat(24), snapshot_status: 'unavailable' })]);
    expect(rows.every((row) => row.contextSnapshotRef === null)).toBe(true);
    expect(rows.every((row) => row.usageStatus === 'unknown')).toBe(true);
  });
});
