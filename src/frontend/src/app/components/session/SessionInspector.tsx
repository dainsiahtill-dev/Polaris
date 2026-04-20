import { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/app/components/ui/card';
import { Badge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/app/components/ui/tooltip';
import { devLogger } from '@/app/utils/devLogger';
import {
  Link2,
  Link2Off,
  Download,
  Shield,
  Clock,
  FileText,
  Activity,
  X,
} from 'lucide-react';
import { apiFetch } from '@/api';

interface SessionInspectorProps {
  sessionId: string;
  role: string;
  hostKind?: string;
  attachmentMode?: string;
  attachedRunId?: string | null;
  attachedTaskId?: string | null;
  workspace?: string;
  onAttach?: () => void;
  onDetach?: () => void;
  onExport?: () => void;
}

/**
 * Session Inspector - 会话侧边栏组件
 *
 * 显示当前会话的详细信息：
 * - Session ID
 * - Host Kind / Attachment Mode
 * - Capabilities
 * - 工具调用统计
 * - 快速操作（attach/detach/export）
 */
export function SessionInspector({
  sessionId,
  role,
  hostKind = 'electron_workbench',
  attachmentMode = 'isolated',
  attachedRunId,
  attachedTaskId,
  workspace,
  onAttach,
  onDetach,
  onExport,
}: SessionInspectorProps) {
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [auditEvents, setAuditEvents] = useState<number>(0);
  const [loading, setLoading] = useState(true);

  // 加载能力配置
  useEffect(() => {
    const loadCapabilities = async () => {
      try {
        const res = await apiFetch(`/v2/roles/capabilities/${role}?host_kind=${hostKind}`);
        const data = await res.json();
        if (data.ok && data.capabilities) {
          const caps = data.capabilities[hostKind] || data.capabilities.default || [];
          setCapabilities(caps);
        }
      } catch (err) {
        devLogger.error('[SessionInspector] Failed to load capabilities:', err);
      } finally {
        setLoading(false);
      }
    };

    loadCapabilities();
  }, [role, hostKind]);

  const getHostKindLabel = (kind: string) => {
    const labels: Record<string, string> = {
      workflow: '工作流',
      electron_workbench: '工作台',
      tui: 'TUI',
      cli: 'CLI',
      api_server: 'API',
      headless: '无头',
    };
    return labels[kind] || kind;
  };

  const getAttachmentModeLabel = (mode: string) => {
    const labels: Record<string, string> = {
      isolated: '隔离',
      attached_readonly: '只读',
      attached_collaborative: '协作',
    };
    return labels[mode] || mode;
  };

  const handleAttach = async () => {
    // TODO: 实现附着逻辑
    onAttach?.();
  };

  const handleDetach = async () => {
    if (!sessionId) return;
    
    try {
      const res = await apiFetch(`/v2/roles/sessions/${sessionId}/actions/detach`, {
        method: 'POST',
      });
      const data = await res.json();
      if (data.ok) {
        onDetach?.();
      }
    } catch (err) {
      devLogger.error('[SessionInspector] Failed to detach:', err);
    }
  };

  const handleExport = async () => {
    if (!sessionId) return;
    
    try {
      const res = await apiFetch(`/v2/roles/sessions/${sessionId}/actions/export`, {
        method: 'POST',
        body: JSON.stringify({ include_messages: true, format: 'json' }),
      });
      const data = await res.json();
      if (data.ok) {
        onExport?.();
      }
    } catch (err) {
      devLogger.error('[SessionInspector] Failed to export:', err);
    }
  };

  return (
    <Card className="w-full bg-slate-900 border-slate-700">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-slate-200 flex items-center gap-2">
          <Activity className="w-4 h-4" />
          会话状态
        </CardTitle>
        <CardDescription className="text-xs text-slate-400">
          Session: {sessionId.slice(0, 8)}...
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Host Kind */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-400">宿主类型</span>
          <Badge variant="outline" className="text-xs">
            {getHostKindLabel(hostKind)}
          </Badge>
        </div>

        {/* Attachment Mode */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-400">附着模式</span>
          <Badge 
            variant="outline" 
            className={`text-xs ${
              attachmentMode === 'isolated' 
                ? 'border-yellow-500 text-yellow-500' 
                : 'border-green-500 text-green-500'
            }`}
          >
            {getAttachmentModeLabel(attachmentMode)}
          </Badge>
        </div>

        {/* Attached Run/Task */}
        {(attachedRunId || attachedTaskId) && (
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400">已附着到</span>
            <span className="text-xs text-slate-300">
              {attachedRunId && `Run: ${attachedRunId.slice(0, 6)}`}
              {attachedTaskId && ` Task: ${attachedTaskId.slice(0, 6)}`}
            </span>
          </div>
        )}

        {/* Capabilities */}
        <div className="space-y-1">
          <div className="flex items-center gap-1 text-xs text-slate-400">
            <Shield className="w-3 h-3" />
            能力
          </div>
          <div className="flex flex-wrap gap-1">
            {loading ? (
              <span className="text-xs text-slate-500">加载中...</span>
            ) : (
              capabilities.slice(0, 4).map((cap) => (
                <Badge key={cap} variant="secondary" className="text-[10px] px-1 py-0">
                  {cap.replace(/_/g, ' ')}
                </Badge>
              ))
            )}
            {capabilities.length > 4 && (
              <Badge variant="secondary" className="text-[10px] px-1 py-0">
                +{capabilities.length - 4}
              </Badge>
            )}
          </div>
        </div>

        {/* Quick Actions */}
        <div className="pt-2 border-t border-slate-700 space-y-2">
          {attachmentMode === 'isolated' ? (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full text-xs"
                    onClick={handleAttach}
                  >
                    <Link2 className="w-3 h-3 mr-1" />
                    附着到工作流
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">将会话附着到当前工作流</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ) : (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full text-xs"
                    onClick={handleDetach}
                  >
                    <Link2Off className="w-3 h-3 mr-1" />
                    解除附着
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">将会话从工作流分离</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}

          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full text-xs"
                  onClick={handleExport}
                >
                  <Download className="w-3 h-3 mr-1" />
                  导出会话
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p className="text-xs">导出为 JSON 或 Markdown</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
      </CardContent>
    </Card>
  );
}
