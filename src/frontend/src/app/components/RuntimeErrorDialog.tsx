import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/app/components/ui/alert-dialog';
import type { RuntimeIssue } from '@/app/types/appContracts';

interface RuntimeErrorDialogProps {
  open: boolean;
  issue: RuntimeIssue | null;
  onOpenChange: (open: boolean) => void;
  onOpenLogs?: () => void;
  onDismiss?: () => void;
}

export function RuntimeErrorDialog({
  open,
  issue,
  onOpenChange,
  onOpenLogs,
  onDismiss,
}: RuntimeErrorDialogProps) {
  const code = String(issue?.code || '').trim();
  const rawDetail = String(issue?.detail || '').trim();
  const detail = rawDetail
    ? rawDetail
        .split(/\r?\n/)
        .filter((line) => {
          const normalized = line.trim().toLowerCase();
          if (!normalized || !code) return true;
          if (!normalized.startsWith('错误码')) return true;
          return !normalized.includes(code.toLowerCase());
        })
        .join('\n')
        .trim() || rawDetail
    : '请查看日志定位问题。';

  return (
    <AlertDialog
      open={open}
      onOpenChange={(nextOpen) => {
        onOpenChange(nextOpen);
        if (!nextOpen) onDismiss?.();
      }}
    >
      <AlertDialogContent
        data-testid="runtime-error-dialog"
        className="soft-panel grid max-h-[88vh] min-w-0 max-w-2xl grid-rows-[auto_auto_auto] overflow-hidden border-red-500/30"
      >
        <AlertDialogHeader className="shrink-0">
          <AlertDialogTitle className="break-words text-status-error">
            {issue?.title || '运行异常'}
          </AlertDialogTitle>
          <AlertDialogDescription className="max-h-[48vh] overflow-y-auto whitespace-pre-wrap break-words pr-1 text-text-muted">
            {detail}
          </AlertDialogDescription>
        </AlertDialogHeader>

        {code ? (
          <div className="break-all rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-status-error">
            错误码: {code}
          </div>
        ) : null}

        <AlertDialogFooter data-testid="runtime-error-footer" className="shrink-0 flex-wrap">
          <AlertDialogCancel className="whitespace-nowrap" onClick={() => onOpenChange(false)}>关闭</AlertDialogCancel>
          {onOpenLogs ? (
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                onOpenLogs();
              }}
              className="whitespace-nowrap bg-red-500 text-white hover:bg-red-400"
            >
              查看日志
            </AlertDialogAction>
          ) : null}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
