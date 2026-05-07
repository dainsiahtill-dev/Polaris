export type RuntimeDiagnosticSeverity = 'ok' | 'warning' | 'error' | 'unknown';

export interface RuntimeDiagnosticEvent {
  timestamp?: string | null;
  state?: string | null;
  status?: string | null;
  phase?: string | null;
  message?: string | null;
  detail?: string | null;
}

export interface RuntimeDiagnosticsSection {
  ok?: boolean | null;
  enabled?: boolean | null;
  required?: boolean | null;
  status?: string | null;
  state?: string | null;
  connected?: boolean | null;
  running?: boolean | null;
  reconnecting?: boolean | null;
  attempt_count?: number | null;
  reconnect_attempts?: number | null;
  retry_after_ms?: number | null;
  retry_after_sec?: number | null;
  remaining?: number | null;
  limit?: number | null;
  reset_at?: string | null;
  last_error?: string | null;
  error?: string | null;
  lifecycle?: RuntimeDiagnosticEvent[] | Record<string, unknown> | null;
  events?: RuntimeDiagnosticEvent[] | null;
  buckets?: RuntimeDiagnosticsSection[] | Record<string, RuntimeDiagnosticsSection> | null;
  [key: string]: unknown;
}

export interface RuntimeDiagnosticsPayload {
  ok?: boolean | null;
  timestamp?: string | null;
  generated_at?: string | null;
  nats?: RuntimeDiagnosticsSection | null;
  websocket?: RuntimeDiagnosticsSection | null;
  web_socket?: RuntimeDiagnosticsSection | null;
  runtime_v2?: RuntimeDiagnosticsSection | null;
  rate_limit?: RuntimeDiagnosticsSection | null;
  rate_limits?: RuntimeDiagnosticsSection | null;
  issues?: RuntimeDiagnosticEvent[] | null;
  [key: string]: unknown;
}

export interface RuntimeDiagnosticsConnectionState {
  live: boolean;
  reconnecting: boolean;
  attemptCount: number;
}
