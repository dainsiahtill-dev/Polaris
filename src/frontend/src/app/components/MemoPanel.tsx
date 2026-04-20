import { FileText, Search, Clock, AlertCircle, ChevronDown } from 'lucide-react';
import { useMemo, useState } from 'react';

export interface MemoItem {
  name: string;
  path: string;
  mtime: string;
  summary?: string;
  task_id?: string;
  task_title?: string;
  status?: string;
  acceptance?: boolean | null;
  run_id?: string;
  director_attempt?: number | null;
}

interface MemoPanelProps {
  items: MemoItem[];
  selected: MemoItem | null;
  content: string;
  mtime: string;
  loading: boolean;
  error: string | null;
  onSelect: (item: MemoItem) => void;
  collapsed?: boolean;
  onToggle?: () => void;
}

export function MemoPanel({
  items,
  selected,
  content,
  mtime,
  loading,
  error,
  onSelect,
  collapsed,
  onToggle,
}: MemoPanelProps) {
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => {
      const haystack = [
        item.name,
        item.summary,
        item.task_id,
        item.task_title,
        item.status,
        item.run_id,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [items, query]);

  return (
    <div className="h-full bg-[var(--ink-indigo)] border-l border-gray-800 flex flex-col">
      <div className="px-4 py-3 border-b border-gray-800 bg-[#252526] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FileText className="size-4 text-blue-400" />
          <h2 className="text-sm font-semibold text-gray-300">尚书令备忘录</h2>
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <div className="flex items-center gap-1">
            <Clock className="size-3" />
            <span>{mtime || selected?.mtime || '-'}</span>
          </div>
          <button
            type="button"
            onClick={onToggle}
            className="p-1 text-gray-400 hover:text-white transition-colors"
            aria-label={collapsed ? '展开备忘录面板' : '收起备忘录面板'}
          >
            <div className={`transform transition-transform ${collapsed ? '-rotate-90' : 'rotate-0'}`}>
              <ChevronDown className="size-3" />
            </div>
          </button>
        </div>
      </div>

      {collapsed ? null : (
        <div className="p-3 border-b border-gray-800">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 size-3.5 text-gray-500" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索备忘录（任务/摘要/ID）"
              className="w-full bg-[#151515] text-gray-300 px-8 py-2 rounded border border-gray-700 text-xs focus:outline-none focus:border-blue-500"
            />
          </div>
          <div className="mt-2 text-[11px] text-gray-500">共 {filtered.length} 条</div>
        </div>
      )}

      {collapsed ? null : (
        <div className="flex-1 min-h-0 flex">
          <div className="w-56 border-r border-gray-800 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="p-3 text-xs text-gray-500">(暂无备忘录)</div>
            ) : (
              filtered.map((item) => {
                const isActive = selected?.path === item.path;
                return (
                  <button
                    key={item.path}
                    onClick={() => onSelect(item)}
                    className={`w-full text-left px-3 py-2 border-b border-gray-800/60 hover:bg-white/5 ${isActive ? 'bg-blue-500/10' : ''
                      }`}
                  >
                    <div className="text-xs text-gray-300 truncate">{item.task_title || item.name}</div>
                    <div className="text-[11px] text-gray-500 truncate">
                      {item.summary || item.task_id || item.run_id || ''}
                    </div>
                  </button>
                );
              })
            )}
          </div>

          <div className="flex-1 overflow-auto">
            {error ? (
              <div className="p-4 text-sm text-red-300 flex items-center gap-2">
                <AlertCircle className="size-4" />
                <span>{error}</span>
              </div>
            ) : null}
            {loading ? (
              <div className="p-4 text-sm text-gray-300">加载中...</div>
            ) : (
              <pre className="p-4 text-xs text-gray-300 font-mono leading-relaxed whitespace-pre-wrap">
                <code>{content || '(空)'}</code>
              </pre>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
