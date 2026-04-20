/**
 * System Services Tab Host
 *
 * Host component for system services configuration and status.
 * Wraps the existing SystemServicesTab component with settings-specific styling.
 */

import { lazy, Suspense } from 'react';
import { Loader2, Terminal } from 'lucide-react';

// Lazy load the SystemServicesTab
const SystemServicesTab = lazy(() =>
  import('@/app/components/SystemServicesTab').then((module) => ({ default: module.SystemServicesTab }))
);

interface SystemServicesTabHostProps {
  /** Optional className for styling */
  className?: string;
}

/**
 * System Services Tab Host Component
 *
 * Provides a settings-compatible wrapper around the SystemServicesTab component.
 */
export function SystemServicesTabHost({ className }: SystemServicesTabHostProps) {
  return (
    <div className={`space-y-6 pb-20 ${className || ''}`}>
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold text-slate-100 flex items-center gap-2">
          <Terminal className="w-6 h-6 text-cyan-400" />
          系统服务
        </h2>
        <p className="text-sm text-slate-400 mt-1">
          查看和管理后端服务状态、MCP 服务、代码搜索等系统组件
        </p>
      </div>

      {/* Services Content */}
      <Suspense
        fallback={
          <div className="flex items-center justify-center py-12">
            <div className="flex items-center gap-2 text-text-muted">
              <Loader2 className="size-4 animate-spin" />
              <span className="text-sm">正在载入系统服务...</span>
            </div>
          </div>
        }
      >
        <SystemServicesTab />
      </Suspense>
    </div>
  );
}
