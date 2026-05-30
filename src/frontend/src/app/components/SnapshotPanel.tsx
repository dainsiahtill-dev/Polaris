import { FileText, Clock } from 'lucide-react';

interface SnapshotPanelProps {
  timestamp?: string | null;
  fileStatus?: string[] | null;
  filePaths?: string[] | null;
  directorState?: Record<string, unknown> | null;
}

export function SnapshotPanel({ timestamp, fileStatus, filePaths, directorState }: SnapshotPanelProps) {
  const fileLines = Array.isArray(fileStatus) ? fileStatus.slice(0, 4) : [];
  const filePathsCount = Array.isArray(filePaths) ? filePaths.length : 0;
  const directorPhase = directorState && typeof directorState['phase'] === 'string' ? String(directorState['phase']) : '';
  const directorIter = directorState && typeof directorState['iteration'] === 'number' ? Number(directorState['iteration']) : null;
  const directorStatus = directorState && typeof directorState['status'] === 'string' ? String(directorState['status']) : '';

  return (
    <div data-testid="snapshot-panel" className="z-10 min-w-0 overflow-hidden border-b border-border bg-bg-panel/90 px-4 py-2 text-xs text-text-dim backdrop-blur-sm">
      <div className="flex min-w-0 items-center justify-between gap-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2 font-mono text-[10px] text-text-dim">
          <div className="flex shrink-0 items-center gap-1.5 rounded border border-border bg-bg-tertiary px-2 py-1">
            <FileText className="size-3 shrink-0 text-text-muted" />
            <span>卷宗: <span className="text-text-main">{filePathsCount}</span></span>
          </div>

          <div className="flex min-w-0 items-center gap-1.5 rounded border border-border bg-bg-tertiary px-2 py-1">
            <Clock className="size-3 shrink-0 text-text-muted" />
            <span className="min-w-0 break-all">时刻: <span className="text-text-main">{timestamp || '—'}</span></span>
          </div>

          {directorPhase || directorStatus || directorIter !== null ? (
            <div className="flex min-w-0 items-center gap-1.5 rounded border border-border bg-bg-tertiary px-2 py-1 animate-pulse-slow">
              <span className="shrink-0 font-bold text-status-secondary">Chief Engineer</span>
              <span className="mx-1 h-3 w-px shrink-0 bg-border"></span>
              <span className="min-w-0 break-all text-text-main">{directorPhase || directorStatus || ''}</span>
              {directorIter !== null ? <span className="text-accent"> #{directorIter}</span> : ''}
            </div>
          ) : null}
        </div>
      </div>

      {fileLines.length > 0 ? (
        <div className="-mx-4 mt-2 flex min-w-0 flex-wrap gap-x-4 gap-y-1 border-t border-border bg-bg-tertiary/20 px-4 pb-1 pt-2 font-mono text-[10px] text-text-dim">
          {fileLines.map((line) => (
            <span key={line} data-testid="snapshot-panel-file-line" className="flex max-w-full min-w-0 items-center gap-1 break-all md:max-w-[28rem]">
              <span className="h-1 w-1 shrink-0 rounded-full bg-text-muted"></span>
              {line}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
