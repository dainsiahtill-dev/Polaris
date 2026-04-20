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
    <div className="border-b border-border bg-bg-panel/90 px-4 py-2 text-xs text-text-dim backdrop-blur-sm z-10">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-shrink-0 items-center gap-4 text-text-dim font-mono text-[10px]">
          <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-bg-tertiary border border-border">
            <FileText className="size-3 text-text-muted" />
            <span>卷宗: <span className="text-text-main">{filePathsCount}</span></span>
          </div>

          <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-bg-tertiary border border-border">
            <Clock className="size-3 text-text-muted" />
            <span>时刻: <span className="text-text-main">{timestamp || '—'}</span></span>
          </div>

          {directorPhase || directorStatus || directorIter !== null ? (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-bg-tertiary border border-border animate-pulse-slow">
              <span className="text-status-secondary font-bold">工部尚书</span>
              <span className="w-px h-3 bg-border mx-1"></span>
              <span className="text-text-main">{directorPhase || directorStatus || ''}</span>
              {directorIter !== null ? <span className="text-accent"> #{directorIter}</span> : ''}
            </div>
          ) : null}
        </div>
      </div>

      {fileLines.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] font-mono text-text-dim border-t border-border pt-2 bg-bg-tertiary/20 -mx-4 px-4 pb-1">
          {fileLines.map((line) => (
            <span key={line} className="truncate max-w-[28rem] flex items-center gap-1">
              <span className="w-1 h-1 rounded-full bg-text-muted"></span>
              {line}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
