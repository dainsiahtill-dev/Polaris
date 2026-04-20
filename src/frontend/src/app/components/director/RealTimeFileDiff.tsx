/** RealTimeFileDiff - 实时文件变更 diff 显示组件
 *
 * 功能：
 * - 实时显示文件变更（绿色新增、红色删除）
 * - 类似 VSCode 的 diff 视图
 * - 支持 unified diff 格式显示
 */
import { useMemo } from 'react';
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued';
import {
  FileCode,
  Plus,
  Minus,
  RefreshCw,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { cn } from '@/app/components/ui/utils';

interface RealTimeFileDiffProps {
  filePath: string;
  operation: 'create' | 'modify' | 'delete';
  patch?: string;
  oldContent?: string;
  newContent?: string;
  compact?: boolean;
  /** 是否是新变更（用于高亮动画） */
  isNew?: boolean;
  /** 是否显示关闭按钮 */
  onClose?: () => void;
}

export function RealTimeFileDiff({
  filePath,
  operation,
  patch,
  oldContent,
  newContent,
  compact = false,
  isNew = false,
}: RealTimeFileDiffProps) {
  // 解析 patch 内容，转换为 oldValue 和 newValue
  const { oldValue, newValue } = useMemo(() => {
    // 如果有 patch 内容，解析它
    if (patch) {
      const lines = patch.split('\n');
      const oldLines: string[] = [];
      const newLines: string[] = [];

      let inNewSection = false;
      for (const line of lines) {
        // Skip diff headers
        if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@')) {
          if (line.startsWith('@@')) {
            inNewSection = true;
          }
          continue;
        }

        if (line.startsWith('-')) {
          if (!inNewSection) {
            oldLines.push(line.substring(1));
          }
        } else if (line.startsWith('+')) {
          newLines.push(line.substring(1));
        } else if (line.startsWith(' ')) {
          oldLines.push(line.substring(1));
          newLines.push(line.substring(1));
        }
      }

      return {
        oldValue: oldLines.join('\n'),
        newValue: newLines.join('\n'),
      };
    }

    // Fallback: 使用直接传递的内容
    return {
      oldValue: oldContent || '',
      newValue: newContent || '',
    };
  }, [patch, oldContent, newContent]);

  // 计算变更统计
  const stats = useMemo(() => {
    const added = newValue.split('\n').filter(l => l.trim()).length;
    const removed = oldValue.split('\n').filter(l => l.trim()).length;
    return { added, removed };
  }, [oldValue, newValue]);

  // 自定义主题 - 匹配汉唐风格
  const customTheme = useMemo(() => ({
    variables: {
      dark: {
        diffViewerBackground: 'transparent',
        diffViewerColor: '#e2e8f0',
        addedBackground: 'rgba(16, 185, 129, 0.15)',
        addedColor: '#34d399',
        removedBackground: 'rgba(239, 68, 68, 0.15)',
        removedColor: '#f87171',
        wordAddedBackground: 'rgba(16, 185, 129, 0.4)',
        wordRemovedBackground: 'rgba(239, 68, 68, 0.4)',
        addedGutterBackground: 'rgba(16, 185, 129, 0.2)',
        removedGutterBackground: 'rgba(239, 68, 68, 0.2)',
        gutterBackground: 'rgba(50, 35, 18, 0.3)',
        gutterBackgroundDark: 'rgba(28, 18, 48, 0.5)',
        highlightBackground: 'rgba(251, 191, 36, 0.1)',
        highlightGutterBackground: 'rgba(251, 191, 36, 0.2)',
        codeFoldGutterBackground: 'rgba(245, 158, 11, 0.1)',
        emptyLineBackground: 'transparent',
        gutterColor: '#94a3b8',
        addedGutterColor: '#34d399',
        removedGutterColor: '#f87171',
      },
    },
  }), []);

  // 操作类型的颜色
  const operationColors = {
    create: {
      bg: 'bg-emerald-500/10',
      border: 'border-emerald-500/20',
      text: 'text-emerald-400',
      icon: 'text-emerald-400',
    },
    modify: {
      bg: 'bg-blue-500/10',
      border: 'border-blue-500/20',
      text: 'text-blue-400',
      icon: 'text-blue-400',
    },
    delete: {
      bg: 'bg-red-500/10',
      border: 'border-red-500/20',
      text: 'text-red-400',
      icon: 'text-red-400',
    },
  };

  const colors = operationColors[operation];

  // 空状态
  const isEmpty = !oldValue && !newValue;

  if (isEmpty && operation === 'create') {
    // 新文件 - 显示全部内容为新增
    return (
      <div className={cn('rounded-lg border', colors.bg, colors.border)}>
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-white/5">
          <div className="flex items-center gap-2">
            <FileCode className={cn('w-4 h-4', colors.icon)} />
            <span className="text-sm text-slate-300 font-mono truncate" title={filePath}>
              {filePath.split('/').pop() || filePath}
            </span>
          </div>
          <span className={cn('text-[10px] px-2 py-0.5 rounded', colors.bg, colors.text)}>
            新建
          </span>
        </div>
        {/* New file content - all highlighted as added */}
        <div className="p-2">
          <ReactDiffViewer
            oldValue=""
            newValue={newValue}
            splitView={false}
            hideLineNumbers={compact}
            compareMethod={DiffMethod.WORDS}
            styles={{
              line: { padding: compact ? '1px 8px' : '2px 8px' },
              contentText: {
                fontSize: compact ? '11px' : '12px',
                fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              },
            }}
          />
        </div>
      </div>
    );
  }

  if (isEmpty && operation === 'delete') {
    return (
      <div className={cn('rounded-lg border', colors.bg, colors.border)}>
        <div className="flex items-center justify-between px-3 py-2">
          <div className="flex items-center gap-2">
            <FileCode className={cn('w-4 h-4', colors.icon)} />
            <span className="text-sm text-slate-300">{filePath}</span>
          </div>
          <span className={cn('text-[10px] px-2 py-0.5 rounded', colors.bg, colors.text)}>
            删除
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className={cn('rounded-lg border overflow-hidden', colors.bg, colors.border)}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/5 bg-slate-900/30">
        <div className="flex items-center gap-3">
          <FileCode className={cn('w-4 h-4', colors.icon)} />
          <span className="text-sm text-slate-300 font-mono truncate" title={filePath}>
            {filePath.split('/').pop() || filePath}
          </span>
          <span className={cn('text-[10px] px-2 py-0.5 rounded', colors.bg, colors.text)}>
            {operation === 'create' ? '新建' : operation === 'modify' ? '修改' : '删除'}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px]">
          <span className="flex items-center gap-1 text-emerald-400">
            <Plus className="w-3 h-3" />
            +{stats.added}
          </span>
          <span className="flex items-center gap-1 text-red-400">
            <Minus className="w-3 h-3" />
            -{stats.removed}
          </span>
        </div>
      </div>

      {/* Diff Content */}
      <div className="p-2">
        <ReactDiffViewer
          oldValue={oldValue}
          newValue={newValue}
          splitView={!compact}
          hideLineNumbers={compact}
          compareMethod={DiffMethod.WORDS}
          styles={{
            line: { padding: compact ? '1px 8px' : '2px 8px' },
            gutter: { padding: compact ? '1px 8px' : '2px 8px', minWidth: '40px' },
            contentText: {
              fontSize: compact ? '11px' : '12px',
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
            },
          }}
        />
      </div>
    </div>
  );
}
