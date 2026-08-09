import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * LogExporter Component
 *
 * Provides export functionality for log data in JSON, CSV, and PDF formats.
 * Uses dynamic imports to reduce initial bundle size.
 */
import { useCallback } from 'react';
import { Download, FileJson, FileSpreadsheet, FileText } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, } from '@/app/components/ui/dropdown-menu';
import { downloadFile, logsToJSON, logsToCSV, generateExportFilename, sampleLogs, isLargeDataset, } from '@/app/utils/exportUtils';
const PDF_ROW_LIMIT = 50;
export function LogExporter({ logs, filename = 'polaris-logs', onExportSuccess, onExportError, }) {
    const exportAsJSON = useCallback(() => {
        try {
            const json = logsToJSON(logs);
            const exportFilename = generateExportFilename(filename, 'json');
            downloadFile(json, exportFilename, 'application/json');
            onExportSuccess?.('json');
        }
        catch (error) {
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
        }
        catch (error) {
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
                if (y > 280)
                    break; // Page boundary
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
        }
        catch (error) {
            const err = error instanceof Error ? error : new Error('PDF export failed');
            onExportError?.('pdf', err);
        }
    }, [logs, filename, onExportSuccess, onExportError]);
    const handleExport = useCallback(async (format) => {
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
    }, [exportAsJSON, exportAsCSV, exportAsPDF]);
    const hasLogs = logs.length > 0;
    return (_jsxs(DropdownMenu, { children: [_jsx(DropdownMenuTrigger, { asChild: true, children: _jsxs(Button, { variant: "outline", size: "sm", disabled: !hasLogs, children: [_jsx(Download, { className: "mr-2 h-4 w-4" }), "\u5BFC\u51FA"] }) }), _jsxs(DropdownMenuContent, { align: "end", children: [_jsxs(DropdownMenuItem, { onClick: () => handleExport('json'), children: [_jsx(FileJson, { className: "mr-2 h-4 w-4" }), "JSON \u683C\u5F0F"] }), _jsxs(DropdownMenuItem, { onClick: () => handleExport('csv'), children: [_jsx(FileSpreadsheet, { className: "mr-2 h-4 w-4" }), "CSV \u8868\u683C"] }), _jsxs(DropdownMenuItem, { onClick: () => handleExport('pdf'), children: [_jsx(FileText, { className: "mr-2 h-4 w-4" }), "PDF \u62A5\u544A"] })] })] }));
}
/**
 * Truncate text to specified length with ellipsis
 */
function truncateText(text, maxLength) {
    if (text.length <= maxLength)
        return text;
    return `${text.slice(0, maxLength - 3)}...`;
}
export default LogExporter;
