/**
 * Standard LLM Interview API Types
 *
 * This file defines the canonical interfaces for all interview-related API calls.
 * All frontend components should use these types to ensure consistency with backend
 * Pydantic models.
 *
 * Backend reference: polaris.delivery.http.routers.interview
 * - InterviewAskPayload
 * - InterviewSavePayload
 * - InterviewCancelPayload
 */
export const interviewValidationRules = {
    '/v2/llm/interview/ask': {
        role: {
            validate: (v) => (v && typeof v === 'string' && v.length > 0
                ? { valid: true }
                : { valid: false, message: 'Role is required' }),
            required: true,
        },
        provider_id: {
            validate: (v) => (v && typeof v === 'string' && v.length > 0
                ? { valid: true }
                : { valid: false, message: 'Provider ID is required' }),
            required: true,
        },
        model: {
            validate: (v) => (v && typeof v === 'string' && v.length > 0
                ? { valid: true }
                : { valid: false, message: 'Model is required' }),
            required: true,
        },
        question: {
            validate: (v) => (v && typeof v === 'string' && v.length > 0
                ? { valid: true }
                : { valid: false, message: 'Question is required' }),
            required: true,
        },
    },
    '/v2/llm/interview/save': {
        role: {
            validate: (v) => (v && typeof v === 'string' && v.length > 0
                ? { valid: true }
                : { valid: false, message: 'Role is required' }),
            required: true,
        },
        provider_id: {
            validate: (v) => (v && typeof v === 'string' && v.length > 0
                ? { valid: true }
                : { valid: false, message: 'Provider ID is required' }),
            required: true,
        },
        model: {
            validate: (v) => (v !== undefined && v !== null
                ? { valid: true }
                : { valid: false, message: 'Model is required' }),
            required: true,
        },
        report: {
            validate: (v) => (v && typeof v === 'object'
                ? { valid: true }
                : { valid: false, message: 'Report is required' }),
            required: true,
        },
    },
    '/v2/llm/interview/cancel': {
        session_id: {
            validate: (v) => (v && typeof v === 'string' && v.length > 0
                ? { valid: true }
                : { valid: false, message: 'Session ID is required' }),
            required: true,
        },
    },
};
// ============================================================================
// Utility Types
// ============================================================================
/**
 * Type guard to check if a value is a valid RoleId
 */
export function isValidRoleId(role) {
    return typeof role === 'string' && role.length > 0;
}
/**
 * Type guard to check if a payload has all required fields
 */
export function hasRequiredFields(payload, requiredFields) {
    return requiredFields.every(field => {
        const value = payload[field];
        return value !== undefined && value !== null;
    });
}
