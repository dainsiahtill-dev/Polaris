/**
 * Tests for LogExporter Component
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { LogExporter } from './LogExporter';
import type { LogEntry } from '@/app/utils/exportUtils';

// Mock dependencies
vi.mock('@/app/components/ui/button', () => ({
  Button: ({ children, ...props }: React.ComponentProps<'button'>) => (
    <button data-testid="button" {...props}>{children}</button>
  ),
}));

vi.mock('@/app/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dropdown-menu">{children}</div>
  ),
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dropdown-content">{children}</div>
  ),
  DropdownMenuItem: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => (
    <button data-testid="dropdown-item" onClick={onClick}>{children}</button>
  ),
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dropdown-trigger">{children}</div>
  ),
}));

vi.mock('lucide-react', () => ({
  Download: () => <span data-testid="icon-download" />,
  FileJson: () => <span data-testid="icon-json" />,
  FileSpreadsheet: () => <span data-testid="icon-csv" />,
  FileText: () => <span data-testid="icon-pdf" />,
}));

const sampleLogs: LogEntry[] = [
  { timestamp: '2024-01-01 12:00:00', level: 'info', message: 'Test message 1' },
  { timestamp: '2024-01-01 12:01:00', level: 'error', message: 'Test message 2' },
];

describe('LogExporter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should render export button', () => {
    render(<LogExporter logs={sampleLogs} />);
    expect(screen.getByText('导出')).toBeInTheDocument();
  });

  it('should be disabled when no logs', () => {
    render(<LogExporter logs={[]} />);
    const button = screen.getByTestId('button');
    expect(button).toBeDisabled();
  });

  it('should show all export options', () => {
    render(<LogExporter logs={sampleLogs} />);

    // Click the trigger to open dropdown
    const trigger = screen.getByTestId('dropdown-trigger');
    fireEvent.click(trigger);

    expect(screen.getByText('JSON 格式')).toBeInTheDocument();
    expect(screen.getByText('CSV 表格')).toBeInTheDocument();
    expect(screen.getByText('PDF 报告')).toBeInTheDocument();
  });

  it('should call onExportSuccess callback', async () => {
    const onSuccess = vi.fn();
    render(<LogExporter logs={sampleLogs} onExportSuccess={onSuccess} />);

    const trigger = screen.getByTestId('dropdown-trigger');
    fireEvent.click(trigger);

    const jsonItem = screen.getByText('JSON 格式');
    fireEvent.click(jsonItem);

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledWith('json');
    });
  });

  it('should use custom filename', () => {
    render(<LogExporter logs={sampleLogs} filename="custom-logs" />);
    expect(screen.getByText('导出')).toBeInTheDocument();
  });
});
