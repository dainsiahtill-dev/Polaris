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
 *   const { valid, errors } = validateApiPayload('/v2/llm/interview/ask', payload);
 *
 *   // Assertion (throws on failure)
 *   assertApiPayload('/v2/llm/interview/ask', payload);
 */
import { interviewValidationRules } from '../types/llm';
import { devLogger } from './devLogger';
// ============================================================================
// Configuration
// ============================================================================
/**
 * Enable detailed validation logging in development
 */
const DEFAULT_VALIDATION_LOGGING_ENABLED = (import.meta.env.DEV && import.meta.env.MODE !== 'test') ||
    import.meta.env.VITE_API_VALIDATION_LOGGING === '1';
/**
 * List of endpoints that require payload validation
 */
const VALIDATED_ENDPOINTS = [
    '/v2/llm/interview/ask',
    '/v2/llm/interview/save',
    '/v2/llm/interview/cancel',
];
/**
 * Check if an endpoint requires validation
 */
function isValidatedEndpoint(endpoint) {
    return VALIDATED_ENDPOINTS.some(e => endpoint.includes(e));
}
/**
 * Validate an API payload against defined rules
 *
 * @param endpoint - API endpoint path
 * @param payload - Request payload to validate
 * @returns Validation result with errors and warnings
 */
export function validateApiPayload(endpoint, payload) {
    const errors = [];
    const warnings = [];
    const rules = interviewValidationRules[endpoint];
    if (!rules) {
        if (isValidationLoggingEnabled()) {
            devLogger.warn(`[API Validation] No validation rules for endpoint: ${endpoint}`);
        }
        return { valid: true, errors, warnings };
    }
    for (const [field, rule] of Object.entries(rules)) {
        const value = payload[field];
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
export function assertApiPayload(endpoint, payload) {
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
export function validatePayloadDetailed(endpoint, payload) {
    const errors = [];
    const missing = [];
    const invalid = [];
    const rules = interviewValidationRules[endpoint];
    if (!rules) {
        return { valid: true, errors, missing, invalid };
    }
    for (const [field, rule] of Object.entries(rules)) {
        const value = payload[field];
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
function sanitizePayloadForLogging(payload) {
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
 *   const response = await validatedFetch('/v2/llm/interview/ask', { body: JSON.stringify(payload) });
 */
export function createValidatedFetch(fetchImpl) {
    return async function validatedFetch(endpoint, options) {
        // Validate payload if present
        if (options?.body && isValidatedEndpoint(endpoint)) {
            try {
                const payload = JSON.parse(options.body);
                assertApiPayload(endpoint, payload);
            }
            catch (e) {
                if (e instanceof SyntaxError) {
                    devLogger.warn(`[API Validation] Invalid JSON in request body for ${endpoint}`);
                }
                else {
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
export function setValidationLogging(enabled) {
    const global = globalThis;
    global.__API_VALIDATION_LOGGING__ = enabled;
}
/**
 * Check if validation logging is enabled
 */
export function isValidationLoggingEnabled() {
    const global = globalThis;
    return global.__API_VALIDATION_LOGGING__ ?? DEFAULT_VALIDATION_LOGGING_ENABLED;
}
/**
 * Get validation statistics (for debugging)
 */
export function getValidationStats() {
    return {
        validatedEndpoints: [...VALIDATED_ENDPOINTS],
        rulesCount: Object.keys(interviewValidationRules).length,
    };
}
