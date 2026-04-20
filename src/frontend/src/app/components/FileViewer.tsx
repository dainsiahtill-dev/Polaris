import { FileCode, Clock, AlertCircle, Loader2 } from 'lucide-react';
import { FileViewerSkeleton } from './FileViewerSkeleton';

interface FileViewerProps {
  selectedFile: {
    id: string;
    name: string;
    path: string;
  } | null;
  content: string;
  mtime: string;
  loading: boolean;
  error: string | null;
  badge?: { text: string; tone: 'green' | 'yellow' | 'red' } | null;
}

export function FileViewer({ selectedFile, content, mtime, loading, error, badge }: FileViewerProps) {
  if (!selectedFile) {
    return (
      <div className="h-full bg-[var(--ink-indigo)] flex items-center justify-center">
        <div className="text-center text-gray-500">
          <FileCode className="size-16 mx-auto mb-4 opacity-20" />
          <p className="text-sm">选择左侧文件查看内容</p>
        </div>
      </div>
    );
  }

  const isJsonl = selectedFile.name.endsWith('.jsonl');

  return (
    <div className="h-full bg-[var(--ink-indigo)] flex flex-col">
      {/* 文件头 */}
      <div className="px-4 py-3 border-b border-gray-800 bg-[#252526]">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-200">{selectedFile.name}</h3>
            <p className="text-xs text-gray-500 mt-0.5">{selectedFile.path}</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 text-xs text-gray-500">
              <Clock className="size-3" />
              <span>{mtime || '-'}</span>
            </div>
            {badge ? (
              <span
                className={`px-2 py-1 text-xs rounded ${
                  badge.tone === 'green'
                    ? 'bg-green-500/20 text-green-400'
                    : badge.tone === 'red'
                      ? 'bg-red-500/20 text-red-400'
                      : 'bg-yellow-500/20 text-yellow-400'
                }`}
              >
                {badge.text}
              </span>
            ) : null}
          </div>
        </div>
      </div>

      {/* 文件内容 */}
      <div className="flex-1 overflow-auto">
        {error ? (
          <div className="p-4 text-sm text-red-300 flex items-center gap-2">
            <AlertCircle className="size-4" />
            <span>{error}</span>
          </div>
        ) : null}
        {loading ? (
          <FileViewerSkeleton />
        ) : isJsonl ? (
          <div className="p-4 space-y-2">
            {!content.trim() ? (
              <div className="text-sm text-gray-400">(空)</div>
            ) : (
              content.split('\n').map((line, idx) => {
                if (!line.trim()) return null;
                try {
                  const event = JSON.parse(line);
                  return (
                    <div key={idx} className="p-3 bg-gray-800/50 rounded border border-gray-700">
                      <div className="flex items-center gap-2 mb-2">
                        {event.seq !== undefined && (
                          <span className="text-xs px-2 py-0.5 rounded bg-blue-500/20 text-blue-400">
                            seq: {event.seq}
                          </span>
                        )}
                        {event.speaker && (
                          <span className="text-xs px-2 py-0.5 rounded bg-purple-500/20 text-purple-400">
                            {event.speaker}
                          </span>
                        )}
                        {event.kind && (
                          <span className="text-xs px-2 py-0.5 rounded bg-orange-500/20 text-orange-400">
                            {event.kind}
                          </span>
                        )}
                        {event.timestamp && (
                          <span className="text-xs text-gray-500">{event.timestamp}</span>
                        )}
                      </div>
                      <pre className="text-xs text-gray-300 font-mono overflow-x-auto">
                        {JSON.stringify(event, null, 2)}
                      </pre>
                    </div>
                  );
                } catch {
                  return null;
                }
              })
            )}
          </div>
        ) : (
          <pre className="p-4 text-sm text-gray-300 font-mono leading-relaxed">
            <code>{content || '(空)'}</code>
          </pre>
        )}
      </div>
    </div>
  );
}
