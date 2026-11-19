import { describe, expect, it } from 'vitest';
import type { EngineeringEvent } from '@/services/api.types';
import {
  contextRefOf, eventKindLabel, eventTone, formatClock, formatElapsed, loopInterventions,
  outcomeTone, qaTone, receiptSummary, roleStatusTone, runShortId, runStatusTone,
  toolGlyph, verificationTone, writesSinceVerification,
} from './format';

function event(seq: number, kind: string, payload: Record<string, unknown>, created_at?: number): EngineeringEvent {
  return { session_id: 's', workspace: '/w', seq, role: 'director', kind, payload, created_at };
}

describe('console format helpers', () => {
  it('maps run statuses to tones', () => {
    expect(runStatusTone('running')).toBe('run');
    expect(runStatusTone('completed')).toBe('ok');
    expect(runStatusTone('failed')).toBe('bad');
    expect(runStatusTone('cancelled')).toBe('warn');
    expect(runStatusTone(undefined)).toBe('idle');
  });
  it('maps role statuses to tones', () => {
    expect(roleStatusTone('running')).toBe('run');
    expect(roleStatusTone('not_called')).toBe('idle');
    expect(roleStatusTone('failed')).toBe('bad');
  });
  it('maps verdict tones', () => {
    expect(verificationTone('passed')).toBe('ok');
    expect(qaTone('approved')).toBe('ok');
    expect(outcomeTone('accepted')).toBe('gold');
    expect(outcomeTone('rejected')).toBe('bad');
  });
  it('formats elapsed mm:ss', () => {
    expect(formatElapsed(1_000, 1_000 + 2 * 60_000 + 48_000)).toBe('02:48');
    expect(formatElapsed(undefined, 5)).toBe('—');
  });
  it('formats clock HH:MM:SS', () => {
    const stamp = new Date(2026, 10, 19, 14, 32, 1).getTime();
    expect(formatClock(stamp)).toMatch(/^14:32:01$/);
    expect(formatClock(undefined)).toBe('—');
  });
  it('derives run short ids', () => {
    expect(runShortId('eng-run-0042')).toBe('RUN-0042');
    expect(runShortId('')).toBe('RUN-····');
  });
  it('accepts only available 24-hex context refs', () => {
    const good = event(1, 'tool_observed', { snapshot_status: 'available', context_snapshot_ref: 'a7f3c2e9b4016d85ef90a214' });
    expect(contextRefOf(good)).toBe('a7f3c2e9b4016d85ef90a214');
    expect(contextRefOf(event(2, 'tool_observed', { context_snapshot_ref: 'a7f3c2e9b4016d85ef90a214' }))).toBeNull();
    expect(contextRefOf(event(3, 'tool_observed', { snapshot_status: 'available', context_snapshot_ref: 'zzz' }))).toBeNull();
  });
  it('classifies event tones', () => {
    expect(eventTone(event(1, 'tool_observed', { ok: true }))).toBe('ok');
    expect(eventTone(event(2, 'tool_observed', { ok: false }))).toBe('bad');
    expect(eventTone(event(3, 'run_failed', {}))).toBe('bad');
    expect(eventTone(event(4, 'role_started', {}))).toBe('info');
  });
  it('labels event kinds', () => {
    expect(eventKindLabel('tool_started')).toBe('工具调用中');
    expect(eventKindLabel('verification_completed')).toBe('验证结束');
    expect(eventKindLabel('custom_kind')).toBe('custom_kind');
  });
  it('buckets tool glyphs', () => {
    expect(toolGlyph('repo_tree')).toBe('folder');
    expect(toolGlyph('write_file')).toBe('file');
    expect(toolGlyph('execute_command')).toBe('terminal');
    expect(toolGlyph('browser_render')).toBe('browser');
    expect(toolGlyph('read_file')).toBe('search');
    expect(toolGlyph('')).toBe('generic');
  });
  it('summarizes receipts', () => {
    expect(receiptSummary(event(1, 'tool_observed', { tool: 'write_file', path: 'src/main.ts' }))).toBe('write_file · src/main.ts');
    expect(receiptSummary(event(2, 'tool_observed', { tool: 'execute_command', argv: ['npm', 'test'] }))).toBe('execute_command · npm test');
    expect(receiptSummary(event(3, 'role_started', {}))).toBe('role_started');
  });
  it('counts writes since the last verification boundary', () => {
    const events = [
      event(1, 'tool_started', { tool: 'write_file' }),
      event(2, 'verification_completed', {}),
      event(3, 'tool_started', { tool: 'write_file' }),
      event(4, 'tool_started', { tool: 'execute_command' }),
      event(5, 'tool_started', { tool: 'repo_tree' }),
    ];
    expect(writesSinceVerification(events, 5)).toEqual({ count: 2, threshold: 5 });
  });
  it('counts loop interventions', () => {
    const events = [event(1, 'stage_gate_loop_enforced', {}), event(2, 'tool_observed', { loop_enforced: true }), event(3, 'tool_observed', {})];
    expect(loopInterventions(events)).toBe(2);
  });
});
