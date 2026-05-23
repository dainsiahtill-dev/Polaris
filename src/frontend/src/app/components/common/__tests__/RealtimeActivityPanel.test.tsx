import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RealtimeActivityPanel } from '../RealtimeActivityPanel';
import type { LogEntry } from '@/types/log';

describe('RealtimeActivityPanel', () => {
  it('preserves existing execution log source labels', () => {
    const logs: LogEntry[] = [
      {
        id: 'factory-1',
        timestamp: '2026-05-23T00:00:00Z',
        level: 'thinking',
        source: 'FACTORY',
        message: 'Factory audit event',
      },
    ];

    render(<RealtimeActivityPanel executionLogs={logs} role="chief_engineer" />);

    expect(screen.getByText('FACTORY')).toBeInTheDocument();
    expect(screen.queryByText('EXEC')).not.toBeInTheDocument();
  });

  it('uses EXEC as the source label when execution logs do not provide one', () => {
    const logs: LogEntry[] = [
      {
        id: 'exec-1',
        timestamp: '2026-05-23T00:00:00Z',
        level: 'thinking',
        message: 'Execution event',
      },
    ];

    render(<RealtimeActivityPanel executionLogs={logs} role="director" />);

    expect(screen.getByText('EXEC')).toBeInTheDocument();
  });
});
