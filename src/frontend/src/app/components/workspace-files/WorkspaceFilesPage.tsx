import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactElement } from 'react';
import Editor, { loader } from '@monaco-editor/react';
import {
  AlertTriangle,
  Binary,
  Braces,
  ChevronDown,
  ChevronRight,
  Code2,
  Cpu,
  Database,
  File,
  FileCode2,
  FileImage,
  FileJson,
  FileText,
  Folder,
  FolderGit2,
  Gauge,
  HardDrive,
  Image as ImageIcon,
  Loader2,
  Lock,
  Package,
  RefreshCw,
  Search,
  Server,
  TerminalSquare,
  Workflow,
  X,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { cn } from '@/app/components/ui/utils';
import {
  listWorkspaceFileTree,
  readScopedFile,
  type WorkspaceFileNode,
  type WorkspaceFileTreeResponse,
} from '@/services';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';

type FileScope = 'workspace' | 'runtime' | 'config';

interface WorkspaceFilesPageProps {
  workspace: string;
  onBackToMain: () => void;
}

const SOURCE_SCOPES: Array<{ id: FileScope; label: string; description: string }> = [
  { id: 'workspace', label: 'Workspace', description: '当前项目目录' },
  { id: 'runtime', label: 'Runtime', description: '运行证据目录' },
  { id: 'config', label: 'Config', description: 'KernelOne 配置' },
];

const TEXT_PREVIEW_LIMIT = 600000;

type MonacoWorkerEnvironment = {
  getWorker: (_workerId: unknown, label: string) => Worker;
};

const monacoGlobal = globalThis as unknown as {
  MonacoEnvironment?: MonacoWorkerEnvironment;
};

let localMonacoConfiguration: Promise<void> | null = null;

function configureLocalMonaco(): Promise<void> {
  if (import.meta.env.MODE === 'test') {
    return Promise.resolve();
  }
  if (!localMonacoConfiguration) {
    localMonacoConfiguration = Promise.all([
      import('monaco-editor/esm/vs/editor/editor.api'),
      import('monaco-editor/esm/vs/language/json/monaco.contribution'),
      import('monaco-editor/esm/vs/editor/editor.worker?worker'),
      import('monaco-editor/esm/vs/language/json/json.worker?worker'),
    ]).then(([monacoModule, , editorWorkerModule, jsonWorkerModule]) => {
      const EditorWorker = editorWorkerModule.default;
      const JsonWorker = jsonWorkerModule.default;
      monacoGlobal.MonacoEnvironment = {
        getWorker(_workerId: unknown, label: string) {
          if (label === 'json') {
            return new JsonWorker();
          }
          return new EditorWorker();
        },
      };
      loader.config({ monaco: monacoModule });
    });
  }
  return localMonacoConfiguration;
}

function formatBytes(value: number | null | undefined): string {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let current = size;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current >= 10 || index === 0 ? current.toFixed(0) : current.toFixed(1)} ${units[index]}`;
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return 'n/a';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function nodeKindLabel(node: WorkspaceFileNode | null): string {
  if (!node) return '未选择';
  if (node.type === 'directory') return '目录';
  if (node.is_binary) return '二进制';
  return node.language || node.extension || 'text';
}

function monacoLanguage(node: WorkspaceFileNode | null): string {
  const language = String(node?.language || '').trim();
  if (!language) return 'plaintext';
  if (language === 'shell') return 'shell';
  if (language === 'log') return 'plaintext';
  return language;
}

function fileIcon(node: WorkspaceFileNode, className = 'size-4'): ReactElement {
  if (node.type === 'directory') {
    if (node.name === '.git') return <FolderGit2 className={cn(className, 'text-orange-300')} />;
    if (node.name === 'node_modules') return <Package className={cn(className, 'text-lime-300')} />;
    return <Folder className={cn(className, 'text-cyan-300')} />;
  }
  const icon = node.icon || '';
  const extension = node.extension || '';
  if (node.is_binary) {
    if (icon === 'image' || ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.avif'].includes(extension)) {
      return <FileImage className={cn(className, 'text-fuchsia-300')} />;
    }
    return <Binary className={cn(className, 'text-slate-400')} />;
  }
  if (icon === 'react') return <Code2 className={cn(className, 'text-sky-300')} />;
  if (icon === 'typescript') return <FileCode2 className={cn(className, 'text-blue-300')} />;
  if (icon === 'javascript') return <FileCode2 className={cn(className, 'text-yellow-300')} />;
  if (icon === 'python') return <TerminalSquare className={cn(className, 'text-emerald-300')} />;
  if (icon === 'rust') return <Cpu className={cn(className, 'text-orange-300')} />;
  if (icon === 'go') return <Workflow className={cn(className, 'text-cyan-300')} />;
  if (icon === 'json') return <FileJson className={cn(className, 'text-amber-300')} />;
  if (icon === 'markdown') return <FileText className={cn(className, 'text-violet-200')} />;
  if (icon === 'yaml') return <Braces className={cn(className, 'text-rose-300')} />;
  if (icon === 'image') return <ImageIcon className={cn(className, 'text-fuchsia-300')} />;
  return <File className={cn(className, 'text-slate-300')} />;
}

function walkNodes(node: WorkspaceFileNode | null | undefined, output: WorkspaceFileNode[] = []): WorkspaceFileNode[] {
  if (!node) return output;
  output.push(node);
  for (const child of node.children || []) {
    walkNodes(child, output);
  }
  return output;
}

function findFirstTextFile(node: WorkspaceFileNode | null | undefined): WorkspaceFileNode | null {
  if (!node) return null;
  if (node.type === 'file' && !node.is_binary) return node;
  for (const child of node.children || []) {
    const found = findFirstTextFile(child);
    if (found) return found;
  }
  return null;
}

function nodeMatchesQuery(node: WorkspaceFileNode, query: string): boolean {
  if (!query) return true;
  if (node.path.toLowerCase().includes(query) || node.name.toLowerCase().includes(query)) {
    return true;
  }
  return (node.children || []).some((child) => nodeMatchesQuery(child, query));
}

function TextCodePreview({
  content,
  language,
  degraded = false,
}: {
  content: string;
  language: string;
  degraded?: boolean;
}) {
  return (
    <div
      data-testid="workspace-code-text-preview"
      className="flex size-full min-h-0 flex-col bg-[#080c18] text-slate-200"
    >
      {degraded ? (
        <div className="shrink-0 border-b border-amber-300/15 bg-amber-300/10 px-4 py-2 text-xs text-amber-100">
          Monaco 编辑器未就绪，已切换为稳定文本预览。
        </div>
      ) : null}
      <pre className="min-h-0 flex-1 overflow-auto p-4 font-mono text-[12px] leading-6 text-slate-200"><code data-language={language}>{content || ' '}</code></pre>
    </div>
  );
}

function FileTreeRow({
  node,
  selectedPath,
  expanded,
  query,
  onToggle,
  onSelect,
}: {
  node: WorkspaceFileNode;
  selectedPath: string;
  expanded: Set<string>;
  query: string;
  onToggle: (node: WorkspaceFileNode) => void;
  onSelect: (node: WorkspaceFileNode) => void;
}) {
  const children = node.children || [];
  const isDirectory = node.type === 'directory';
  const isExpanded = expanded.has(node.id);
  const isSelected = selectedPath === node.path && node.type === 'file';
  const normalizedQuery = query.trim().toLowerCase();
  const visibleChildren = normalizedQuery
    ? children.filter((child) => nodeMatchesQuery(child, normalizedQuery))
    : children;

  if (normalizedQuery && !nodeMatchesQuery(node, normalizedQuery)) {
    return null;
  }

  return (
    <div>
      <button
        type="button"
        data-testid={isDirectory ? 'workspace-file-tree-dir' : 'workspace-file-tree-file'}
        data-file-path={node.path}
        onClick={() => isDirectory ? onToggle(node) : onSelect(node)}
        className={cn(
          'group flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs transition-colors',
          isSelected
            ? 'border border-cyan-300/40 bg-cyan-300/15 text-cyan-50 shadow-[0_0_18px_rgba(34,211,238,0.12)]'
            : 'text-slate-300 hover:bg-white/10 hover:text-white',
        )}
        style={{ paddingLeft: `${8 + Math.min(node.depth, 12) * 12}px` }}
      >
        <span className="flex size-4 shrink-0 items-center justify-center text-slate-500">
          {isDirectory ? (
            isExpanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />
          ) : (
            <span className="size-3.5" />
          )}
        </span>
        {fileIcon(node)}
        <span className="min-w-0 flex-1 truncate font-medium" title={node.path || node.name}>
          {node.name}
        </span>
        {node.is_symlink ? <Lock className="size-3 text-amber-300" /> : null}
        {node.type === 'file' ? (
          <span className="shrink-0 font-mono text-[10px] text-slate-500">
            {formatBytes(node.size)}
          </span>
        ) : null}
      </button>
      {isDirectory && (isExpanded || normalizedQuery) ? (
        <div>
          {visibleChildren.map((child) => (
            <FileTreeRow
              key={child.id}
              node={child}
              selectedPath={selectedPath}
              expanded={expanded}
              query={query}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function WorkspaceFilesPage({ workspace, onBackToMain }: WorkspaceFilesPageProps) {
  const [scope, setScope] = useState<FileScope>('workspace');
  const [includeIgnored, setIncludeIgnored] = useState(false);
  const [query, setQuery] = useState('');
  const [tree, setTree] = useState<WorkspaceFileTreeResponse | null>(null);
  const [treeLoading, setTreeLoading] = useState(false);
  const [treeError, setTreeError] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedNode, setSelectedNode] = useState<WorkspaceFileNode | null>(null);
  const [content, setContent] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [isMonacoReady, setIsMonacoReady] = useState(import.meta.env.MODE === 'test');
  const [monacoLoadFailed, setMonacoLoadFailed] = useState(false);
  const mountedRef = useRef(true);
  const treeRequestSeq = useRef(0);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      treeRequestSeq.current += 1;
    };
  }, []);

  const loadTree = useCallback(async () => {
    const requestId = treeRequestSeq.current + 1;
    treeRequestSeq.current = requestId;
    setTreeLoading(true);
    setTreeError('');
    const result = await listWorkspaceFileTree({
      scope,
      includeHidden: true,
      includeIgnored,
      maxDepth: includeIgnored ? 16 : 12,
      maxEntries: includeIgnored ? 12000 : 6000,
    });
    if (!mountedRef.current || requestId !== treeRequestSeq.current) {
      return;
    }
    setTreeLoading(false);
    if (!result.ok || !result.data) {
      setTree(null);
      setSelectedNode(null);
      setContent('');
      setTreeError(result.error || '文件树加载失败');
      return;
    }
    setTree(result.data);
    const nextExpanded = new Set<string>([result.data.tree.id]);
    for (const child of result.data.tree.children || []) {
      if (child.type === 'directory' && ['src', 'app', 'polaris', 'docs', 'runtime', 'tests'].includes(child.name)) {
        nextExpanded.add(child.id);
      }
    }
    setExpanded(nextExpanded);
    const firstFile = findFirstTextFile(result.data.tree);
    setSelectedNode(firstFile);
  }, [includeIgnored, scope]);

  useEffect(() => {
    void loadTree();
  }, [loadTree, workspace]);

  useEffect(() => {
    let mounted = true;
    configureLocalMonaco()
      .then(() => {
        if (!mounted) return;
        setIsMonacoReady(true);
        setMonacoLoadFailed(false);
      })
      .catch(() => {
        if (!mounted) return;
        setIsMonacoReady(false);
        setMonacoLoadFailed(true);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedNode || selectedNode.type !== 'file') {
      setContent('');
      setPreviewError('');
      return;
    }
    if (selectedNode.is_binary) {
      setContent('');
      setPreviewError('');
      return;
    }
    const controller = new AbortController();
    setPreviewLoading(true);
    setPreviewError('');
    void readScopedFile(selectedNode.path, scope)
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!result.ok || !result.data) {
          setContent('');
          setPreviewError(result.error || '文件读取失败');
          return;
        }
        setContent(result.data.content || '');
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setContent('');
        setPreviewError(error instanceof Error ? error.message : '文件读取失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setPreviewLoading(false);
        }
      });
    return () => controller.abort();
  }, [scope, selectedNode?.path, selectedNode?.type, selectedNode?.is_binary]);

  const allNodes = useMemo(() => walkNodes(tree?.tree), [tree]);
  const visibleFileCount = useMemo(
    () => allNodes.filter((node) => node.type === 'file' && (!query || node.path.toLowerCase().includes(query.toLowerCase()))).length,
    [allNodes, query],
  );

  const handleToggle = useCallback((node: WorkspaceFileNode) => {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(node.id)) next.delete(node.id);
      else next.add(node.id);
      return next;
    });
  }, []);

  const handleSelect = useCallback((node: WorkspaceFileNode) => {
    setSelectedNode(node);
  }, []);

  const selectedTitle = selectedNode?.path || selectedNode?.name || '选择文件';
  const workspaceName = workspaceLabel(workspace, '未选定工作区');
  const treeWarning = tree && (tree.truncated || tree.stats.omitted > 0) ? tree : null;

  return (
    <div data-testid="workspace-files-page" className="polaris-soft-scope flex size-full flex-col overflow-hidden bg-bg text-text-main">
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-white/10 bg-slate-950/80 px-4 backdrop-blur-xl">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onBackToMain}
            className="rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-semibold text-slate-200 transition-colors hover:border-cyan-300/40 hover:text-cyan-100"
          >
            返回
          </button>
          <div className="flex size-9 items-center justify-center rounded-lg border border-cyan-300/25 bg-cyan-300/10 text-cyan-200 shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
            <HardDrive className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-sm font-semibold text-white">Workspace 文件浏览器</h1>
              <span className="rounded border border-cyan-300/20 bg-cyan-300/10 px-1.5 py-0.5 font-mono text-[10px] uppercase text-cyan-200">
                SaaS ready
              </span>
            </div>
            <p className="truncate text-xs text-slate-400" title={workspace}>{workspaceName}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="hidden items-center gap-1 rounded-lg border border-white/10 bg-white/5 p-1 md:flex">
            {SOURCE_SCOPES.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setScope(item.id)}
                className={cn(
                  'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                  scope === item.id ? 'bg-cyan-300/15 text-cyan-100' : 'text-slate-400 hover:text-white',
                )}
                title={item.description}
              >
                {item.label}
              </button>
            ))}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setIncludeIgnored((value) => !value)}
            className={cn(
              'border border-white/10 text-xs',
              includeIgnored ? 'bg-amber-300/15 text-amber-100' : 'text-slate-300',
            )}
          >
            <Package className="size-3.5" />
            vendor/cache
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => { void loadTree(); }} className="border border-white/10 text-slate-200">
            {treeLoading ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
            刷新
          </Button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(260px,360px)_minmax(0,1fr)_minmax(240px,300px)] overflow-hidden">
        <aside className="flex min-h-0 flex-col border-r border-white/10 bg-slate-950/60">
          <div className="space-y-3 border-b border-white/10 p-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-slate-500" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.currentTarget.value)}
                placeholder="搜索文件..."
                className="h-9 border-white/10 bg-slate-900/80 pl-9 text-xs text-slate-100 placeholder:text-slate-500"
              />
              {query ? (
                <button
                  type="button"
                  onClick={() => setQuery('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-500 hover:text-slate-100"
                  aria-label="清空搜索"
                >
                  <X className="size-3.5" />
                </button>
              ) : null}
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-lg border border-white/10 bg-white/5 p-2">
                <div className="font-mono text-sm text-white">{tree?.stats.files.toLocaleString() || '0'}</div>
                <div className="text-[10px] text-slate-500">files</div>
              </div>
              <div className="rounded-lg border border-white/10 bg-white/5 p-2">
                <div className="font-mono text-sm text-white">{tree?.stats.directories.toLocaleString() || '0'}</div>
                <div className="text-[10px] text-slate-500">dirs</div>
              </div>
              <div className="rounded-lg border border-white/10 bg-white/5 p-2">
                <div className="font-mono text-sm text-white">{visibleFileCount.toLocaleString()}</div>
                <div className="text-[10px] text-slate-500">visible</div>
              </div>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {treeError ? (
              <div className="rounded-lg border border-red-400/20 bg-red-500/10 p-3 text-xs text-red-100">
                {treeError}
              </div>
            ) : treeLoading && !tree ? (
              <div className="flex h-full items-center justify-center text-xs text-slate-500">
                <Loader2 className="mr-2 size-4 animate-spin" />
                正在扫描目录
              </div>
            ) : tree?.tree ? (
              <FileTreeRow
                node={tree.tree}
                selectedPath={selectedNode?.path || ''}
                expanded={expanded}
                query={query}
                onToggle={handleToggle}
                onSelect={handleSelect}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-slate-500">暂无文件树</div>
            )}
          </div>
        </aside>

        <main className="flex min-h-0 flex-col bg-[#080c18]">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/10 bg-slate-950/70 px-4">
            <div className="flex min-w-0 items-center gap-2">
              {selectedNode ? fileIcon(selectedNode, 'size-4') : <File className="size-4 text-slate-500" />}
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-slate-100" title={selectedTitle}>
                  {selectedTitle}
                </div>
                <div className="font-mono text-[10px] text-slate-500">{nodeKindLabel(selectedNode)}</div>
              </div>
            </div>
            {selectedNode?.type === 'file' && !selectedNode.is_binary ? (
              <span className="rounded border border-cyan-300/20 bg-cyan-300/10 px-2 py-1 font-mono text-[10px] text-cyan-100">
                read-only · {monacoLanguage(selectedNode)}
              </span>
            ) : null}
          </div>
          <div className="min-h-0 flex-1">
            {!selectedNode ? (
              <div className="flex h-full flex-col items-center justify-center text-slate-500">
                <Server className="mb-3 size-10 text-cyan-300/30" />
                <p className="text-sm">选择文件查看代码</p>
              </div>
            ) : selectedNode.type === 'directory' ? (
              <div className="flex h-full flex-col items-center justify-center text-slate-500">
                <Folder className="mb-3 size-10 text-cyan-300/30" />
                <p className="text-sm">这是目录，选择一个文件打开预览</p>
              </div>
            ) : selectedNode.is_binary ? (
              <div className="flex h-full flex-col items-center justify-center px-8 text-center text-slate-500">
                {fileIcon(selectedNode, 'mb-4 size-12')}
                <p className="text-sm text-slate-300">二进制文件不在浏览器中直接读取</p>
                <p className="mt-2 max-w-md text-xs leading-6 text-slate-500">
                  文件类型、大小和修改时间仍可在右侧查看。代码预览只读取文本文件，避免把图片、字体或构建产物误解码。
                </p>
              </div>
            ) : previewLoading ? (
              <div className="flex h-full items-center justify-center text-xs text-slate-500">
                <Loader2 className="mr-2 size-4 animate-spin" />
                正在读取文件
              </div>
            ) : previewError ? (
              <div className="m-4 rounded-lg border border-red-400/20 bg-red-500/10 p-4 text-sm text-red-100">
                <AlertTriangle className="mb-2 size-4" />
                {previewError}
              </div>
            ) : !isMonacoReady || monacoLoadFailed ? (
              <TextCodePreview
                content={content.slice(0, TEXT_PREVIEW_LIMIT)}
                language={monacoLanguage(selectedNode)}
                degraded={monacoLoadFailed}
              />
            ) : (
              <Editor
                height="100%"
                language={monacoLanguage(selectedNode)}
                value={content.slice(0, TEXT_PREVIEW_LIMIT)}
                theme="vs-dark"
                loading={(
                  <TextCodePreview
                    content={content.slice(0, TEXT_PREVIEW_LIMIT)}
                    language={monacoLanguage(selectedNode)}
                  />
                )}
                onMount={() => {
                  setMonacoLoadFailed(false);
                }}
                options={{
                  readOnly: true,
                  minimap: { enabled: true },
                  fontSize: 13,
                  fontLigatures: true,
                  wordWrap: 'on',
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  renderLineHighlight: 'line',
                }}
              />
            )}
          </div>
        </main>

        <aside className="flex min-h-0 flex-col border-l border-white/10 bg-slate-950/70 p-4">
          <div className="mb-4 flex items-center gap-2">
            <Gauge className="size-4 text-cyan-200" />
            <h2 className="text-sm font-semibold text-white">文件态势</h2>
          </div>
          <div className="space-y-3">
            <div className="rounded-lg border border-white/10 bg-white/5 p-3">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">scope</div>
              <div className="mt-1 text-sm font-semibold text-slate-100">{scope}</div>
            </div>
            <div className="rounded-lg border border-white/10 bg-white/5 p-3">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">selected</div>
              <div className="mt-1 break-all font-mono text-xs text-slate-200">{selectedTitle}</div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                <div className="text-[10px] text-slate-500">size</div>
                <div className="mt-1 font-mono text-sm text-white">{formatBytes(selectedNode?.size)}</div>
              </div>
              <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                <div className="text-[10px] text-slate-500">binary</div>
                <div className="mt-1 font-mono text-sm text-white">{selectedNode?.is_binary ? 'yes' : 'no'}</div>
              </div>
            </div>
            <div className="rounded-lg border border-white/10 bg-white/5 p-3">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">modified</div>
              <div className="mt-1 text-xs text-slate-200">{formatTimestamp(selectedNode?.mtime)}</div>
            </div>
            {treeWarning ? (
              <div className="rounded-lg border border-amber-300/20 bg-amber-300/10 p-3 text-xs text-amber-100">
                <div className="mb-1 flex items-center gap-2 font-semibold">
                  <AlertTriangle className="size-3.5" />
                  目录已防护裁剪
                </div>
                <p className="leading-5">
                  omitted {treeWarning.stats.omitted.toLocaleString()} · max {treeWarning.max_entries.toLocaleString()} entries
                </p>
                {treeWarning.excluded.length > 0 ? (
                  <p className="mt-2 break-words text-amber-100/70">
                    {treeWarning.excluded.slice(0, 8).join(', ')}
                    {treeWarning.excluded.length > 8 ? '...' : ''}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
          <div className="mt-auto rounded-lg border border-cyan-300/15 bg-cyan-300/10 p-3 text-xs leading-5 text-cyan-100/80">
            <p>HTTP 仅用于初始目录快照、用户刷新和单次文件读取。</p>
            <p className="mt-1 text-cyan-100/55">运行态实时数据仍只走 runtime.v2 WebSocket。</p>
          </div>
        </aside>
      </div>
    </div>
  );
}
