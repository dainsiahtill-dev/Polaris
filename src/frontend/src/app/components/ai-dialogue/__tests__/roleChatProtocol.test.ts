import { describe, expect, it } from 'vitest';

import {
  chatCompleteContent,
  chatErrorMessage,
  normalizeRuntimeChatEvent,
} from '../roleChatProtocol';

describe('roleChatProtocol', () => {
  it('normalizes canonical runtime chat events for the subscribed channel', () => {
    expect(normalizeRuntimeChatEvent({
      channel: 'chat:1',
      payload: {
        type: 'content_chunk',
        data: { content: 'hello' },
      },
    }, 'chat:1')).toEqual({
      type: 'content_chunk',
      data: { content: 'hello' },
    });
  });

  it('normalizes wrapped EVENT payloads from runtime.v2', () => {
    expect(normalizeRuntimeChatEvent({
      type: 'EVENT',
      event: {
        channel: 'chat:1',
        payload: {
          type: 'thinking_chunk',
          data: { content: 'thinking' },
        },
      },
    }, 'chat:1')).toEqual({
      type: 'thinking_chunk',
      data: { content: 'thinking' },
    });
  });

  it('rejects unrelated channels and unknown event types', () => {
    expect(normalizeRuntimeChatEvent({
      channel: 'chat:other',
      payload: { type: 'content_chunk', data: { content: 'ignored' } },
    }, 'chat:1')).toBeNull();
    expect(normalizeRuntimeChatEvent({
      channel: 'chat:1',
      payload: { type: 'old_done', data: { content: 'ignored' } },
    }, 'chat:1')).toBeNull();
  });

  it('keeps historical complete aliases isolated at the protocol boundary', () => {
    expect(chatCompleteContent({ content: 'canonical', response: 'legacy' })).toBe('canonical');
    expect(chatCompleteContent({ response: 'legacy-response' })).toBe('legacy-response');
    expect(chatCompleteContent({ complete: 'legacy-complete' })).toBe('legacy-complete');
  });

  it('normalizes error aliases without leaking objects into the UI', () => {
    expect(chatErrorMessage({ error: 'canonical', message: 'legacy' })).toBe('canonical');
    expect(chatErrorMessage({ message: 'legacy-message' })).toBe('legacy-message');
    expect(chatErrorMessage(undefined)).toBe('未知错误');
  });
});
