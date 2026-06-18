import { useCallback, useEffect, useRef, useState } from 'react';
import type { TestEvent } from '../../test/types';
import { devLogger } from '@/app/utils/devLogger';
import { runStreamingTest } from '../streamingTest';

export interface TestCompleteEvent {
  schema_version: number;
  test_run_id: string;
  timestamp: string;
  target: {
    role: string;
    provider_id: string;
    model: string;
  };
  suites: Record<string, unknown>;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    estimated: boolean;
  };
  final: {
    ready: boolean;
    grade: string;
    next_action: string;
  };
}

export interface UseTestStreamOptions {
  onEvent?: (event: TestEvent) => void;
  onSuiteStart?: (suite: string) => void;
  onSuiteComplete?: (suite: string, result: { ok: boolean }) => void;
  onComplete?: (report: TestCompleteEvent) => void;
  onError?: (error: string) => void;
}

// Extended payload for Scheme B (connectivity-only with direct config)
export interface TestStreamPayload {
  role: string;
  providerId: string;
  model: string;
  suites?: string[];
  testLevel?: string;
  evaluationMode?: string;
  apiKey?: string | null;
  envOverrides?: Record<string, string>;
  // Scheme B: direct config fields for connectivity-only tests
  providerType?: string;
  baseUrl?: string;
  apiPath?: string;
  timeout?: number;
}

export function useTestStream(options: UseTestStreamOptions = {}) {
  const { onEvent, onSuiteStart, onSuiteComplete, onComplete, onError } = options;
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const isStreamingRef = useRef(false);

  const startStream = useCallback(async (payload: TestStreamPayload) => {
    if (isStreamingRef.current) {
      devLogger.debug('[useTestStream] Already streaming, ignoring');
      return;
    }

    devLogger.debug('[useTestStream] Starting Nat-JetStream test', payload);
    isStreamingRef.current = true;
    setIsStreaming(true);

    abortControllerRef.current = new AbortController();

    onEvent?.({
      type: 'stdout',
      timestamp: new Date().toISOString(),
      content: '正在建立 Nat-JetStream 测试通道...',
    });

    try {
      await runStreamingTest({
        role: payload.role,
        providerId: payload.providerId,
        model: payload.model,
        suites: payload.suites || ['connectivity', 'response'],
        testLevel: payload.testLevel || 'quick',
        evaluationMode: payload.evaluationMode || 'provider',
        apiKey: payload.apiKey,
        envOverrides: payload.envOverrides,
        providerType: payload.providerType,
        baseUrl: payload.baseUrl,
        apiPath: payload.apiPath,
        timeout: payload.timeout,
        signal: abortControllerRef.current.signal,
        onEvent,
        onSuiteStart,
        onSuiteComplete: (suite, ok) => onSuiteComplete?.(suite, { ok }),
        onComplete: (report) => onComplete?.(report as unknown as TestCompleteEvent),
        onError,
      });
    } finally {
      isStreamingRef.current = false;
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  }, [onComplete, onError, onEvent, onSuiteComplete, onSuiteStart]);

  const stopStream = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    isStreamingRef.current = false;
    setIsStreaming(false);
  }, []);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  return {
    isStreaming,
    startStream,
    stopStream,
  };
}
