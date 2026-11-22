import { describe, expect, it } from 'vitest';
import type { EngineeringEvent } from '@/services/api.types';
import {
  COMPRESSION_DROP_UNIT_LABELS,
  compressionFromEvents,
  parseContextCompression,
  preparedRequestMessages,
} from './contextOSCompression';

describe('parseContextCompression', () => {
  it('parses the bounded-budget wording and quotes the host text verbatim', () => {
    const fact = parseContextCompression([
      { role: 'system', content: 'system prompt' },
      {
        role: 'user',
        content: '【上下文已压缩】为控制在有界上下文预算（约 32000 tokens）内，省略了 12 条较早的工具轮次；这些轮次的完整记录仍在工程运行日志中，可按证据引用取回。以下保留最近的关键轮次。',
      },
    ]);
    expect(fact).not.toBeNull();
    expect(fact?.droppedTurns).toBe(12);
    expect(fact?.budgetTokens).toBe(32000);
    expect(fact?.dropUnit).toBe('tool_turns');
    expect(fact?.text).toContain('约 32000 tokens');
    expect(fact?.text).toContain('省略了 12 条较早的工具轮次');
  });

  it('parses the model-window wording that the current runtime emits', () => {
    const fact = parseContextCompression([
      { role: 'user', content: '【上下文已压缩】为适配模型窗口，省略了 69 条较早的工具轮次；这些轮次的完整记录仍在工程运行日志中。以下保留最近的关键轮次。' },
    ]);
    expect(fact?.droppedTurns).toBe(69);
    expect(fact?.budgetTokens).toBeNull();
    expect(fact?.dropUnit).toBe('tool_turns');
  });

  it('distinguishes dropped conversation messages from tool turns', () => {
    const fact = parseContextCompression([
      { role: 'user', content: '【上下文已压缩】为控制在有界上下文预算（约 8,192 tokens）内，省略了 4 条较早的对话消息；以下保留最近的关键消息。' },
    ]);
    expect(fact?.droppedTurns).toBe(4);
    expect(fact?.budgetTokens).toBe(8192);
    expect(fact?.dropUnit).toBe('messages');
    expect(COMPRESSION_DROP_UNIT_LABELS[fact?.dropUnit ?? 'unknown']).toBe('对话消息');
  });

  it('keeps the last marker and ignores messages without one', () => {
    const fact = parseContextCompression([
      { role: 'user', content: '【上下文已压缩】省略了 3 条较早的工具轮次' },
      { role: 'assistant', content: '普通回复' },
      { role: 'user', content: '【上下文已压缩】省略了 7 条较早的工具轮次' },
    ]);
    expect(fact?.droppedTurns).toBe(7);
  });

  it('returns null when the host never compressed', () => {
    expect(parseContextCompression([{ role: 'user', content: '没有标记' }])).toBeNull();
    expect(parseContextCompression([])).toBeNull();
  });

  it('reads text blocks from structured message content', () => {
    const fact = parseContextCompression([
      { role: 'user', content: [{ type: 'text', text: '【上下文已压缩】省略了 5 条较早的轮次' }] },
    ]);
    expect(fact?.droppedTurns).toBe(5);
    expect(fact?.dropUnit).toBe('turns');
  });
});

describe('compressionFromEvents', () => {
  function event(seq: number, payload: Record<string, unknown>): EngineeringEvent {
    return { session_id: 'run', workspace: '/ws', seq, role: 'director', kind: 'request_prepared', payload, created_at: seq };
  }

  it('reads the prepared request messages from the journal', () => {
    const marker = '【上下文已压缩】为适配模型窗口，省略了 78 条较早的工具轮次；这些轮次的完整记录仍在工程运行日志中。以下保留最近的关键轮次。';
    const prepared = event(10, {
      turn_seq: 1204,
      request: { role: 'director', options: { messages: [{ role: 'user', content: marker }] } },
    });
    expect(preparedRequestMessages(prepared.payload)).toHaveLength(1);
    const fact = compressionFromEvents([prepared]);
    expect(fact?.droppedTurns).toBe(78);
    expect(fact?.sourceSeq).toBe(10);
    expect(fact?.text).toBe(marker);
  });

  it('skips request_prepared events without a marker and keeps the latest one', () => {
    const first = event(1, {
      request: { options: { messages: [{ role: 'user', content: '【上下文已压缩】省略了 2 条较早的工具轮次' }] } },
    });
    const clean = event(2, { request: { options: { messages: [{ role: 'user', content: '正常请求' }] } } });
    const last = event(3, {
      request: { options: { messages: [{ role: 'user', content: '【上下文已压缩】省略了 9 条较早的工具轮次' }] } },
    });
    const fact = compressionFromEvents([first, clean, last]);
    expect(fact?.droppedTurns).toBe(9);
    expect(fact?.sourceSeq).toBe(3);
  });

  it('returns null when no prepared request compressed', () => {
    expect(compressionFromEvents([event(1, { request: { options: { messages: [] } } })])).toBeNull();
  });
});
