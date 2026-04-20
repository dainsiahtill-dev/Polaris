import type { ValidationIssue } from '../types/visual';
import { getRoleLabel, getValidationSeverity } from '../utils/validation';
import { AlertTriangle, XCircle, Info } from 'lucide-react';

interface ValidationPanelProps {
  issues: ValidationIssue[];
  onIssueClick?: (issue: ValidationIssue) => void;
  onClose?: () => void;
}

export function ValidationPanel({ issues, onIssueClick, onClose }: ValidationPanelProps) {
  if (issues.length === 0) return null;

  const errors = issues.filter(i => getValidationSeverity(i) === 'error');
  const warnings = issues.filter(i => getValidationSeverity(i) === 'warning');

  return (
    <div className="absolute top-4 right-4 z-50 w-80 rounded-lg border border-rose-500/30 bg-black/90 shadow-lg backdrop-blur-sm">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
        <div className="flex items-center gap-2">
          <AlertTriangle className="size-4 text-rose-400" />
          <span className="text-sm font-medium text-rose-200">
            配置问题 ({issues.length})
          </span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="rounded p-1 text-text-dim hover:bg-white/10 hover:text-text-main"
          >
            <XCircle className="size-4" />
          </button>
        )}
      </div>

      {/* Issues List */}
      <div className="max-h-60 overflow-y-auto p-2">
        {/* Errors */}
        {errors.length > 0 && (
          <div className="mb-2">
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-rose-400">
              错误 ({errors.length})
            </div>
            <ul className="space-y-1">
              {errors.map((issue, idx) => (
                <li
                  key={`error-${idx}`}
                  className="cursor-pointer rounded border border-rose-500/20 bg-rose-500/10 p-2 text-[11px] hover:bg-rose-500/20"
                  onClick={() => onIssueClick?.(issue)}
                >
                  <div className="flex items-start gap-1.5">
                    <XCircle className="mt-0.5 size-3 shrink-0 text-rose-400" />
                    <div>
                      <div className="text-rose-200">{issue.message}</div>
                      {issue.suggestion && (
                        <div className="mt-1 flex items-center gap-1 text-[10px] text-rose-300/70">
                          <Info className="size-3" />
                          {issue.suggestion}
                        </div>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Warnings */}
        {warnings.length > 0 && (
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-amber-400">
              警告 ({warnings.length})
            </div>
            <ul className="space-y-1">
              {warnings.map((issue, idx) => (
                <li
                  key={`warning-${idx}`}
                  className="cursor-pointer rounded border border-amber-500/20 bg-amber-500/10 p-2 text-[11px] hover:bg-amber-500/20"
                  onClick={() => onIssueClick?.(issue)}
                >
                  <div className="flex items-start gap-1.5">
                    <AlertTriangle className="mt-0.5 size-3 shrink-0 text-amber-400" />
                    <div>
                      <div className="text-amber-200">{issue.message}</div>
                      {issue.suggestion && (
                        <div className="mt-1 flex items-center gap-1 text-[10px] text-amber-300/70">
                          <Info className="size-3" />
                          {issue.suggestion}
                        </div>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-white/10 px-3 py-2 text-[10px] text-text-dim">
        点击问题项定位到对应节点
      </div>
    </div>
  );
}

export function ValidationBadge({ count }: { count: number }) {
  if (count === 0) return null;

  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/20 px-2 py-0.5 text-[10px] text-rose-300">
      <AlertTriangle className="size-3" />
      {count} 个问题
    </span>
  );
}
