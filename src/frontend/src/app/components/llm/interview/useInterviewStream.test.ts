import { describe, expect, it } from 'vitest';

import { createContentTagParser, type StreamingTagEvent } from './useInterviewStream';

describe('createContentTagParser', () => {
  it('parses answer tags split across content chunks', () => {
    const parser = createContentTagParser();
    const events: StreamingTagEvent[] = [];

    ['<', 'answer', '>风险', '识别</', 'answer>'].forEach((chunk) => {
      parser.consume(chunk, '2026-06-02T00:00:00.000Z', (event) => events.push(event));
    });

    expect(events.map((event) => event.type)).toEqual([
      'answer_start',
      'answer_chunk',
      'answer_chunk',
      'answer_end',
    ]);
    expect(events.filter((event) => event.type === 'answer_chunk').map((event) => event.data.content).join('')).toBe(
      '风险识别'
    );
  });

  it('parses thinking aliases without leaking partial closing tags', () => {
    const parser = createContentTagParser();
    const events: StreamingTagEvent[] = [];

    ['<think>', 'step 1</thi', 'nk>'].forEach((chunk) => {
      parser.consume(chunk, '2026-06-02T00:00:00.000Z', (event) => events.push(event));
    });

    expect(events.map((event) => event.type)).toEqual(['thinking_start', 'thinking_chunk', 'thinking_end']);
    expect(events[1]?.data.content).toBe('step 1');
  });
});
