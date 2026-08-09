import { useCallback, useEffect, useRef, useState } from 'react';
import { devLogger } from '@/app/utils/devLogger';
import { runStreamingTest } from '../streamingTest';
export function useTestStream(options = {}) {
    const { onEvent, onSuiteStart, onSuiteComplete, onComplete, onError } = options;
    const [isStreaming, setIsStreaming] = useState(false);
    const abortControllerRef = useRef(null);
    const isStreamingRef = useRef(false);
    const startStream = useCallback(async (payload) => {
        if (isStreamingRef.current) {
            devLogger.debug('[useTestStream] Already streaming, ignoring');
            return;
        }
        devLogger.debug('[useTestStream] Starting Nats-JetStream test', payload);
        isStreamingRef.current = true;
        setIsStreaming(true);
        abortControllerRef.current = new AbortController();
        onEvent?.({
            type: 'stdout',
            timestamp: new Date().toISOString(),
            content: '正在建立 Nats-JetStream 测试通道...',
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
                onComplete: (report) => onComplete?.(report),
                onError,
            });
        }
        finally {
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
