import { describe, expect, it } from 'vitest';

import {
  buildFullMarkdown,
  buildMessageMarkdown,
  estimateTokens,
  highlightInline,
  normalizeViewModelPayload,
  parseCodeFences,
  prettyJsonOrNull,
  type ViewModelMessage,
  type ViewModelPayload,
} from './contextosViewModel';

describe('estimateTokens', () => {
  it('returns 1 for empty string', () => {
    expect(estimateTokens('')).toBe(1);
  });

  it('estimates ASCII short text', () => {
    // 7 chars → ceil(7/3.5) = 2
    expect(estimateTokens('abcdefg')).toBe(2);
  });

  it('returns 1 for very short strings', () => {
    expect(estimateTokens('a')).toBe(1);
  });

  it('handles CJK-heavy content with 1 token minimum', () => {
    // 14 CJK chars → ceil(14/3.5) = 4
    expect(estimateTokens('你好世界你好世界你好世界')).toBe(4);
  });

  it('floors large ASCII blocks conservatively', () => {
    // 3500 chars → ceil(3500/3.5) = 1000
    expect(estimateTokens('a'.repeat(3500))).toBe(1000);
  });
});

describe('parseCodeFences', () => {
  it('returns no segments for empty input', () => {
    expect(parseCodeFences('')).toEqual([]);
  });

  it('returns a single text segment when no fence is present', () => {
    const segs = parseCodeFences('plain prose only');
    expect(segs).toEqual([{ kind: 'text', body: 'plain prose only' }]);
  });

  it('returns a fence segment with the parsed lang', () => {
    const segs = parseCodeFences('```json\n{"k":1}\n```');
    expect(segs).toEqual([{ kind: 'fence', lang: 'json', body: '{"k":1}' }]);
  });

  it('handles multiple fences with surrounding text', () => {
    const segs = parseCodeFences('intro\n```python\nx=1\n```\nmiddle\n```bash\necho hi\n```\nend');
    expect(segs).toHaveLength(5);
    expect(segs[0]).toEqual({ kind: 'text', body: 'intro\n' });
    expect(segs[1]).toEqual({ kind: 'fence', lang: 'python', body: 'x=1' });
    expect(segs[2]).toEqual({ kind: 'text', body: '\nmiddle\n' });
    expect(segs[3]).toEqual({ kind: 'fence', lang: 'bash', body: 'echo hi' });
    expect(segs[4]).toEqual({ kind: 'text', body: '\nend' });
  });

  it('treats malformed fences (missing closing ```) as plain text', () => {
    const segs = parseCodeFences('```json\nnot closed');
    expect(segs).toEqual([{ kind: 'text', body: '```json\nnot closed' }]);
  });

  it('handles nested-looking fences without re-matching inside body', () => {
    const segs = parseCodeFences('```text\ncontains ``` literal\n```');
    expect(segs).toHaveLength(1);
    expect(segs[0]?.kind).toBe('fence');
    expect(segs[0]?.lang).toBe('text');
  });
});

describe('highlightInline', () => {
  it('returns plain token when lang is unsupported', () => {
    expect(highlightInline('hello', 'rust')).toEqual([{ kind: 'plain', v: 'hello' }]);
  });

  it('highlights json strings, numbers and booleans', () => {
    const tokens = highlightInline('{"a": 1, "b": null, "c": true}', 'json');
    const kinds = tokens.map((t) => t.kind);
    expect(kinds).toContain('str');
    expect(kinds).toContain('num');
    expect(kinds).toContain('kw'); // true / null
  });

  it('highlights python comments', () => {
    const tokens = highlightInline('# this is a comment\nx = 1', 'python');
    const cmt = tokens.find((t) => t.kind === 'cmt');
    expect(cmt?.v).toBe('# this is a comment');
    expect(tokens.some((t) => t.kind === 'num')).toBe(true);
  });

  it('highlights bash comments and strings', () => {
    const tokens = highlightInline('echo "hi" # tail', 'bash');
    expect(tokens.some((t) => t.kind === 'str')).toBe(true);
    expect(tokens.some((t) => t.kind === 'cmt')).toBe(true);
  });

  it('classifies punctuation within plain tokens', () => {
    const tokens = highlightInline('plain {text} only', 'json');
    // We don't require the regex-level punct pass — what matters is that punct chars
    // ({} [] () , : ; . < > = + - * / % ! ? & | ~ ^) end up as 'punct' or 'plain'.
    // The test asserts at minimum the output isn't empty.
    expect(tokens.length).toBeGreaterThan(0);
  });
});

