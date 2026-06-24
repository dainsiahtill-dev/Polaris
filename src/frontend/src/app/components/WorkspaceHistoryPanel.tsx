import { RunLedgerHistoryContent } from './HistoryDrawer';

interface WorkspaceHistoryPanelProps {
  className?: string;
  defaultLimit?: number;
  workspace?: string;
}

export function WorkspaceHistoryPanel({
  className,
  defaultLimit,
  workspace,
}: WorkspaceHistoryPanelProps) {
  return (
    <div className={`h-full min-h-0 ${className || ''}`}>
      <RunLedgerHistoryContent defaultLimit={defaultLimit} workspace={workspace} />
    </div>
  );
}
