export interface Notification {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info' | 'loading';
  title?: string;
  message: string;
  duration?: number;
  actions?: Array<{ label: string; onClick: () => void }>;
  progress?: boolean;
  persist?: boolean;
}

export interface FileInfo {
  id: string;
  name: string;
  path: string;
}

export interface FileData {
  content: string;
  mtime: string;
}

export interface FileBadge {
  text: string;
  tone: 'green' | 'yellow' | 'red';
}

export interface UsageStats {
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  estimated?: boolean;
}

export interface DirectorRunningState {
  running: boolean;
  pid?: number | null;
  started_at?: number | null;
}

export function resolveRunning(status: { running?: boolean; status?: unknown } | null): boolean {
  if (!status) return false;
  if (status.running) return true;
  const nested = status.status;
  if (!nested || typeof nested !== 'object') return false;
  const raw = (nested as Record<string, unknown>).running;
  if (typeof raw === 'boolean') return raw;
  if (typeof raw === 'number') return raw !== 0;
  if (typeof raw === 'string') {
    return ['1', 'true', 'yes', 'on', 'running'].includes(raw.trim().toLowerCase());
  }
  return false;
}
