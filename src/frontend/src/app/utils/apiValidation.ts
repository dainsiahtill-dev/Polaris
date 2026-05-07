/**
 * API Payload Validation Layer
 * 
 * Provides runtime validation for API payloads to catch missing required fields
 * before sending requests to the backend. This prevents 422 errors and provides
 * clear error messages during development.
 * 
 * Usage:
 *   import { validateApiPayload, assertApiPayload } from './apiValidation';
 *   
 *   // Validation with result
 *   const { valid, errors } = validateApiPayload('/llm/interview/ask', payload);
 *   
 *   // Assertion (throws on failure)
 *   assertApiPayload('/llm/interview/ask', payload);
 */

import type { InterviewApiEndpoints } from '../types/llm';
import { interviewValidationRules } from '../types/llm';
import { devLogger } from './devLogger';

// ============================================================================
// Configuration
// ============================================================================

/**
 * Enable detailed validation logging in development
 */
const DEFAULT_VALIDATION_LOGGING_ENABLED =
  (import.meta.env.DEV && import.meta.env.MODE !== 'test') ||
  import.meta.env.VITE_API_VALIDATION_LOGGING === '1';

/**
 * List of endpoints that require payload validation
 */
const VALIDATED_ENDPOINTS = [
  '/llm/interview/ask',
  '/llm/interview/save',
  '/llm/interview/cancel',
  '/llm/interview/stream',
] as const;

type ValidatedEndpoint = typeof VALIDATED_ENDPOINTS[number];

/**
 * Check if an endpoint requires validation
 */
function isValidatedEndpoint(endpoint: string): endpoint is ValidatedEndpoint {
  return VALIDATED_ENDPOINTS.some(e => endpoint.includes(e));
}

// ============================================================================
// Validation Functions
// ============================================================================

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

/**
 * Validate an API payload against defined rules
 * 
 * @param endpoint - API endpoint path
 * @param payload - Request payload to validate
 * @returns Validation result with errors and warnings
 */
export function validateApiPayload<T extends keyof InterviewApiEndpoints>(
  endpoint: T,
  payload: InterviewApiEndpoints[T]
): ValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];
  
  const rules = interviewValidationRules[endpoint];
  if (!rules) {
    if (isValidationLoggingEnabled()) {
      devLogger.warn(`[API Validation] No validation rules for endpoint: ${endpoint}`);
    }
    return { valid: true, errors, warnings };
  }
  
  for (const [field, rule] of Object.entries(rules)) {
    const value = payload[field as keyof typeof payload];
    
    // Check required fields
    if (rule?.required && (value === undefined || value === null)) {
      errors.push(`[${endpoint}] Required field missing: "${field}"`);
      continue;
    }
    
    // Run custom validation if value exists
    if (value !== undefined && value !== null && rule?.validate) {
      const result = rule.validate(value);
      if (!result.valid) {
        errors.push(`[${endpoint}] Field "${field}": ${result.message}`);
      }
    }
  }
  
  // Check for extra fields (warnings)
  const allowedFields = Object.keys(rules);
  const actualFields = Object.keys(payload);
  const extraFields = actualFields.filter(f => !allowedFields.includes(f));
  if (extraFields.length > 0) {
    warnings.push(`[${endpoint}] Unexpected fields: ${extraFields.join(', ')}`);
  }
  
  if (isValidationLoggingEnabled() && errors.length > 0) {
    devLogger.error('[API Validation Failed]', {
      endpoint,
      errors,
      payload: sanitizePayloadForLogging(payload),
    });
  }
  
  return {
    valid: errors.length === 0,
    errors,
    warnings,
  };
}

/**
 * Assert that a payload is valid (throws if invalid)
 * 
 * @param endpoint - API endpoint path
 * @param payload - Request payload to validate
 * @throws Error if validation fails
 */
export function assertApiPayload<T extends keyof InterviewApiEndpoints>(
  endpoint: T,
  payload: InterviewApiEndpoints[T]
): void {
  const { valid, errors } = validateApiPayload(endpoint, payload);
  if (!valid) {
    const message = `API payload validation failed for ${endpoint}:\n  - ${errors.join('\n  - ')}`;
    throw new Error(message);
  }
}

