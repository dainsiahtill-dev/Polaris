import { jsx as _jsx } from "react/jsx-runtime";
import { RunLedgerHistoryContent } from './HistoryDrawer';
export function WorkspaceHistoryPanel({ className, defaultLimit, workspace, }) {
    return (_jsx("div", { className: `h-full min-h-0 ${className || ''}`, children: _jsx(RunLedgerHistoryContent, { defaultLimit: defaultLimit, workspace: workspace }) }));
}
