/**
 * Standard LLM Interview API Types
 * 
 * This file defines the canonical interfaces for all interview-related API calls.
 * All frontend components should use these types to ensure consistency with backend
 * Pydantic models.
 * 
 * Backend reference: backend/app/routers/llm.py
 * - InterviewAskPayload
 * - InterviewSavePayload
 * - InterviewCancelPayload
 */

import type { InteractiveInterviewReport } from '../components/llm/interview/InteractiveInterviewHall';

// ============================================================================
// Role Types
// ============================================================================

export type RoleId = 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr' | string;

// ============================================================================
// Event Types
// ============================================================================

export interface TestEvent {
  type: 'command' | 'stdout' | 'stderr' | 'result' | 'error' | 'progress';
  timestamp: string;
  content: string;
  details?: unknown;
}

// ============================================================================
// Interview API Payloads (Must match backend Pydantic models)
// ============================================================================

/**
 * Payload for starting/running an interview
 * Matches backend: InterviewAskPayload
 */
export interface AskInterviewPayload {
  roleId: RoleId;
  providerId: string;
  model: string;
  question: string;
  expectedCriteria?: string[];
  expectsThinking?: boolean;
  context?: Array<{ question: string; answer: string }>;
  sessionId?: string | null;
}

/**
 * Payload for saving an interview report
 * Matches backend: InterviewSavePayload
 */
export interface SaveInterviewPayload {
  roleId: RoleId;
  providerId: string;
  model: string | null;
  report: InteractiveInterviewReport;
}

/**
 * Payload for running connectivity test
 */
export interface RunConnectivityPayload {
  role: RoleId;
  providerId: string;
  model: string;
}

/**
 * Payload for running a full interview
 */
export interface RunInterviewPayload {
  role: RoleId;
  providerId: string;
  model: string;
  onEvent?: (event: TestEvent) => void;
}

/**
 * Payload for canceling an interview
 * Matches backend: InterviewCancelPayload
 */
export interface CancelInterviewPayload {
  sessionId: string;
}

// ============================================================================
// API Endpoint Type Mapping (for type-safe validation)
// ============================================================================

export interface InterviewApiEndpoints {
  '/llm/interview/ask': {
    role: string;
    provider_id: string;
    model: string;
    question: string;
    context?: Array<Record<string, any>> | null;
    expects_thinking?: boolean | null;
    criteria?: string[] | null;
    session_id?: string | null;
    api_key?: string | null;
    headers?: Record<string, string>;
    env_overrides?: Record<string, string>;
    debug?: boolean | null;
  };
  '/llm/interview/save': {
    role: string;
    provider_id: string;
    model: string;
    report: Record<string, any>;
    session_id?: string | null;
  };
  '/llm/interview/cancel': {
    session_id: string;
  };
  '/llm/interview/stream': {
    role: string;
    provider_id: string;
    model: string;
    question: string;
    context?: Array<Record<string, any>> | null;
    expects_thinking?: boolean | null;
    criteria?: string[] | null;
    session_id?: string | null;
    api_key?: string | null;
    headers?: Record<string, string>;
    env_overrides?: Record<string, string>;
    debug?: boolean | null;
  };
}

// ============================================================================
// Validation Rules (for runtime validation)
// ============================================================================

export interface ValidationRule<T> {
  validate: (value: T) => { valid: boolean; message?: string };
  required?: boolean;
}

export type ValidationRules<T> = {
  [K in keyof T]?: ValidationRule<T[K]>;
};

export const interviewValidationRules: {
  [K in keyof InterviewApiEndpoints]: ValidationRules<InterviewApiEndpoints[K]>;
} = {
  '/llm/interview/ask': {
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
  '/llm/interview/save': {
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
  '/llm/interview/cancel': {
    session_id: {
      validate: (v) => (v && typeof v === 'string' && v.length > 0
        ? { valid: true }
        : { valid: false, message: 'Session ID is required' }),
      required: true,
    },
  },
  '/llm/interview/stream': {
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
};

// ============================================================================
// Utility Types
// ============================================================================

/**
 * Type guard to check if a value is a valid RoleId
 */
export function isValidRoleId(role: unknown): role is RoleId {
  return typeof role === 'string' && role.length > 0;
}

/**
 * Type guard to check if a payload has all required fields
 */
export function hasRequiredFields<T extends Record<string, any>>(
  payload: T,
  requiredFields: (keyof T)[]
): boolean {
  return requiredFields.every(field => {
    const value = payload[field];
    return value !== undefined && value !== null;
  });
}
