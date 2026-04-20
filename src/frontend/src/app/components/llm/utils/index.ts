/**
 * Utils Exports
 */

export {
  createError,
  normalizeError,
  isAppError,
  getUserFriendlyMessage,
  errorLogger,
  withErrorHandling,
  useErrorHandling,
  CommonErrors,
  getErrorFallbackMessage,
} from './errorHandling';

export type {
  ErrorCategory,
  AppError,
  ErrorLogger,
  AsyncFunction,
  UseErrorHandlingResult,
  ErrorBoundaryFallbackProps,
} from './errorHandling';

export {
  resolveModelName,
  validateModelName,
  getModelResolutionLog,
  getDefaultModelForProvider,
  type ModelResolutionContext,
  type ModelResolutionResult,
  type ModelValidationResult,
} from './modelResolver';
