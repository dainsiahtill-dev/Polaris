import { describe, expect, it } from 'vitest';

import { normalizeFinalProviderRequestPayload } from './finalProviderRequestProtocol';

describe('normalizeFinalProviderRequestPayload', () => {
  it('accepts canonical final provider request audit payloads', () => {
    const payload = normalizeFinalProviderRequestPayload({
      schema_version: 'context.final_provider_request_audit.v1',
      context_hash: 'abc123abc123abc123abc123',
      trace_id: 'trace-1',
      call_id: 'call-1',
      stored_at: '2026-06-30T00:00:00Z',
      message_count: 2,
      role: 'director',
      provider_id: 'openai',
      provider_type: 'openai_compat',
      model: 'gpt-5',
      tools: [{ type: 'function', name: 'write_file' }, null, 'bad'],
      tool_choice: 'auto',
      response_format: { type: 'json_schema' },
      provider_request: { model: 'gpt-5' },
      final_request_context_audit: { final_request_token_estimate: 1200 },
    });

    expect(payload).toMatchObject({
      schema_version: 'context.final_provider_request_audit.v1',
      context_hash: 'abc123abc123abc123abc123',
      trace_id: 'trace-1',
      call_id: 'call-1',
      message_count: 2,
      role: 'director',
      provider_id: 'openai',
      provider_type: 'openai_compat',
      model: 'gpt-5',
      tool_choice: 'auto',
    });
    expect(payload?.tools).toEqual([{ type: 'function', name: 'write_file' }]);
    expect(payload?.provider_request).toEqual({ model: 'gpt-5' });
    expect(payload?.final_request_context_audit).toEqual({ final_request_token_estimate: 1200 });
  });

  it('fails closed for non-object payloads', () => {
    expect(normalizeFinalProviderRequestPayload(null)).toBeNull();
    expect(normalizeFinalProviderRequestPayload('bad')).toBeNull();
  });

  it('normalizes malformed optional objects to empty records', () => {
    const payload = normalizeFinalProviderRequestPayload({
      provider_request: 'bad',
      final_request_context_audit: ['bad'],
      tools: 'bad',
    });

    expect(payload?.provider_request).toEqual({});
    expect(payload?.final_request_context_audit).toEqual({});
    expect(payload?.tools).toEqual([]);
  });
});
