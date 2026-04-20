import type { ReactNode } from 'react';
import { X, Loader2, ChevronDown, ChevronUp, Copy, Download } from 'lucide-react';
import type { SimpleProvider } from '../types';

type PanelStatus = 'idle' | 'running' | 'success' | 'failed';

interface TestPanelHeaderProps {
  provider: SimpleProvider;
  status: PanelStatus;
  onClose: () => void;
  running?: boolean;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  onCopyLogs?: () => void;
  onExportLogs?: () => void;
  title?: string;
  subtitle?: string;
  statusText?: Partial<Record<PanelStatus, string>>;
  extraActions?: ReactNode;
}

const STATUS_TEXT: Record<PanelStatus, string> = {
  idle: '准备就绪',
  running: '测试中',
  success: '成功',
  failed: '失败'
};

const STATUS_BADGES: Record<PanelStatus, string> = {
  idle: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
  running: 'bg-cyan-500/20 text-cyan-200 border-cyan-500/30',
  success: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
  failed: 'bg-red-500/20 text-red-200 border-red-500/30'
};

export function TestPanelHeader({
  provider,
  status,
  onClose,
  running,
  collapsed,
  onToggleCollapse,
  onCopyLogs,
  onExportLogs,
  title,
  subtitle,
  statusText,
  extraActions
}: TestPanelHeaderProps) {
  const resolvedTitle = title || `🖥️ Testing: ${provider.name}`;
  const resolvedSubtitle = subtitle || `Provider: ${provider.name} · Model: ${provider.modelId || 'default'}`;
  const resolvedStatusText = statusText?.[status] || STATUS_TEXT[status];

  return (
    <div className="flex items-start justify-between gap-4 p-4 border-b border-cyan-500/20 bg-black/40">
      <div>
        <div className="text-sm font-semibold text-text-main flex items-center gap-2">
          {resolvedTitle}
          <span className={`text-[9px] uppercase tracking-wider px-2 py-0.5 rounded border ${STATUS_BADGES[status]}`}>
            {resolvedStatusText}
          </span>
        </div>
        <div className="text-[10px] text-text-dim mt-1">
          {resolvedSubtitle}
        </div>
      </div>
      <div className="flex items-center gap-2">
        {extraActions}
        {onCopyLogs ? (
          <button
            type="button"
            onClick={onCopyLogs}
            className="p-1.5 rounded border border-white/10 hover:border-accent/40 text-text-dim"
            title="复制日志"
          >
            <Copy className="size-3" />
          </button>
        ) : null}
        {onExportLogs ? (
          <button
            type="button"
            onClick={onExportLogs}
            className="p-1.5 rounded border border-white/10 hover:border-accent/40 text-text-dim"
            title="导出会话"
          >
            <Download className="size-3" />
          </button>
        ) : null}
        {onToggleCollapse ? (
          <button
            type="button"
            onClick={onToggleCollapse}
            className="p-1.5 rounded border border-white/10 hover:border-accent/40 text-text-dim"
            title={collapsed ? '展开' : '折叠'}
          >
            {collapsed ? <ChevronDown className="size-3" /> : <ChevronUp className="size-3" />}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          disabled={running}
          className="p-1.5 rounded border border-white/10 hover:border-accent/40 disabled:opacity-50"
        >
          {running ? <Loader2 className="size-3 animate-spin" /> : <X className="size-3" />}
        </button>
      </div>
    </div>
  );
}
