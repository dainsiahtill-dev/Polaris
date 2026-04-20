/**
 * Tests for API Validation Layer
 */

import { 
  validateApiPayload, 
  assertApiPayload, 
  validatePayloadDetailed,
  createValidatedFetch 
} from '../apiValidation';

describe('API Validation', () => {
  describe('validateApiPayload', () => {
    it('should validate a correct /llm/interview/ask payload', () => {
      const payload = {
        role: 'pm',
        provider_id: 'minimax-123',
        model: 'MiniMax-M2.1',
        question: 'What is a decorator?',
      };
      
      const result = validateApiPayload('/llm/interview/ask', payload);
      
      expect(result.valid).toBe(true);
      expect(result.errors).toHaveLength(0);
    });
    
    it('should fail when required fields are missing', () => {
      const payload = {
        role: 'pm',
        provider_id: 'minimax-123',
        // missing model and question
      };
      
      const result = validateApiPayload('/llm/interview/ask', payload as any);
      
      expect(result.valid).toBe(false);
      expect(result.errors.length).toBeGreaterThan(0);
      expect(result.errors.some(e => e.includes('model'))).toBe(true);
      expect(result.errors.some(e => e.includes('question'))).toBe(true);
    });
    
    it('should fail when model is empty string', () => {
      const payload = {
        role: 'pm',
        provider_id: 'minimax-123',
        model: '',
        question: 'What is a decorator?',
      };
      
      const result = validateApiPayload('/llm/interview/ask', payload);
      
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('model'))).toBe(true);
    });
    
    it('should validate /llm/interview/save payload', () => {
      const payload = {
        role: 'pm',
        provider_id: 'minimax-123',
        model: 'MiniMax-M2.1',
        report: { id: 'test', status: 'complete' },
      };
      
      const result = validateApiPayload('/llm/interview/save', payload);
      
      expect(result.valid).toBe(true);
    });
    
    it('should fail when model is null in save payload', () => {
      const payload = {
        role: 'pm',
        provider_id: 'minimax-123',
        model: null,
        report: { id: 'test' },
      };
      
      const result = validateApiPayload('/llm/interview/save', payload);
      
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('model'))).toBe(true);
    });
    
    it('should return warnings for unexpected fields', () => {
      const payload = {
        role: 'pm',
        provider_id: 'minimax-123',
        model: 'MiniMax-M2.1',
        question: 'What?',
        unexpectedField: 'should warn',
      };
      
      const result = validateApiPayload('/llm/interview/ask', payload);
      
      expect(result.valid).toBe(true);
      expect(result.warnings.some(w => w.includes('unexpectedField'))).toBe(true);
    });
    
    it('should handle unknown endpoints gracefully', () => {
      const payload = { any: 'data' };
      
      const result = validateApiPayload('/unknown/endpoint' as any, payload);
      
      expect(result.valid).toBe(true);
      expect(result.warnings).toHaveLength(0);
    });
  });
  
  describe('assertApiPayload', () => {
    it('should not throw for valid payload', () => {
      const payload = {
        role: 'pm',
        provider_id: 'minimax-123',
        model: 'MiniMax-M2.1',
        question: 'What?',
      };
      
      expect(() => assertApiPayload('/llm/interview/ask', payload)).not.toThrow();
    });
    
    it('should throw for invalid payload', () => {
      const payload = {
        role: 'pm',
        // missing required fields
      };
      
      expect(() => assertApiPayload('/llm/interview/ask', payload as any)).toThrow('API payload validation failed');
    });
    
    it('should include field names in error message', () => {
      const payload = {
        role: 'pm',
        provider_id: 'minimax-123',
        // missing model and question
      };
      
      try {
        assertApiPayload('/llm/interview/ask', payload as any);
        fail('Should have thrown');
      } catch (e: any) {
        expect(e.message).toContain('model');
        expect(e.message).toContain('question');
      }
    });
  });
  
  describe('validatePayloadDetailed', () => {
    it('should return detailed error information', () => {
      const payload = {
        role: '',
        provider_id: 'minimax-123',
        // missing model and question
      };
      
      const result = validatePayloadDetailed('/llm/interview/ask', payload as any);
      
      expect(result.valid).toBe(false);
      expect(result.missing).toContain('model');
      expect(result.missing).toContain('question');
      expect(result.errors.length).toBeGreaterThan(0);
    });
    
    it('should categorize invalid fields', () => {
      const payload = {
        role: '', // empty string - validation should catch this
        provider_id: 'minimax-123',
        model: 'gpt-4',
        question: 'Valid question',
      };
      
      const result = validatePayloadDetailed('/llm/interview/ask', payload);
      
      if (!result.valid) {
        expect(result.invalid.some(i => i.field === 'role')).toBe(true);
      }
    });
  });
  
  describe('createValidatedFetch', () => {
    it('should validate payload before calling fetch', async () => {
      const mockFetch = jest.fn().mockResolvedValue(new Response());
      const validatedFetch = createValidatedFetch(mockFetch);
      
      const payload = {
        role: 'pm',
        provider_id: 'minimax-123',
        model: 'MiniMax-M2.1',
        question: 'What?',
      };
      
      await validatedFetch('/llm/interview/ask', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      
      expect(mockFetch).toHaveBeenCalledWith('/llm/interview/ask', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
    });
    
    it('should throw for invalid payload without calling fetch', async () => {
      const mockFetch = jest.fn().mockResolvedValue(new Response());
      const validatedFetch = createValidatedFetch(mockFetch);
      
      const payload = {
        role: 'pm',
        // missing required fields
      };
      
      await expect(
        validatedFetch('/llm/interview/ask', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
      ).rejects.toThrow('API payload validation failed');
      
      expect(mockFetch).not.toHaveBeenCalled();
    });
    
    it('should skip validation for non-interview endpoints', async () => {
      const mockFetch = jest.fn().mockResolvedValue(new Response());
      const validatedFetch = createValidatedFetch(mockFetch);
      
      await validatedFetch('/other/endpoint', {
        method: 'POST',
        body: JSON.stringify({ any: 'data' }),
      });
      
      expect(mockFetch).toHaveBeenCalled();
    });
    
    it('should skip validation for GET requests', async () => {
      const mockFetch = jest.fn().mockResolvedValue(new Response());
      const validatedFetch = createValidatedFetch(mockFetch);
      
      await validatedFetch('/llm/interview/ask', {
        method: 'GET',
      });
      
      expect(mockFetch).toHaveBeenCalled();
    });
    
    it('should handle invalid JSON gracefully', async () => {
      const mockFetch = jest.fn().mockResolvedValue(new Response());
      const validatedFetch = createValidatedFetch(mockFetch);
      
      await validatedFetch('/llm/interview/ask', {
        method: 'POST',
        body: 'invalid json',
      });
      
      // Should log warning but not throw
      expect(mockFetch).toHaveBeenCalled();
    });
  });
});
