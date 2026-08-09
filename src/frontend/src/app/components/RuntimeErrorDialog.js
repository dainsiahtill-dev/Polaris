import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, } from '@/app/components/ui/alert-dialog';
export function RuntimeErrorDialog({ open, issue, onOpenChange, onOpenLogs, onDismiss, }) {
    const code = String(issue?.code || '').trim();
    const rawDetail = String(issue?.detail || '').trim();
    const detail = rawDetail
        ? rawDetail
            .split(/\r?\n/)
            .filter((line) => {
            const normalized = line.trim().toLowerCase();
            if (!normalized || !code)
                return true;
            if (!normalized.startsWith('错误码'))
                return true;
            return !normalized.includes(code.toLowerCase());
        })
            .join('\n')
            .trim() || rawDetail
        : '请查看日志定位问题。';
    return (_jsx(AlertDialog, { open: open, onOpenChange: (nextOpen) => {
            onOpenChange(nextOpen);
            if (!nextOpen)
                onDismiss?.();
        }, children: _jsxs(AlertDialogContent, { "data-testid": "runtime-error-dialog", className: "soft-panel grid max-h-[88vh] min-w-0 max-w-2xl grid-rows-[auto_auto_auto] overflow-hidden border-red-500/30", children: [_jsxs(AlertDialogHeader, { className: "shrink-0", children: [_jsx(AlertDialogTitle, { className: "break-words text-status-error", children: issue?.title || '运行异常' }), _jsx(AlertDialogDescription, { className: "max-h-[48vh] overflow-y-auto whitespace-pre-wrap break-words pr-1 text-text-muted", children: detail })] }), code ? (_jsxs("div", { className: "break-all rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-status-error", children: ["\u9519\u8BEF\u7801: ", code] })) : null, _jsxs(AlertDialogFooter, { "data-testid": "runtime-error-footer", className: "shrink-0 flex-wrap", children: [_jsx(AlertDialogCancel, { className: "whitespace-nowrap", onClick: () => onOpenChange(false), children: "\u5173\u95ED" }), onOpenLogs ? (_jsx(AlertDialogAction, { onClick: (event) => {
                                event.preventDefault();
                                onOpenLogs();
                            }, className: "whitespace-nowrap bg-red-500 text-white hover:bg-red-400", children: "\u67E5\u770B\u65E5\u5FD7" })) : null] })] }) }));
}
