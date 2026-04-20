/**
 * Tests for Export Utilities
 */

import { describe, it, expect } from 'vitest';
import {
  downloadFile,
  logsToJSON,
  logsToCSV,
  parseLogLine,
  normalizeLogLevel,
  parseLogLines,
  generateExportFilename,
  isLargeDataset,
  sampleLogs,
  type LogEntry,
} from './exportUtils';

// Mock URL.createObjectURL and related APIs
const mockClick = vi.fn();
const mockRevokeObjectURL = vi.fn();

vi.stubGlobal('URL', {
  createObjectURL: vi.fn(() => 'blob:test'),
  revokeObjectURL: mockRevokeObjectURL,
});

vi.stubGlobal('document', {
  body: {
    appendChild: vi.fn(),
    removeChild: vi.fn(),
  },
  createElement: vi.fn(() => ({
    href: '',
    download: '',
    click: mockClick,
  })),
});

describe('downloadFile', () => {
  it('should create a blob and trigger download', () => {
    const content = 'test content';
    const filename = 'test.txt';
    const mimeType = 'text/plain';

    downloadFile(content, filename, mimeType);

    expect(mockClick).toHaveBeenCalled();
    expect(mockRevokeObjectURL).toHaveBeenCalledWith('blob:test');
  });
});

describe('logsToJSON', () => {
  const sampleLogs: LogEntry[] = [
    { timestamp: '2024-01-01 12:00:00', level: 'info', message: 'Test message' },
    { timestamp: '2024-01-01 12:01:00', level: 'error', message: 'Error occurred', source: 'test' },
  ];

  it('should convert logs to JSON string', () => {
    const result = logsToJSON(sampleLogs);
    const parsed = JSON.parse(result);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].timestamp).toBe('2024-01-01 12:00:00');
    expect(parsed[0].level).toBe('info');
  });

  it('should respect maxRows option', () => {
    const result = logsToJSON(sampleLogs, { maxRows: 1 });
    const parsed = JSON.parse(result);
    expect(parsed).toHaveLength(1);
  });

  it('should include metadata when option is set', () => {
    const logsWithMetadata: LogEntry[] = [
      { timestamp: '2024-01-01 12:00:00', level: 'info', message: 'Test', metadata: { key: 'value' } },
    ];
    const result = logsToJSON(logsWithMetadata, { includeMetadata: true });
    const parsed = JSON.parse(result);
    expect(parsed[0].metadata).toEqual({ key: 'value' });
  });
});

describe('logsToCSV', () => {
  const sampleLogs: LogEntry[] = [
    { timestamp: '2024-01-01 12:00:00', level: 'info', message: 'Simple message' },
    { timestamp: '2024-01-01 12:01:00', level: 'error', message: 'Message with "quotes"', source: 'test' },
  ];

  it('should convert logs to CSV string', () => {
    const result = logsToCSV(sampleLogs);
    const lines = result.split('\n');
    expect(lines[0]).toBe('Timestamp,Level,Message,Source');
    expect(lines[1]).toContain('2024-01-01 12:00:00');
    expect(lines[1]).toContain('info');
  });

  it('should escape quotes in messages', () => {
    const result = logsToCSV(sampleLogs);
    const lines = result.split('\n');
    expect(lines[2]).toContain('"Message with ""quotes"""');
  });

  it('should respect maxRows option', () => {
    const result = logsToCSV(sampleLogs, { maxRows: 1 });
    const lines = result.split('\n');
    expect(lines).toHaveLength(2); // Header + 1 row
  });
});

describe('parseLogLine', () => {
  it('should parse standard log format with brackets', () => {
    const line = '[2024-01-01 12:00:00] [INFO] Test message';
    const result = parseLogLine(line);
    expect(result.timestamp).toBe('2024-01-01 12:00:00');
    expect(result.level).toBe('info');
    expect(result.message).toBe('Test message');
  });

  it('should parse log format without brackets', () => {
    const line = '2024-01-01T12:00:00 ERROR Error message';
    const result = parseLogLine(line);
    expect(result.level).toBe('error');
    expect(result.message).toBe('Error message');
  });

  it('should handle unrecognized format', () => {
    const line = 'Random text without timestamp';
    const result = parseLogLine(line);
    expect(result.message).toBe('Random text without timestamp');
    expect(result.level).toBe('info');
  });
});

describe('normalizeLogLevel', () => {
  it('should normalize common levels', () => {
    expect(normalizeLogLevel('INFO')).toBe('info');
    expect(normalizeLogLevel('error')).toBe('error');
    expect(normalizeLogLevel('WARN')).toBe('warn');
  });

  it('should convert WARNING to warn', () => {
    expect(normalizeLogLevel('WARNING')).toBe('warn');
  });

  it('should convert FATAL and CRITICAL to critical', () => {
    expect(normalizeLogLevel('FATAL')).toBe('critical');
    expect(normalizeLogLevel('CRITICAL')).toBe('critical');
  });
});

describe('parseLogLines', () => {
  it('should parse multiple lines', () => {
    const lines = [
      '[2024-01-01 12:00:00] [INFO] First message',
      '[2024-01-01 12:01:00] [ERROR] Second message',
      '',
    ];
    const result = parseLogLines(lines);
    expect(result).toHaveLength(2);
    expect(result[0].message).toBe('First message');
    expect(result[1].level).toBe('error');
  });
});

describe('generateExportFilename', () => {
  it('should generate filename with timestamp', () => {
    const result = generateExportFilename('test', 'json');
    expect(result).toMatch(/^test-\d{8}T\d{6}\.json$/);
  });

  it('should use correct extension for each format', () => {
    expect(generateExportFilename('logs', 'csv')).toMatch(/\.csv$/);
    expect(generateExportFilename('logs', 'pdf')).toMatch(/\.pdf$/);
  });
});

describe('isLargeDataset', () => {
  it('should identify datasets over 50 entries', () => {
    expect(isLargeDataset(Array(51).fill({ timestamp: '', level: 'info', message: '' }))).toBe(true);
    expect(isLargeDataset(Array(50).fill({ timestamp: '', level: 'info', message: '' }))).toBe(false);
  });
});

describe('sampleLogs', () => {
  it('should return all logs if under limit', () => {
    const logs = Array(10).fill({ timestamp: '2024-01-01', level: 'info' as const, message: 'test' });
    const result = sampleLogs(logs, 50);
    expect(result).toHaveLength(10);
  });

  it('should sample logs if over limit', () => {
    const logs = Array(100).fill({ timestamp: '2024-01-01', level: 'info' as const, message: 'test' });
    const result = sampleLogs(logs, 50);
    expect(result.length).toBeLessThanOrEqual(50);
  });
});
