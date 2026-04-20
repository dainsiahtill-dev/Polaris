import { useState, useMemo } from 'react';
import { ScrollText, FileText, CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react';
import { normalizePlanText } from '@/app/utils/planRender';
import { cn } from '@/app/components/ui/utils';
import { StatusBadge } from '@/app/components/ui/badge';

interface PlanBoardProps {
  planText: string | null | undefined;
  planMtime?: string | null;
  planTextNormalized?: boolean;
  className?: string;
  defaultExpanded?: boolean;
}

export function PlanBoard({ 
  planText, 
  planMtime, 
  planTextNormalized, 
  className,
  defaultExpanded = true 
}: PlanBoardProps) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  
  // 处理 plan 文本，只进行规范化
  const processedText = useMemo(() => {
    const text = planText || '';
    if (planTextNormalized) {
      return text;
    }
    const { text: normalized } = normalizePlanText(text);
    return normalized;
  }, [planText, planTextNormalized]);

  // 空状态
  if (!planText || planText.trim().length === 0) {
    return (
      <div
        className={cn(
          'rounded-xl border border-border bg-bg-panel/50 p-6',
          className
        )}
      >
        <div className="flex items-center gap-2 mb-4">
          <ScrollText className="w-5 h-5 text-accent" />
          <h3 className="font-heading font-bold text-text-main">敕令总图</h3>
          {planMtime && (
            <span className="text-xs text-text-muted ml-auto">{planMtime}</span>
          )}
        </div>
        <div className="text-center py-8 text-text-muted">
          <ScrollText className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p>暂无敕令总图</p>
          <p className="text-xs mt-1">请在 plan.md 中编写任务计划</p>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'rounded-xl border border-border bg-bg-panel/50 overflow-hidden flex flex-col',
        className
      )}
    >
      {/* 可点击的头部 */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-4 py-3 border-b border-border bg-bg-tertiary/30 hover:bg-bg-tertiary/50 transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-text-muted" />
          ) : (
            <ChevronRight className="w-4 h-4 text-text-muted" />
          )}
          <ScrollText className="w-5 h-5 text-accent" />
          <h3 className="font-heading font-bold text-text-main">敕令总图</h3>
          {planTextNormalized && (
            <StatusBadge color="success" variant="dot" className="text-xs">
              <CheckCircle2 className="w-3 h-3" />
              已规范化
            </StatusBadge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">{processedText.length} 字符</span>
          {planMtime && (
            <span className="text-xs text-text-muted hidden sm:inline">· {planMtime}</span>
          )}
        </div>
      </button>

      {/* 可折叠的内容区 */}
      {isExpanded && (
        <>
          <div className="flex-1 p-4 overflow-auto max-h-80">
            <pre className="text-sm text-text-main whitespace-pre-wrap break-words font-sans leading-relaxed">
              {processedText}
            </pre>
          </div>

          {/* 底部信息 */}
          <div className="px-4 py-2 border-t border-border bg-bg-tertiary/20 text-xs text-text-muted flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2">
              <FileText className="w-3 h-3" />
              <span>点击头部可折叠</span>
              {planTextNormalized && (
                <span className="text-status-success">· 数据已规范化</span>
              )}
            </div>
            <span className="text-text-dim">只读</span>
          </div>
        </>
      )}
    </div>
  );
}

export default PlanBoard;