/**
 * Validate payload and return detailed result
 * Similar to validateApiPayload but with more context
 */
export function validatePayloadDetailed<T extends keyof InterviewApiEndpoints>(
  endpoint: T,
  payload: InterviewApiEndpoints[T]
): {
  valid: boolean;
  errors: Array<{ field: string; message: string; value: unknown }>;
  missing: string[];
  invalid: Array<{ field: string; message: string }>;
} {
  const errors: Array<{ field: string; message: string; value: unknown }> = [];
  const missing: string[] = [];
  const invalid: Array<{ field: string; message: string }> = [];
  
  const rules = interviewValidationRules[endpoint];
  if (!rules) {
    return { valid: true, errors, missing, invalid };
  }
  
  for (const [field, rule] of Object.entries(rules)) {
    const value = payload[field as keyof typeof payload];
    
    if (rule?.required && (value === undefined || value === null)) {
      missing.push(field);
      errors.push({ field, message: 'Required field missing', value });
      continue;
    }
    
    if (value !== undefined && value !== null && rule?.validate) {
      const result = rule.validate(value);
      if (!result.valid) {
        invalid.push({ field, message: result.message || 'Validation failed' });
        errors.push({ field, message: result.message || 'Validation failed', value });
      }
    }
  }
  
  return {
    valid: errors.length === 0,
    errors,
    missing,
    invalid,
  };
}

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Sanitize payload for logging (remove sensitive data)
 */
function sanitizePayloadForLogging(payload: Record<string, any>): Record<string, any> {
  const sanitized = { ...payload };
  
  // Remove sensitive fields
  const sensitiveFields = ['api_key', 'password', 'token', 'secret', 'authorization'];
  for (const field of Object.keys(sanitized)) {
    const lowerField = field.toLowerCase();
    if (sensitiveFields.some(s => lowerField.includes(s))) {
      sanitized[field] = '***REDACTED***';
    }
  }
  
  return sanitized;
}

/**
 * Create a validation wrapper for fetch
 * 
 * Usage:
 *   const validatedFetch = createValidatedFetch(apiFetch);
 *   const response = await validatedFetch('/llm/interview/ask', { body: JSON.stringify(payload) });
 */
export function createValidatedFetch(
  fetchImpl: (endpoint: string, options?: RequestInit) => Promise<Response>
) {
  return async function validatedFetch(
    endpoint: string,
    options?: RequestInit
  ): Promise<Response> {
    // Validate payload if present
    if (options?.body && isValidatedEndpoint(endpoint)) {
      try {
        const payload = JSON.parse(options.body as string);
        assertApiPayload(endpoint as keyof InterviewApiEndpoints, payload);
      } catch (e) {
        if (e instanceof SyntaxError) {
          devLogger.warn(`[API Validation] Invalid JSON in request body for ${endpoint}`);
        } else {
          // Re-throw validation errors
          throw e;
        }
      }
    }
    
    return fetchImpl(endpoint, options);
  };
}

// ============================================================================
// Debug Utilities
// ============================================================================

/**
 * Enable or disable validation logging at runtime
 */
export function setValidationLogging(enabled: boolean): void {
  const global = globalThis as Record<string, unknown>;
  global.__API_VALIDATION_LOGGING__ = enabled;
}

/**
 * Check if validation logging is enabled
 */
export function isValidationLoggingEnabled(): boolean {
  const global = globalThis as Record<string, unknown>;
  return (global.__API_VALIDATION_LOGGING__ as boolean | undefined) ?? DEFAULT_VALIDATION_LOGGING_ENABLED;
}

/**
 * Get validation statistics (for debugging)
 */
export function getValidationStats(): {
  validatedEndpoints: string[];
  rulesCount: number;
} {
  return {
    validatedEndpoints: [...VALIDATED_ENDPOINTS],
    rulesCount: Object.keys(interviewValidationRules).length,
  };
}
