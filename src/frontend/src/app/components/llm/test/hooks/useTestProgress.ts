import { useMemo, useRef, useState } from 'react';
import type { TestLog, TestLogType, TestState, TestStep, TestTarget, TestResult } from '../types';

const DEFAULT_TEST_STEPS: TestStep[] = [
  { key: 'init', label: '初始化连接' },
  { key: 'validate', label: '验证配置' },
  { key: 'request', label: '发送测试请求' },
  { key: 'response', label: '等待响应' },
  { key: 'analyze', label: '分析结果' }
];

const MAX_LOGS = 200;

const progressForStep = (steps: TestStep[], key?: string) => {
  if (!key) return 0;
  const idx = steps.findIndex((step) => step.key === key);
  if (idx < 0) return 0;
  const denom = Math.max(steps.length - 1, 1);
  return Math.round((idx / denom) * 100);
};

const buildLog = (type: TestLogType, message: string, details?: unknown): TestLog => {
  const stamp = new Date();
  return {
    id: `${stamp.getTime()}-${Math.random().toString(16).slice(2)}`,
    timestamp: stamp.toISOString(),
    type,
    message,
    details
  };
};

const appendLog = (prev: TestState, log: TestLog): TestLog[] => {
  const logs = [...prev.logs, log];
  if (logs.length <= MAX_LOGS) return logs;
  return logs.slice(logs.length - MAX_LOGS);
};

export const useTestProgress = (steps: TestStep[] = DEFAULT_TEST_STEPS) => {
  const [state, setState] = useState<TestState>({
    status: 'idle',
    progress: 0,
    logs: []
  });
  const controllerRef = useRef<AbortController | null>(null);

  const resolvedSteps = useMemo(() => (steps.length > 0 ? steps : DEFAULT_TEST_STEPS), [steps]);

  const reset = () => {
    controllerRef.current = null;
    setState({ status: 'idle', progress: 0, logs: [] });
  };

  const start = (target?: TestTarget) => {
    const controller = new AbortController();
    controllerRef.current = controller;
    const initialStep = resolvedSteps[0];
    const log = buildLog('info', '测试已启动');
    setState({
      status: 'running',
      progress: initialStep ? progressForStep(resolvedSteps, initialStep.key) : 0,
      currentStep: initialStep?.label,
      logs: [log],
      result: undefined,
      error: undefined,
      target,
      runId: undefined,
      startedAt: new Date().toISOString(),
      finishedAt: undefined
    });
    return controller;
  };

  const setStep = (stepKey: string, message?: string) => {
    const step = resolvedSteps.find((item) => item.key === stepKey);
    setState((prev) => {
      const log = message ? buildLog('info', message) : null;
      return {
        ...prev,
        currentStep: step?.label || prev.currentStep,
        progress: progressForStep(resolvedSteps, stepKey),
        logs: log ? appendLog(prev, log) : prev.logs
      };
    });
  };

  const addLog = (type: TestLogType, message: string, details?: unknown) => {
    const log = buildLog(type, message, details);
    setState((prev) => ({
      ...prev,
      logs: appendLog(prev, log)
    }));
  };

  const setRunId = (runId?: string) => {
    setState((prev) => ({ ...prev, runId }));
  };

  const complete = (result?: TestResult) => {
    const log = buildLog('success', '测试完成');
    setState((prev) => ({
      ...prev,
      status: 'success',
      progress: 100,
      currentStep: '完成',
      result: result ?? prev.result,
      finishedAt: new Date().toISOString(),
      logs: appendLog(prev, log)
    }));
  };

  const fail = (error: string, result?: TestResult) => {
    const log = buildLog('error', error);
    setState((prev) => ({
      ...prev,
      status: 'failed',
      progress: prev.progress || 100,
      currentStep: '失败',
      error,
      result: result ?? prev.result,
      finishedAt: new Date().toISOString(),
      logs: appendLog(prev, log)
    }));
  };

  const cancel = () => {
    controllerRef.current?.abort();
    const log = buildLog('info', '测试已取消');
    setState((prev) => ({
      ...prev,
      status: 'cancelled',
      currentStep: '已取消',
      finishedAt: new Date().toISOString(),
      logs: appendLog(prev, log)
    }));
  };

  return {
    state,
    steps: resolvedSteps,
    start,
    setStep,
    addLog,
    setRunId,
    complete,
    fail,
    cancel,
    reset,
    controllerRef
  };
};
