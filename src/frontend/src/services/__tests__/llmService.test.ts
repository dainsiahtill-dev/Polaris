/**
 * LLM Service Tests
 *
 * 测试 LLM 配置服务的 API 调用功能
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock the apiClient
const mockApiGet = vi.fn();
const mockApiPost = vi.fn();
const mockApiFetch = vi.fn();

vi.mock('@/services/apiClient', () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
}));

vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

import * as llmService from '../llmService';

describe('llmService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  describe('getLLMConfig', () => {
    it('should call apiGet with correct path', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          schema_version: 1,
          providers: {},
          roles: {},
        },
      });

      const result = await llmService.getLLMConfig();

      expect(mockApiGet).toHaveBeenCalledWith('/llm/config', '读取LLM配置失败');
      expect(result.ok).toBe(true);
    });

    it('should return error on API failure', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to load config',
      });

      const result = await llmService.getLLMConfig();

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to load config');
    });
  });

  describe('saveLLMConfig', () => {
    it('should call apiPost with correct path and config', async () => {
      const config = {
        schema_version: 1,
        providers: { openai: { model: 'gpt-4' } },
        roles: {},
      };

      mockApiPost.mockResolvedValueOnce({
        ok: true,
        data: config,
      });

      const result = await llmService.saveLLMConfig(config);

      expect(mockApiPost).toHaveBeenCalledWith('/llm/config', config, '保存LLM配置失败');
      expect(result.ok).toBe(true);
      expect(result.data).toEqual(config);
    });

    it('should return error on API failure', async () => {
      mockApiPost.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to save config',
      });

      const result = await llmService.saveLLMConfig({
        schema_version: 1,
        providers: {},
        roles: {},
      });

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to save config');
    });
  });

  describe('getLLMStatus', () => {
    it('should call apiGet with correct path', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          state: 'ready',
          required_ready_roles: ['pm', 'director'],
          blocked_roles: [],
          roles: {},
        },
      });

      const result = await llmService.getLLMStatus();

      expect(mockApiGet).toHaveBeenCalledWith('/llm/status', '读取LLM状态失败');
      expect(result.ok).toBe(true);
    });

    it('should return error on API failure', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to load status',
      });

      const result = await llmService.getLLMStatus();

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to load status');
    });
  });

  describe('getRoleChatStatus', () => {
    it('should call apiGet with correct role path', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          ready: true,
          role: 'pm',
        },
      });

      const result = await llmService.getRoleChatStatus('pm');

      expect(mockApiGet).toHaveBeenCalledWith('/v2/role/pm/chat/status', '获取对话状态失败');
      expect(result.ok).toBe(true);
    });

    it('should return error on API failure', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: false,
        error: 'Failed to get chat status',
      });

      const result = await llmService.getRoleChatStatus('director');

      expect(result.ok).toBe(false);
      expect(result.error).toBe('Failed to get chat status');
    });
  });

  describe('sendRoleChatMessage', () => {
    it('should call apiFetch with correct parameters', async () => {
      const request = { message: 'Hello', context: { workspace: 'test' } };
      const mockResponse = new Response(JSON.stringify({ type: 'complete', data: { response: 'Hi' } }));
      mockApiFetch.mockResolvedValueOnce(mockResponse);

      const result = await llmService.sendRoleChatMessage('pm', request);

      expect(mockApiFetch).toHaveBeenCalledWith('/v2/role/pm/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: undefined,
      });
      expect(result).toBe(mockResponse);
    });

    it('should pass abort signal when provided', async () => {
      const request = { message: 'Hello' };
      const signal = new AbortController().signal;
      const mockResponse = new Response(JSON.stringify({ type: 'complete' }));
      mockApiFetch.mockResolvedValueOnce(mockResponse);

      await llmService.sendRoleChatMessage('architect', request, signal);

      expect(mockApiFetch).toHaveBeenCalledWith('/v2/role/architect/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal,
      });
    });
  });

  describe('parseSSEData', () => {
    it('should parse valid SSE data line', () => {
      const result = llmService.parseSSEData('data: {"type":"content_chunk","data":{"content":"Hello"}}');

      expect(result).not.toBeNull();
      expect(result?.type).toBe('content_chunk');
      expect(result?.data?.content).toBe('Hello');
    });

    it('should return null for non-data lines', () => {
      const result = llmService.parseSSEData('not a data line');

      expect(result).toBeNull();
    });

    it('should return null for [DONE] marker', () => {
      const result = llmService.parseSSEData('data: [DONE]');

      expect(result).toBeNull();
    });

    it('should handle non-JSON data as content chunk', () => {
      const result = llmService.parseSSEData('data: plain text content');

      expect(result).not.toBeNull();
      expect(result?.type).toBe('content_chunk');
      expect(result?.data?.content).toBe('plain text content');
    });

    it('should handle empty data gracefully', () => {
      // Empty data line returns content_chunk with empty content
      const result = llmService.parseSSEData('data: ');

      // It returns a content_chunk with empty content (not null)
      expect(result?.type).toBe('content_chunk');
      expect(result?.data?.content).toBe('');
    });
  });

  describe('createStreamReader', () => {
    it('should return reader from response body', () => {
      const mockReader = {} as ReadableStreamDefaultReader<Uint8Array>;
      const mockBody = {
        getReader: () => mockReader,
      };
      const response = { body: mockBody } as unknown as Response;

      const result = llmService.createStreamReader(response);

      expect(result).toBe(mockReader);
    });

    it('should return null when body is null', () => {
      const response = { body: null } as unknown as Response;

      const result = llmService.createStreamReader(response);

      expect(result).toBeNull();
    });
  });

  // Note: Type exports cannot be tested at runtime in TypeScript
  // These are compile-time only and don't exist at runtime
  describe('Module exports', () => {
    it('should export service functions', () => {
      expect(typeof llmService.getLLMConfig).toBe('function');
      expect(typeof llmService.saveLLMConfig).toBe('function');
      expect(typeof llmService.getLLMStatus).toBe('function');
      expect(typeof llmService.getRoleChatStatus).toBe('function');
      expect(typeof llmService.sendRoleChatMessage).toBe('function');
      expect(typeof llmService.parseSSEData).toBe('function');
      expect(typeof llmService.createStreamReader).toBe('function');
    });
  });
});
