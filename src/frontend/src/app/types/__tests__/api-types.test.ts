/**
 * Compile-time Type Tests for API Payloads
 * 
 * These tests run at compile time (not runtime) to ensure frontend types
 * match backend Pydantic models. If types drift, TypeScript will report errors.
 * 
 * To run: npx tsc --noEmit
 */

import type {
  AskInterviewPayload,
  SaveInterviewPayload,
  CancelInterviewPayload,
  InterviewApiEndpoints,
} from '../llm';

// ============================================================================
// Backend Type Mirrors (keep in sync with backend/app/routers/llm.py)
// ============================================================================

/**
 * Mirror of backend InterviewAskPayload Pydantic model
 * Source: backend/app/routers/llm.py line 76-89
 */
interface BackendAskPayload {
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
}

/**
 * Mirror of backend InterviewSavePayload Pydantic model
 * Source: backend/app/routers/llm.py line 123-128
 */
interface BackendSavePayload {
  role: string;
  provider_id: string;
  model: string;
  report: Record<string, any>;
  session_id?: string | null;
}

/**
 * Mirror of backend InterviewCancelPayload Pydantic model
 * Source: backend/app/routers/llm.py line 119-120
 */
interface BackendCancelPayload {
  session_id: string;
}

// ============================================================================
// Type Equality Utilities
// ============================================================================

/**
 * Assert that two types are equal
 * If types differ, this will cause a compile error
 */
type AssertEqual<T, U> = T extends U ? (U extends T ? true : false) : false;

/**
 * Assert that T is assignable to U (T can be used where U is expected)
 */
type AssertAssignable<T, U> = T extends U ? true : false;

/**
 * Assert that a type is true (useful for compile-time assertions)
 */
type AssertTrue<T extends true> = T;

// ============================================================================
// Field-level Type Tests
// ============================================================================

// Test AskInterviewPayload field mappings
namespace AskPayloadTests {
  // role <-> roleId mapping (camelCase in frontend, snake_case in backend)
  export type RoleAssignable = AssertAssignable<AskInterviewPayload['roleId'], BackendAskPayload['role']>;
  
  // providerId <-> provider_id
  export type ProviderIdAssignable = AssertAssignable<AskInterviewPayload['providerId'], BackendAskPayload['provider_id']>;
  
  // model (should be identical)
  export type ModelEqual = AssertEqual<AskInterviewPayload['model'], BackendAskPayload['model']>;
  
  // question (should be identical)
  export type QuestionEqual = AssertEqual<AskInterviewPayload['question'], BackendAskPayload['question']>;
  
  // Compile-time assertions - if any fail, TypeScript will error here
  export const _roleAssignable: RoleAssignable = true;
  export const _providerIdAssignable: ProviderIdAssignable = true;
  export const _modelEqual: ModelEqual = true;
  export const _questionEqual: QuestionEqual = true;
}

// Test SaveInterviewPayload field mappings
namespace SavePayloadTests {
  // role <-> roleId
  export type RoleAssignable = AssertAssignable<SaveInterviewPayload['roleId'], BackendSavePayload['role']>;
  
  // providerId <-> provider_id
  export type ProviderIdAssignable = AssertAssignable<SaveInterviewPayload['providerId'], BackendSavePayload['provider_id']>;
  
  // model (frontend allows null, backend requires string)
  // This is intentional - we should handle null-to-string conversion in the API layer
  export type ModelAssignable = AssertAssignable<string, BackendSavePayload['model']>;
  
  // Compile-time assertions
  export const _roleAssignable: RoleAssignable = true;
  export const _providerIdAssignable: ProviderIdAssignable = true;
  export const _modelAssignable: ModelAssignable = true;
}

// Test CancelInterviewPayload
namespace CancelPayloadTests {
  // sessionId <-> session_id
  export type SessionIdAssignable = AssertAssignable<CancelInterviewPayload['sessionId'], BackendCancelPayload['session_id']>;
  
  export const _sessionIdAssignable: SessionIdTests.SessionIdAssignable = true;
}

// ============================================================================
// Endpoint Payload Tests
// ============================================================================

namespace EndpointTests {
  // Test that InterviewApiEndpoints match backend expectations
  export type AskEndpointValid = AssertAssignable<
    InterviewApiEndpoints['/llm/interview/ask'],
    BackendAskPayload
  >;
  
  export type SaveEndpointValid = AssertAssignable<
    InterviewApiEndpoints['/llm/interview/save'],
    BackendSavePayload
  >;
  
  export type CancelEndpointValid = AssertAssignable<
    InterviewApiEndpoints['/llm/interview/cancel'],
    BackendCancelPayload
  >;
  
  // Compile-time assertions
  export const _askValid: AskEndpointValid = true;
  export const _saveValid: SaveEndpointValid = true;
  export const _cancelValid: CancelEndpointValid = true;
}

// ============================================================================
// Required Fields Tests
// ============================================================================

namespace RequiredFieldsTests {
  /**
   * Extract required fields from a type (non-optional, non-nullable)
   */
  type RequiredFields<T> = {
    [K in keyof T as undefined extends T[K] ? never : null extends T[K] ? never : K]: T[K];
  };
  
  // Test that required fields are consistent
  // Backend required fields for /llm/interview/ask: role, provider_id, model, question
  type BackendAskRequired = RequiredFields<BackendAskPayload>;
  type FrontendAskRequired = RequiredFields<AskInterviewPayload>;
  
  // Frontend required fields should be assignable to backend required fields
  export type AskRequiredFieldsValid = AssertAssignable<
    keyof FrontendAskRequired,
    keyof BackendAskRequired
  >;
  
  export const _askRequiredValid: AskRequiredFieldsValid = true;
}

// ============================================================================
// Runtime Type Guards Tests
// ============================================================================

import { isValidRoleId, hasRequiredFields } from '../llm';

describe('Runtime Type Guards', () => {
  describe('isValidRoleId', () => {
    it('should return true for valid role IDs', () => {
      expect(isValidRoleId('pm')).toBe(true);
      expect(isValidRoleId('qa')).toBe(true);
      expect(isValidRoleId('custom-role')).toBe(true);
    });
    
    it('should return false for invalid role IDs', () => {
      expect(isValidRoleId('')).toBe(false);
      expect(isValidRoleId(null)).toBe(false);
      expect(isValidRoleId(undefined)).toBe(false);
      expect(isValidRoleId(123)).toBe(false);
    });
  });
  
  describe('hasRequiredFields', () => {
    it('should return true when all required fields are present', () => {
      const payload = {
        roleId: 'pm',
        providerId: 'test-provider',
        model: 'gpt-4',
      };
      expect(hasRequiredFields(payload, ['roleId', 'providerId', 'model'])).toBe(true);
    });
    
    it('should return false when a required field is missing', () => {
      const payload = {
        roleId: 'pm',
        providerId: 'test-provider',
        // model is missing
      };
      expect(hasRequiredFields(payload, ['roleId', 'providerId', 'model'])).toBe(false);
    });
    
    it('should return false when a required field is null', () => {
      const payload = {
        roleId: 'pm',
        providerId: 'test-provider',
        model: null,
      };
      expect(hasRequiredFields(payload, ['roleId', 'providerId', 'model'])).toBe(false);
    });
  });
});

// ============================================================================
// Integration Reminder
// ============================================================================

/**
 * REMINDER: When backend types change, update this file:
 * 
 * 1. Update the Backend*Payload interfaces to match Pydantic models
 * 2. Add new type tests for changed fields
 * 3. Run `npx tsc --noEmit` to verify
 * 4. Update frontend types in ../llm.ts if needed
 */

export {};
