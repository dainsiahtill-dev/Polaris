/**
 * Export Utilities for Polaris Logs
 *
 * Provides utilities for exporting log data in various formats (JSON, CSV, PDF).
 * All functions are pure and side-effect free.
 */

export interface LogEntry {
  timestamp: string;
  level: 'debug' | 'info' | 'warn' | 'error' | 'critical';
  message: string;
  source?: string;
  metadata?: Record<string, unknown>;
}

export type ExportFormat = 'json' | 'csv' | 'pdf';

export interface ExportOptions {
  filename?: string;
  maxRows?: number;
  includeMetadata?: boolean;
}

/**
 * Download content as a file in the browser
 */
export function downloadFile(content: string, downloadFilename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = downloadFilename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Convert logs to JSON string
 */
export function logsToJSON(logs: LogEntry[], options?: ExportOptions): string {
  const maxRows = options?.maxRows ?? logs.length;
  const data = logs.slice(0, maxRows).map(log => {
    const entry: Record<string, unknown> = {
      timestamp: log.timestamp,
      level: log.level,
      message: log.message,
    };
    if (log.source) entry.source = log.source;
    if (options?.includeMetadata && log.metadata) entry.metadata = log.metadata;
    return entry;
  });
  return JSON.stringify(data, null, 2);
}

/**
 * Escape CSV field value
 */
function escapeCSVField(value: string): string {
  // If value contains comma, double quote, or newline, wrap in quotes and escape existing quotes
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

/**
 * Convert logs to CSV string
 */
export function logsToCSV(logs: LogEntry[], options?: ExportOptions): string {
  const maxRows = options?.maxRows ?? logs.length;
  const headers = ['Timestamp', 'Level', 'Message', 'Source'];
  const rows = logs.slice(0, maxRows).map(log => [
    escapeCSVField(log.timestamp),
    escapeCSVField(log.level),
    escapeCSVField(log.message),
    escapeCSVField(log.source || ''),
  ]);
  return [headers.join(','), ...rows.map(row => row.join(','))].join('\n');
}

/**
 * Convert raw log lines to LogEntry format
 * Supports common log formats like:
 * - [TIMESTAMP] [LEVEL] MESSAGE
 * - TIMESTAMP LEVEL MESSAGE
 */
export function parseLogLine(line: string): LogEntry {
  // Try standard format: [2024-01-01 12:00:00] [INFO] Message
  const standardMatch = line.match(/^\[?(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]?\s*\[?(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\]?\s*(.+)$/i);
  if (standardMatch) {
    const [, timestamp, level, message] = standardMatch;
    return {
      timestamp,
      level: normalizeLogLevel(level),
      message: message.trim(),
    };
  }

  // Fallback: treat entire line as message
  return {
    timestamp: new Date().toISOString(),
    level: 'info',
    message: line,
  };
}

/**
 * Normalize log level strings to standard format
 */
export function normalizeLogLevel(level: string): LogEntry['level'] {
  const upper = level.toUpperCase();
  if (upper === 'WARNING') return 'warn';
  if (upper === 'FATAL' || upper === 'CRITICAL') return 'critical';
  if (['DEBUG', 'INFO', 'WARN', 'ERROR'].includes(upper)) {
    return upper.toLowerCase() as LogEntry['level'];
  }
  return 'info';
}

/**
 * Convert raw string lines to LogEntry array
 */
export function parseLogLines(lines: string[]): LogEntry[] {
  return lines.filter(Boolean).map(parseLogLine);
}

/**
 * Generate timestamped filename with format suffix
 */
export function generateExportFilename(base: string, format: ExportFormat): string {
  const timestamp = new Date().toISOString().slice(0, 19).replace(/[:-]/g, '');
  return `${base}-${timestamp}.${format}`;
}

/**
 * Check if log data is too large for PDF (which has page limits)
 */
export function isLargeDataset(logs: LogEntry[]): boolean {
  return logs.length > 50;
}

/**
 * Sample logs for large datasets (when user wants to include all, this can be ignored)
 */
export function sampleLogs(logs: LogEntry[], maxCount: number = 50): LogEntry[] {
  if (logs.length <= maxCount) return logs;
  const step = Math.ceil(logs.length / maxCount);
  return logs.filter((_, index) => index % step === 0).slice(0, maxCount);
}
