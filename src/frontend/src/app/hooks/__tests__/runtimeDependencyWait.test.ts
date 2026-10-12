import { describe, expect, it } from 'vitest';
import { parseProcessStreamLine } from '../_runtimeParsers';

function taskEvent(overrides: Record<string, unknown> = {}, container: string = 'payload') {
  return JSON.stringify({
    stream: 'task_runtime.execution', source: 'runtime.task_runtime', event_type: 'factory_run_bound',
    summary: 'Director task 2 factory_run_bound',
    [container]: {
      task_id: '2', status: 'blocked', last_error: '',
      task_row_snapshot: { id: 2, status: 'blocked', blocked_by: [1], last_error: '' },
      details: { row_write_receipt: { operation: 'replace', before_hash: 'a', after_hash: 'b' } },
      ...overrides,
    },
  });
}

describe('TaskRuntime dependency wait projection', () => {
  it('keeps the real compact runtime.v2 task fact waiting when dependency rows are omitted', () => {
    const entry = parseProcessStreamLine('event.factory', JSON.stringify({
      fact_stream: 'task_runtime.execution', type: 'task_runtime_execution', task_id: '2',
      status: 'blocked', execution_state: 'blocked', last_error: '',
      task_row_snapshot_projection: { status: 'durable_fact_only' },
      message: 'Director task 2 factory_run_bound',
    }));
    expect(entry?.level).toBe('info');
  });

  it('preserves an explicit failure on a compact task fact', () => {
    const entry = parseProcessStreamLine('event.factory', JSON.stringify({
      fact_stream: 'task_runtime.execution', type: 'task_runtime_execution', task_id: '2',
      status: 'blocked', last_error: 'receipt hash mismatch', message: 'Director task 2 blocked',
    }));
    expect(entry?.level).toBe('error');
  });

  it.each(['payload', 'data'])('keeps an admitted dependent task waiting through %s without reporting execution failure', (container) => {
    const entry = parseProcessStreamLine('event.factory', taskEvent({}, container));
    expect(entry?.level).toBe('info');
    expect(entry?.message).toBe('Director task 2 factory_run_bound');
  });

  it.each(['payload', 'data'])('preserves a blocked task with a real error through %s as a failure', (container) => {
    const entry = parseProcessStreamLine('event.factory', taskEvent({ last_error: 'handoff provenance rejected' }, container));
    expect(entry?.level).toBe('error');
  });

  it('preserves failed verifier evidence even when dependencies remain', () => {
    const entry = parseProcessStreamLine('event.factory', taskEvent({ result: { ok: false, exit_code: 1 } }));
    expect(entry?.level).toBe('error');
  });

  it('does not turn a blocked Factory stage into a dependency wait', () => {
    const entry = parseProcessStreamLine('event.factory', JSON.stringify({
      event: 'stage_completed', data: { result: { status: 'blocked' }, blocked_by: [1] },
    }));
    expect(entry?.level).toBe('error');
  });
});
