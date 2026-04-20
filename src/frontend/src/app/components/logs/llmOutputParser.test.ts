import { describe, expect, it } from 'vitest';
import { extractFirstJsonValue, parseJsonLikeOutputWithMeta, unwrapJsonFence } from './llmOutputParser';

describe('llmOutputParser', () => {
  it('parses direct json object', () => {
    const parsed = parseJsonLikeOutputWithMeta('{"a":1,"b":["x","y"]}');
    expect(parsed.value).toEqual({ a: 1, b: ['x', 'y'] });
    expect(parsed.note).toBe('');
  });

  it('parses fenced json', () => {
    const parsed = parseJsonLikeOutputWithMeta('```json\n{"ok":true}\n```');
    expect(parsed.value).toEqual({ ok: true });
    expect(parsed.note).toBe('');
  });

  it('normalizes prefixed text with first json value', () => {
    const parsed = parseJsonLikeOutputWithMeta('prefix text {"k":1} trailing');
    expect(parsed.value).toEqual({ k: 1 });
    expect(parsed.note.startsWith('normalized_first_json_value')).toBe(true);
  });

  it('normalizes concatenated json objects and keeps first one', () => {
    const parsed = parseJsonLikeOutputWithMeta('{"a":1}{"b":2}');
    expect(parsed.value).toEqual({ a: 1 });
    expect(parsed.note.startsWith('normalized_first_json_value')).toBe(true);
  });

  it('returns null for invalid json', () => {
    const parsed = parseJsonLikeOutputWithMeta('not-json');
    expect(parsed.value).toBeNull();
    expect(parsed.note).toBe('json_parse_failed');
  });

  it('extracts first array value correctly', () => {
    const first = extractFirstJsonValue('xx [1,2,{"a":3}] yy');
    expect(first.value).toBe('[1,2,{"a":3}]');
    expect(first.trailing).toBe('yy');
  });

  it('unwraps fenced text', () => {
    expect(unwrapJsonFence('```json\n{"x":1}\n```')).toBe('{"x":1}');
  });
});
