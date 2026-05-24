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

    it('includes missing runtime roles from structured backend errors', async () => {
      const response = new Response(
        JSON.stringify({
          error: {
            code: 'RUNTIME_ROLES_NOT_READY',
            message: 'One or more required runtime roles are not ready',
            details: {
              required_roles: ['pm', 'chief_engineer', 'director', 'qa'],
              missing_roles: ['pm', 'director'],
            },
          },
        }),
        { status: 409 }
      );

      await expect(extractErrorDetail(response, 'fallback')).resolves.toBe(
        'One or more required runtime roles are not ready · blocked: pm, director'
      );
    });

    it('falls back when the response body is not JSON', async () => {
      const response = new Response('not json', { status: 500 });

      await expect(extractErrorDetail(response, 'fallback')).resolves.toBe('fallback');
    });
  });
});
