/**
 * Shared type definitions for Polaris frontend
 *
 * This module provides core type definitions used across multiple features.
 * Feature-specific types should be defined in their respective feature directories.
 */

import { z } from 'zod';
import { devLogger } from '@/app/utils/devLogger';

// ============================================================================
// Common Schemas
// ============================================================================

export const IdSchema = z.string().min(1);
export const TimestampSchema = z.string().datetime();

// ============================================================================
// API Response Schemas
// ============================================================================

export const ApiSuccessSchema = z.object({
  success: z.literal(true),
  data: z.unknown(),
});

export const ApiErrorSchema = z.object({
  success: z.literal(false),
  error: z.string(),
  code: z.string().optional(),
  details: z.unknown().optional(),
});

export const ApiResponseSchema = z.union([ApiSuccessSchema, ApiErrorSchema]);

// ============================================================================
// Status Types
// ============================================================================

export const TaskStatusSchema = z.enum([
  'pending',
  'in_progress',
  'completed',
  'failed',
  'cancelled',
  'timeout',
  'blocked',
]);

export type TaskStatus = z.infer<typeof TaskStatusSchema>;

export const PrioritySchema = z.enum(['low', 'medium', 'high', 'critical']);
export type Priority = z.infer<typeof PrioritySchema>;

// ============================================================================
// Base Entity Types
// ============================================================================

export interface BaseEntity {
  id: string;
  createdAt: string;
  updatedAt: string;
}

export interface NamedEntity extends BaseEntity {
  name: string;
  description?: string;
}

// ============================================================================
// Utility Types
// ============================================================================

export type Nullable<T> = T | null;
export type Optional<T> = T | undefined;
export type AsyncState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; error: string };

// ============================================================================
// Validation Helpers
// ============================================================================

export function validateOrThrow<T>(schema: z.ZodSchema<T>, data: unknown): T {
  try {
    return schema.parse(data);
  } catch (error) {
    if (error instanceof z.ZodError) {
      devLogger.error('Validation failed:', error.issues);
      throw new Error(`Validation failed: ${error.issues.map((issue) => issue.message).join(', ')}`);
    }
    throw error;
  }
}

export function safeValidate<T>(schema: z.ZodSchema<T>, data: unknown): { success: true; data: T } | { success: false; error: string } {
  try {
    return { success: true, data: schema.parse(data) };
  } catch (error) {
    if (error instanceof z.ZodError) {
      return { success: false, error: error.issues.map((issue) => issue.message).join(', ') };
    }
    return { success: false, error: String(error) };
  }
}
