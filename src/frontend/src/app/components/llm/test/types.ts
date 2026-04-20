export type TestStatus = 'idle' | 'running' | 'success' | 'failed' | 'cancelled';

export type TestLogType = 'info' | 'error' | 'success' | 'request' | 'response';

export interface TestLog {
  id: string;
  timestamp: string;
  type: TestLogType;
  message: string;
  details?: unknown;
}

export type TestEventType = 'command' | 'stdout' | 'stderr' | 'response' | 'result' | 'error';

export interface TestEvent {
  type: TestEventType;
  timestamp: string;
  content: string;
  details?: unknown;
}

export interface TestStep {
  key: string;
  label: string;
}

export interface TestTarget {
  providerId: string;
  providerName: string;
  model?: string;
  level?: 'quick' | 'deep';
  role?: string;
}

export interface TestSuiteSummary {
  name: string;
  ok: boolean;
  note?: string;
}

export interface TestUsageSummary {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  estimated?: boolean;
}

export interface TestResult {
  report?: Record<string, unknown>;
  runId?: string;
  ready?: boolean;
  grade?: string;
  latencyMs?: number;
  usage?: TestUsageSummary;
  suites?: TestSuiteSummary[];
  thinking?: {
    supportsThinking?: boolean;
    confidence?: number;
    format?: string;
  };
}

export interface TestState {
  status: TestStatus;
  progress: number;
  currentStep?: string;
  logs: TestLog[];
  result?: TestResult;
  error?: string;
  target?: TestTarget;
  runId?: string;
  startedAt?: string;
  finishedAt?: string;
}
