/**
 * LogExporter Component
 *
 * Provides export functionality for log data in JSON, CSV, and PDF formats.
 * Uses dynamic imports to reduce initial bundle size.
 */

import { useCallback } from 'react';
import { Download, FileJson, FileSpreadsheet, FileText } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/app/components/ui/dropdown-menu';
import {
  downloadFile,
  logsToJSON,
  logsToCSV,
  generateExportFilename,
  sampleLogs,
  isLargeDataset,
  type LogEntry,
  type ExportFormat,
} from '@/app/utils/exportUtils';

interface LogExporterProps {
  /** Log entries to export */
  logs: LogEntry[];
  /** Base filename without extension */
  filename?: string;
  /** Callback after successful export */
  onExportSuccess?: (format: ExportFormat) => void;
  /** Callback on export error */
  onExportError?: (format: ExportFormat, error: Error) => void;
}

const PDF_ROW_LIMIT = 50;

export function LogExporter({
  logs,
  filename = 'polaris-logs',
  onExportSuccess,
  onExportError,
}: LogExporterProps): React.JSX.Element {
  const exportAsJSON = useCallback(() => {
    try {
      const json = logsToJSON(logs);
      const exportFilename = generateExportFilename(filename, 'json');
      downloadFile(json, exportFilename, 'application/json');
      onExportSuccess?.('json');
    } catch (error) {
      const err = error instanceof Error ? error : new Error('JSON export failed');
      onExportError?.('json', err);
    }
  }, [logs, filename, onExportSuccess, onExportError]);

  const exportAsCSV = useCallback(() => {
    try {
      const csv = logsToCSV(logs);
      const exportFilename = generateExportFilename(filename, 'csv');
      downloadFile(csv, exportFilename, 'text/csv');
      onExportSuccess?.('csv');
    } catch (error) {
      const err = error instanceof Error ? error : new Error('CSV export failed');
      onExportError?.('csv', err);
    }
  }, [logs, filename, onExportSuccess, onExportError]);

  const exportAsPDF = useCallback(async () => {
    try {
      // Dynamic import to reduce initial bundle size
      const { default: jsPDF } = await import('jspdf');
      const doc = new jsPDF();

      // Title
      doc.setFontSize(16);
      doc.text('Polaris Logs', 10, 15);

      // Metadata
      doc.setFontSize(10);
      doc.text(`Exported: ${new Date().toLocaleString()}`, 10, 22);
      doc.text(`Total entries: ${logs.length}`, 10, 28);

      // Determine entries to include
      const displayLogs = isLargeDataset(logs)
        ? sampleLogs(logs, PDF_ROW_LIMIT)
        : logs;

      if (displayLogs.length < logs.length) {
        doc.text(`(Showing ${displayLogs.length} of ${logs.length} entries due to PDF page limits)`, 10, 34);
      }

      // Table header
      let y = 42;
      doc.setFontSize(8);
      doc.setFont('helvetica', 'bold');
      doc.text('Timestamp', 10, y);
      doc.text('Level', 70, y);
      doc.text('Message', 90, y);

      // Separator line
      y += 2;
      doc.line(10, y, 200, y);
      y += 4;

      // Log entries
      doc.setFont('helvetica', 'normal');
      for (const log of displayLogs) {
        if (y > 280) break; // Page boundary

        const level = log.level.toUpperCase().padEnd(7);
        const message = truncateText(log.message, 60);

        doc.text(log.timestamp.slice(0, 19), 10, y);
        doc.text(level, 70, y);
        doc.text(message, 90, y);

        y += 5;
      }

      const exportFilename = generateExportFilename(filename, 'pdf');
      doc.save(exportFilename);
      onExportSuccess?.('pdf');
    } catch (error) {
      const err = error instanceof Error ? error : new Error('PDF export failed');
      onExportError?.('pdf', err);
    }
  }, [logs, filename, onExportSuccess, onExportError]);

  const handleExport = useCallback(
    async (format: ExportFormat) => {
      switch (format) {
        case 'json':
          exportAsJSON();
          break;
        case 'csv':
          exportAsCSV();
          break;
        case 'pdf':
          await exportAsPDF();
          break;
      }
    },
    [exportAsJSON, exportAsCSV, exportAsPDF],
  );

  const hasLogs = logs.length > 0;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" disabled={!hasLogs}>
          <Download className="mr-2 h-4 w-4" />
          导出
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => handleExport('json')}>
          <FileJson className="mr-2 h-4 w-4" />
          JSON 格式
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleExport('csv')}>
          <FileSpreadsheet className="mr-2 h-4 w-4" />
          CSV 表格
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleExport('pdf')}>
          <FileText className="mr-2 h-4 w-4" />
          PDF 报告
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Truncate text to specified length with ellipsis
 */
function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 3)}...`;
}

export default LogExporter;
