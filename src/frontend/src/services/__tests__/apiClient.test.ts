import { describe, expect, it } from 'vitest';
import { extractErrorDetail } from '../apiClient';

describe('apiClient', () => {
  describe('extractErrorDetail', () => {
    it('extracts nested structured backend error messages', async () => {
      const response = new Response(
        JSON.stringify({
          error: {
            code: 'INVALID_LLM_CONFIG',
            message: 'Invalid LLM configuration: provider timeout too high',
          },
        }),
        { status: 400 }
      );

      await expect(extractErrorDetail(response, 'fallback')).resolves.toBe(
        'Invalid LLM configuration: provider timeout too high'
      );
    });

    it('falls back when the response body is not JSON', async () => {
      const response = new Response('not json', { status: 500 });

      await expect(extractErrorDetail(response, 'fallback')).resolves.toBe('fallback');
    });
  });
});
