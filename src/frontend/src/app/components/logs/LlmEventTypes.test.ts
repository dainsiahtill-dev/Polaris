import { describe, expect, it } from 'vitest';

import { parseLlmEventLine, parseLlmEventLines } from './LlmEventTypes';

describe('parseLlmEventLine', () => {
  it('parses legacy llm event payload', () => {
    const line = JSON.stringify({
      schema_version: 1,
      event_id: 'evt-1',
      run_id: 'run-1',
      iteration: 1,
      role: 'pm',
      ts: '2026-03-09T00:00:00Z',
      seq: 1,
      source: 'system',
      event: 'info',
      data: { message: 'ok' },
    });
    const parsed = parseLlmEventLine(line);
    expect(parsed).not.toBeNull();
    expect(parsed?.event).toBe('info');
    expect(parsed?.event_id).toBe('evt-1');
  });

  it('parses canonical stream event payload', () => {
    const line = JSON.stringify({
      schema_version: 2,
      event_id: 'evt-2',
      run_id: 'run-2',
      ts: '2026-03-09T00:00:01Z',
      seq: 2,
      actor: 'director',
      source: 'role_execution_kernel.stream',
      kind: 'action',
      refs: { iteration: 3 },
      raw: {
        stream_event: 'tool_call',
        tool: 'write_file',
        args: { path: 'README.md' },
      },
    });
    const parsed = parseLlmEventLine(line);
    expect(parsed).not.toBeNull();
    expect(parsed?.event).toBe('tool_call');
    expect(parsed?.role).toBe('director');
    expect(parsed?.iteration).toBe(3);
    expect(parsed?.data).toMatchObject({
      tool: 'write_file',
    });
  });

  it('returns null for invalid payload', () => {
    expect(parseLlmEventLine('not-json')).toBeNull();
    expect(parseLlmEventLine('{}')).toBeNull();
  });
});

describe('parseLlmEventLines', () => {
  it('filters invalid lines', () => {
    const lines = [
      'not-json',
      JSON.stringify({
        schema_version: 1,
        event_id: 'evt-3',
        run_id: 'run-3',
        iteration: 0,
        role: 'pm',
        ts: '2026-03-09T00:00:02Z',
        seq: 3,
        source: 'system',
        event: 'config',
        data: { tag: 'init', message: 'ready' },
      }),
    ];
    const parsed = parseLlmEventLines(lines);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].event).toBe('config');
  });
});
