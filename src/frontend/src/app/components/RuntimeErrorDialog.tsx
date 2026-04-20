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
      <AlertDialogContent className="border border-red-500/30 bg-[#1f2125] max-w-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-red-200">
            {issue?.title || '运行异常'}
          </AlertDialogTitle>
          <AlertDialogDescription className="text-gray-300 whitespace-pre-wrap">
            {detail}
          </AlertDialogDescription>
        </AlertDialogHeader>

        {code ? (
          <div className="rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-100">
            错误码: {code}
          </div>
        ) : null}

        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => onOpenChange(false)}>关闭</AlertDialogCancel>
          {onOpenLogs ? (
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                onOpenLogs();
              }}
              className="bg-red-500 text-white hover:bg-red-400"
            >
              查看日志
            </AlertDialogAction>
          ) : null}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
