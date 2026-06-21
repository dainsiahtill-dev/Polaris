/** StrategyDiffViewer - 策略变更对比查看器
 *
 * 功能：
 * - 策略 diff/变更显示
 * - 版本历史对比
 */
import { useState, useMemo } from 'react';
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued';
import {
  GitCompare,
  ChevronRight,
  Clock,
  User,
  Tag,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';

export interface StrategyVersion {
  id: string;
  version: string;
  content: string;
  timestamp: string;
  author?: string;
  message?: string;
}

interface StrategyDiffViewerProps {
  versions?: StrategyVersion[];
  leftVersion?: string;
  rightVersion?: string;
  onSelectVersion?: (left: string, right: string) => void;
  splitView?: boolean;
}

export function StrategyDiffViewer({
  versions = [],
  leftVersion,
  rightVersion,
  onSelectVersion,
  splitView = true,
}: StrategyDiffViewerProps) {
  const [selectedLeft, setSelectedLeft] = useState<string>(leftVersion || (versions[0]?.id ?? ''));
  const [selectedRight, setSelectedRight] = useState<string>(rightVersion || (versions[1]?.id ?? versions[0]?.id ?? ''));
  const [ignoreWhitespace, setIgnoreWhitespace] = useState(false);
  const [splitViewEnabled, setSplitViewEnabled] = useState(splitView);

  const leftContent = useMemo(() => {
    const v = versions.find(v => v.id === selectedLeft);
    return v?.content ?? '';
  }, [versions, selectedLeft]);

  const rightContent = useMemo(() => {
    const v = versions.find(v => v.id === selectedRight);
    return v?.content ?? '';
  }, [versions, selectedRight]);

  const handleVersionSelect = (side: 'left' | 'right', versionId: string) => {
    if (side === 'left') {
      setSelectedLeft(versionId);
    } else {
      setSelectedRight(versionId);
    }
    if (onSelectVersion) {
      const other = side === 'left' ? selectedRight : selectedLeft;
      onSelectVersion(
        side === 'left' ? versionId : other,
        side === 'right' ? versionId : other
      );
    }
  };

  return (
    <div
      className="h-full flex flex-col bg-[linear-gradient(165deg,rgba(15,23,42,0.96),rgba(30,27,75,0.74),rgba(8,15,31,0.98))]"
      data-testid="strategy-diff-viewer"
    >
      {/* Header */}
      <div className="h-14 flex items-center justify-between px-4 border-b border-indigo-400/20">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-cyan-500/[0.15] border border-cyan-400/25 flex items-center justify-center shadow-lg shadow-cyan-500/10">
            <GitCompare className="w-4 h-4 text-cyan-100" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-cyan-100">变更对比</h2>
            <p className="text-[10px] text-cyan-400/60 uppercase tracking-wider">Diff Viewer</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setIgnoreWhitespace(!ignoreWhitespace)}
            className={cn(
              'border-cyan-400/30 text-cyan-400 hover:bg-cyan-500/10',
              ignoreWhitespace && 'bg-cyan-500/20'
            )}
          >
            忽略空白
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setSplitViewEnabled(!splitViewEnabled)}
            className={cn(
              'border-cyan-400/30 text-cyan-400 hover:bg-cyan-500/10',
              splitViewEnabled && 'bg-cyan-500/20'
            )}
          >
            {splitViewEnabled ? '分屏' : '单屏'}
          </Button>
        </div>
      </div>

      {/* Version Selectors */}
      {versions.length >= 2 && (
        <div className="h-12 flex items-center gap-4 px-4 border-b border-indigo-400/10 bg-cyan-500/5">
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-cyan-200/60">对比:</span>
            <select
              value={selectedLeft}
              onChange={(e) => handleVersionSelect('left', e.target.value)}
              className="h-7 px-2 rounded bg-slate-950/80 border border-cyan-400/20 text-xs text-cyan-200"
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version} - {new Date(v.timestamp).toLocaleDateString()}
                </option>
              ))}
            </select>
          </div>

          <ChevronRight className="w-4 h-4 text-cyan-400/50" />

          <div className="flex items-center gap-2">
            <span className="text-[10px] text-cyan-200/60">到:</span>
            <select
              value={selectedRight}
              onChange={(e) => handleVersionSelect('right', e.target.value)}
              className="h-7 px-2 rounded bg-slate-950/80 border border-cyan-400/20 text-xs text-cyan-200"
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version} - {new Date(v.timestamp).toLocaleDateString()}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* Diff Viewer */}
      <div className="flex-1 overflow-hidden">
        {versions.length >= 2 ? (
          <ReactDiffViewer
            oldValue={leftContent}
            newValue={rightContent}
            splitView={splitViewEnabled}
            hideLineNumbers={false}
            compareMethod={DiffMethod.WORDS}
            useDarkTheme
            styles={{
              diffContainer: {
                background: 'rgba(15, 23, 42, 0.82)',
              },
              line: {
                padding: '2px 8px',
              },
              gutter: {
                padding: '2px 8px',
                minWidth: '50px',
              },
              contentText: {
                fontSize: '12px',
                fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              },
            }}
          />
        ) : (
          <div className="h-full flex flex-col items-center justify-center text-cyan-400/50">
            <GitCompare className="w-12 h-12 mb-4 opacity-30" />
            <p>需要至少两个版本才能对比</p>
            <p className="text-xs mt-2 opacity-70">保存策略版本后即可查看变更</p>
          </div>
        )}
      </div>

      {/* Version Info Footer */}
      {versions.length > 0 && (
        <div className="h-10 flex items-center justify-between px-4 border-t border-amber-400/10 bg-slate-900/30 text-[10px] text-cyan-200/50">
          <div className="flex items-center gap-4">
            {versions.find(v => v.id === selectedRight) && (
              <>
                <span className="flex items-center gap-1.5">
                  <Tag className="w-3 h-3" />
                  v{versions.find(v => v.id === selectedRight)?.version}
                </span>
                <span className="flex items-center gap-1.5">
                  <Clock className="w-3 h-3" />
                  {versions.find(v => v.id === selectedRight)?.timestamp
                    ? new Date(versions.find(v => v.id === selectedRight)!.timestamp).toLocaleString()
                    : '-'}
                </span>
                {versions.find(v => v.id === selectedRight)?.author && (
                  <span className="flex items-center gap-1.5">
                    <User className="w-3 h-3" />
                    {versions.find(v => v.id === selectedRight)?.author}
                  </span>
                )}
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded bg-emerald-500/50" />
              <span>新增</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded bg-red-500/50" />
              <span>删除</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
