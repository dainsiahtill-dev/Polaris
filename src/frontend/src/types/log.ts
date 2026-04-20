// 统一日志类型定义 - 所有组件应从此处导入
// 避免在多处重复定义 LogEntry 接口

export type LogLevel = 'info' | 'success' | 'warning' | 'error' | 'thinking' | 'tool' | 'exec';

export interface LogEntry {
  id: string;
  timestamp: string;
  level: LogLevel;
  source: string;
  message: string;
  details?: string;
  meta?: Record<string, unknown>;
  title?: string;
  tags?: string[];
}