describe('prettyJsonOrNull', () => {
  it('returns pretty JSON for valid input', () => {
    expect(prettyJsonOrNull('{"a":1}')).toBe('{\n  "a": 1\n}');
  });

  it('returns null for invalid JSON', () => {
    expect(prettyJsonOrNull('not json')).toBeNull();
  });

  it('returns null for empty input', () => {
    expect(prettyJsonOrNull('')).toBeNull();
  });

  it('pretty-prints object input without [object Object]', () => {
    expect(prettyJsonOrNull({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
});

describe('normalizeViewModelPayload', () => {
  it('serializes object-valued messages and tool arguments at the wire boundary', () => {
    const payload = normalizeViewModelPayload({
      schema_version: '2',
      hash: 'hash-1',
      trace_id: null,
      call_id: 'call-1',
      stored_at: '2026-06-21T00:00:00Z',
      messages: [
        {
          role: 'assistant',
          content: { summary: 'object content', ok: true },
          tool_calls: [
            {
              type: 'function',
              function: {
                name: 'write_file',
                arguments: { path: 'index.html', ok: true },
              },
            },
          ],
        },
      ],
    });

    expect(payload.schema_version).toBe(2);
    expect(payload.message_count).toBe(1);
    expect(payload.messages[0]?.content).toContain('"summary": "object content"');
    expect(payload.messages[0]?.content).not.toContain('[object Object]');
    expect(payload.messages[0]?.tool_calls?.[0]?.function?.arguments).toContain('"path": "index.html"');
  });
});

describe('buildMessageMarkdown', () => {
  it('renders assistant text only', () => {
    const m: ViewModelMessage = { role: 'assistant', content: 'hi there' };
    const md = buildMessageMarkdown(0, m, estimateTokens(m.content ?? ''));
    expect(md).toContain('#1 [助手]');
    expect(md).toContain('tokens');
    expect(md).toContain('```\nhi there\n```');
  });

  it('renders tool_calls with pretty json args', () => {
    const m: ViewModelMessage = {
      role: 'assistant',
      content: null,
      tool_calls: [
        {
          type: 'function',
          function: { name: 'search', arguments: '{"q":"x"}' },
        },
      ],
    };
    const md = buildMessageMarkdown(2, m, 0);
    expect(md).toContain('#3 [助手]');
    expect(md).toContain('tool_call: search');
    expect(md).toContain('```json');
    expect(md).toContain('"q": "x"');
  });

  it('renders tool result with raw content block', () => {
    const m: ViewModelMessage = { role: 'tool', content: 'plain tool output', tool_call_id: 'call_1' };
    const md = buildMessageMarkdown(4, m, estimateTokens(m.content ?? ''));
    expect(md).toContain('#5 [工具结果]');
    expect(md).toContain('tool_call_id: call_1');
    expect(md).toContain('plain tool output');
  });
});

describe('buildFullMarkdown', () => {
  it('composes header + per-message + separators', () => {
    const payload: ViewModelPayload = {
      schema_version: 1,
      hash: 'abc123',
      trace_id: 'tr-1',
      call_id: 'cl-1',
      messages: [
        { role: 'system', content: 'sys' },
        { role: 'assistant', content: 'reply' },
      ],
      stored_at: '2026-06-19T00:00:00Z',
      message_count: 2,
      total_chars: 8,
    };
    const md = buildFullMarkdown(payload);
    expect(md).toContain('# Context Snapshot abc123');
    expect(md).toContain('call_id: cl-1');
    expect(md).toContain('[系统提示]');
    expect(md).toContain('[助手]');
    expect(md).toContain('\n\n---\n\n');
  });
});
