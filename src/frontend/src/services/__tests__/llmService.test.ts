/**
 * LLM Service Tests
 *
 * 测试 LLM 配置服务的 API 调用功能
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockApiGet = vi.fn();
const mockApiPost = vi.fn();

vi.mock('@/services/apiClient', () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
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

      expect(mockApiGet).toHaveBeenCalledWith('/v2/llm/config', '读取LLM配置失败');
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

      expect(mockApiPost).toHaveBeenCalledWith('/v2/llm/config', config, '保存LLM配置失败');
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

      expect(mockApiGet).toHaveBeenCalledWith('/v2/llm/status', '读取LLM状态失败');
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

    it('should call chief engineer role chat status path', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          ready: true,
          role: 'chief_engineer',
        },
      });

      const result = await llmService.getRoleChatStatus('chief_engineer');

      expect(mockApiGet).toHaveBeenCalledWith('/v2/role/chief_engineer/chat/status', '获取对话状态失败');
      expect(result.data?.role).toBe('chief_engineer');
    });

    it('should pass explicit workspace to role chat status', async () => {
      mockApiGet.mockResolvedValueOnce({
        ok: true,
        data: {
          ready: true,
          role: 'pm',
          workspace: 'C:/Temp/Product',
        },
      });

      const result = await llmService.getRoleChatStatus('pm', 'C:/Temp/Product');

      expect(mockApiGet).toHaveBeenCalledWith('/v2/role/pm/chat/status?workspace=C%3A%2FTemp%2FProduct', '获取对话状态失败');
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

  // Note: Type exports cannot be tested at runtime in TypeScript
  // These are compile-time only and don't exist at runtime
  describe('Module exports', () => {
    it('should export service functions', () => {
      expect(typeof llmService.getLLMConfig).toBe('function');
      expect(typeof llmService.saveLLMConfig).toBe('function');
      expect(typeof llmService.getLLMStatus).toBe('function');
      expect(typeof llmService.getRoleChatStatus).toBe('function');
    });
  });
});
