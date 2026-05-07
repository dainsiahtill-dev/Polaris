import { useCallback, useEffect, useState, type ReactNode } from 'react';
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Edit3,
  Eye,
  FileText,
  FolderOpen,
  RefreshCw,
  Save,
  Search,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { cn } from '@/app/components/ui/utils';
import { sanitizeMarkdown } from '@/app/utils/xssSanitizer';
import { pmDocumentService, type PmDocumentInfo } from '@/services/pmService';
import { toast } from 'sonner';

interface PMDocumentPanelProps {
  workspace: string;
  selectedPath: string | null;
  onDocumentSelect: (path: string) => void;
}

interface FileNode {
  name: string;
  path: string;
  displayPath: string;
  type: 'file' | 'directory';
  children?: FileNode[];
  expanded?: boolean;
  document?: PmDocumentInfo;
}

export function PMDocumentPanel({
  workspace,
  selectedPath,
  onDocumentSelect,
}: PMDocumentPanelProps) {
  const [fileTree, setFileTree] = useState<FileNode[]>([]);
  const [selectedFile, setSelectedFile] = useState<FileNode | null>(null);
  const [fileContent, setFileContent] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isTreeLoading, setIsTreeLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [viewMode, setViewMode] = useState<'preview' | 'edit'>('preview');

  const loadFileTree = useCallback(async () => {
    setIsTreeLoading(true);
    setTreeError(null);

    const result = await pmDocumentService.list();
    if (!result.ok || !result.data) {
      setFileTree([]);
      setTreeError(result.error || '无法读取 PM 文档索引');
      setIsTreeLoading(false);
      return;
    }

    setFileTree(buildFileTree(result.data.documents, workspace));
    setIsTreeLoading(false);
  }, [workspace]);

  useEffect(() => {
    void loadFileTree();
  }, [loadFileTree]);

  const toggleDirectory = useCallback((node: FileNode) => {
    const updateTree = (nodes: FileNode[]): FileNode[] =>
      nodes.map((current) => {
        if (current.path === node.path) {
          return { ...current, expanded: !current.expanded };
        }
        if (current.children) {
          return { ...current, children: updateTree(current.children) };
        }
        return current;
      });

    setFileTree((currentTree) => updateTree(currentTree));
  }, []);

  const handleFileSelect = useCallback(async (node: FileNode) => {
    if (node.type === 'directory') {
      toggleDirectory(node);
      return;
    }

    setIsLoading(true);
    setContentError(null);
    setSelectedFile(node);
    onDocumentSelect(node.path);

    const result = await pmDocumentService.get(node.path);
    if (result.ok && result.data) {
      setFileContent(result.data.content || '');
    } else {
      const message = result.error || '加载 PM 文档失败';
      setFileContent('');
      setContentError(message);
      toast.error(message);
    }

    setIsLoading(false);
  }, [onDocumentSelect, toggleDirectory]);

  const handleSave = async () => {
    if (!selectedFile) return;

    setIsSaving(true);
    const result = await pmDocumentService.save(
      selectedFile.path,
      fileContent,
      'Updated from PM document workspace',
    );

    if (result.ok && result.data?.success) {
      toast.success('文件已保存');
      setViewMode('preview');
      const now = new Date().toISOString();
      setSelectedFile((previous) => previous
        ? {
          ...previous,
          document: {
            path: previous.document?.path || previous.path,
            current_version: result.data?.version || previous.document?.current_version || 1,
            version_count: previous.document?.version_count ? previous.document.version_count + 1 : 1,
            last_modified: now,
            created_at: previous.document?.created_at || now,
          },
        }
        : previous);
      await loadFileTree();
    } else {
      toast.error(result.error || '保存失败');
    }

    setIsSaving(false);
  };

  const filteredTree = searchQuery.trim()
    ? filterTree(fileTree, searchQuery.toLowerCase())
    : fileTree;

  return (
    <div className="flex h-full">
      <div className="flex w-64 flex-col border-r border-white/10 bg-slate-950/30">
        <div className="flex h-14 items-center justify-between border-b border-white/10 px-3">
          <span className="text-sm font-medium text-slate-300">文档</span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-slate-400 hover:text-slate-200"
            onClick={() => void loadFileTree()}
            disabled={isTreeLoading}
            aria-label="刷新文档列表"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isTreeLoading && 'animate-spin')} />
          </Button>
        </div>

        <div className="border-b border-white/10 p-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <Input
              placeholder="搜索文档..."
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              className="h-8 border-white/10 bg-white/5 pl-7 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50"
            />
          </div>
        </div>

        <div className="flex-1 overflow-auto py-2" data-testid="pm-document-tree">
          {treeError ? (
            <PanelMessage
              icon={<AlertCircle className="h-4 w-4 text-red-400" />}
              title="文档索引不可用"
              description={treeError}
              testId="pm-document-error"
            />
          ) : isTreeLoading ? (
            <PanelMessage
              icon={<RefreshCw className="h-4 w-4 animate-spin text-amber-400" />}
              title="正在读取真实 PM 文档索引"
              description="来源：/v2/pm/documents"
            />
          ) : filteredTree.length > 0 ? (
            filteredTree.map((node) => (
              <FileTreeNode
                key={node.path}
                node={node}
                level={0}
                selectedPath={selectedFile?.path ?? selectedPath ?? undefined}
                onSelect={handleFileSelect}
              />
            ))
          ) : (
            <PanelMessage
              icon={<FolderOpen className="h-4 w-4 text-slate-500" />}
              title="暂无已跟踪文档"
              description="运行 Architect/PM 并生成文档后，这里才会显示真实工件。"
              testId="pm-document-empty"
            />
          )}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        {selectedFile ? (
          <>
            <div className="flex h-14 items-center justify-between border-b border-white/10 bg-white/[0.02] px-4">
              <div className="flex min-w-0 items-center gap-3">
                <FileText className="h-4 w-4 flex-shrink-0 text-amber-400" />
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-medium text-slate-200">{selectedFile.name}</h3>
                  <p className="truncate text-[10px] text-slate-500">{selectedFile.displayPath}</p>
                  <p
                    className="mt-0.5 truncate text-[10px] text-amber-300/80"
                    data-testid="pm-document-provenance"
                    title={buildDocumentProvenance(selectedFile)}
                  >
                    {buildDocumentProvenance(selectedFile)}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <div className="flex items-center rounded-lg border border-white/10 bg-white/5 p-1">
                  <button
                    onClick={() => setViewMode('preview')}
                    className={cn(
                      'flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-all',
                      viewMode === 'preview'
                        ? 'bg-amber-500/20 text-amber-400'
                        : 'text-slate-500 hover:text-slate-300',
                    )}
                  >
                    <Eye className="h-3 w-3" />
                    预览
                  </button>
                  <button
                    onClick={() => setViewMode('edit')}
                    className={cn(
                      'flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-all',
                      viewMode === 'edit'
                        ? 'bg-amber-500/20 text-amber-400'
                        : 'text-slate-500 hover:text-slate-300',
                    )}
                  >
                    <Edit3 className="h-3 w-3" />
                    编辑
                  </button>
                </div>

                {viewMode === 'edit' && (
                  <Button
                    size="sm"
                    onClick={handleSave}
                    disabled={isSaving}
                    className="bg-amber-600 text-white hover:bg-amber-700"
                  >
                    <Save className={cn('mr-1.5 h-3.5 w-3.5', isSaving && 'animate-pulse')} />
                    {isSaving ? '保存中' : '保存'}
                  </Button>
                )}
              </div>
            </div>

            <div className="flex-1 overflow-auto">
              {isLoading ? (
                <div className="flex h-full items-center justify-center text-slate-500">
                  <RefreshCw className="h-5 w-5 animate-spin" />
                </div>
              ) : contentError ? (
                <div className="flex h-full items-center justify-center p-6">
                  <div className="max-w-md rounded-lg border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-200">
                    <div className="flex items-center gap-2 font-medium">
                      <AlertCircle className="h-4 w-4" />
                      文档读取失败
                    </div>
                    <p className="mt-2 text-xs text-red-200/80">{contentError}</p>
                  </div>
                </div>
              ) : viewMode === 'edit' ? (
                <textarea
                  value={fileContent}
                  onChange={(event) => setFileContent(event.target.value)}
                  className="h-full w-full resize-none bg-slate-950 p-4 font-mono text-sm text-slate-200 focus:outline-none"
                  spellCheck={false}
                />
              ) : (
                <MarkdownPreview content={fileContent} />
              )}
            </div>
          </>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-slate-500">
            <FolderOpen className="mb-4 h-12 w-12 opacity-20" />
            <p className="text-sm">选择文档以查看</p>
            <p className="mt-1 text-xs text-slate-600">左侧只显示 PM 已跟踪的真实文档</p>
          </div>
        )}
      </div>
    </div>
  );
}

function formatDocumentTimestamp(value: unknown): string {
  const raw = typeof value === 'string' ? value.trim() : '';
  if (!raw) return 'modified unknown';
  return `modified ${raw}`;
}

function buildDocumentProvenance(node: FileNode): string {
  const version = String(node.document?.current_version || '-').trim() || '-';
  const modified = formatDocumentTimestamp(node.document?.last_modified);
  return `PM docs API · v${version} · ${modified}`;
}

function displayDocumentPath(path: string, workspace: string): string {
  const normalizedPath = path.replace(/\\/g, '/');
  const normalizedWorkspace = workspace.replace(/\\/g, '/').replace(/\/+$/, '');
  const lowerPath = normalizedPath.toLowerCase();
  const lowerWorkspace = normalizedWorkspace.toLowerCase();

  if (lowerWorkspace && lowerPath.startsWith(`${lowerWorkspace}/`)) {
    return normalizedPath.slice(normalizedWorkspace.length + 1);
  }

  const workspaceMarker = '/workspace/';
  const markerIndex = lowerPath.indexOf(workspaceMarker);
  if (markerIndex >= 0) {
    return normalizedPath.slice(markerIndex + 1);
  }

  return normalizedPath;
}

function sortTree(nodes: FileNode[]): FileNode[] {
  return [...nodes]
    .sort((left, right) => {
      if (left.type !== right.type) return left.type === 'directory' ? -1 : 1;
      return left.name.localeCompare(right.name);
    })
    .map((node) => ({
      ...node,
      children: node.children ? sortTree(node.children) : undefined,
    }));
}

function buildFileTree(documents: PmDocumentInfo[], workspace: string): FileNode[] {
  const roots: FileNode[] = [];
  const directories = new Map<string, FileNode>();

  for (const document of documents) {
    const displayPath = displayDocumentPath(document.path, workspace);
    const segments = displayPath.split('/').filter(Boolean);
    if (segments.length === 0) continue;

    let currentLevel = roots;
    let currentPath = '';

    segments.forEach((segment, index) => {
      currentPath = currentPath ? `${currentPath}/${segment}` : segment;
      const isLeaf = index === segments.length - 1;

      if (isLeaf) {
        currentLevel.push({
          name: segment,
          path: document.path,
          displayPath,
          type: 'file',
          document,
        });
        return;
      }

      let directory = directories.get(currentPath);
      if (!directory) {
        directory = {
          name: segment,
          path: `directory:${currentPath}`,
          displayPath: currentPath,
          type: 'directory',
          expanded: true,
          children: [],
        };
        directories.set(currentPath, directory);
        currentLevel.push(directory);
      }

      currentLevel = directory.children ?? [];
    });
  }

  return sortTree(roots);
}

function PanelMessage({
  icon,
  title,
  description,
  testId,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  testId?: string;
}) {
  return (
    <div data-testid={testId} className="px-3 py-6 text-center">
      <div className="mx-auto mb-2 flex h-8 w-8 items-center justify-center rounded-lg bg-white/5">
        {icon}
      </div>
      <p className="text-xs font-medium text-slate-300">{title}</p>
      <p className="mt-1 text-[10px] leading-relaxed text-slate-500">{description}</p>
    </div>
  );
}

interface FileTreeNodeProps {
  node: FileNode;
  level: number;
  selectedPath?: string;
  onSelect: (node: FileNode) => void;
}

function FileTreeNode({ node, level, selectedPath, onSelect }: FileTreeNodeProps) {
  const isSelected = selectedPath === node.path;
  const isDirectory = node.type === 'directory';
  const paddingLeft = level * 12 + 12;

  return (
    <div>
      <div
        onClick={() => onSelect(node)}
        style={{ paddingLeft }}
        className={cn(
          'flex cursor-pointer items-center gap-1.5 py-1.5 pr-3 transition-colors',
          isSelected
            ? 'bg-amber-500/10 text-amber-400'
            : 'text-slate-400 hover:bg-white/5 hover:text-slate-200',
        )}
      >
        {isDirectory ? (
          <span className="text-slate-500">
            {node.expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </span>
        ) : (
          <span className="w-3.5" />
        )}

        {isDirectory ? (
          <FolderOpen className="h-4 w-4 text-amber-500/70" />
        ) : (
          <FileText className="h-4 w-4 text-slate-500" />
        )}

        <span className="truncate text-xs">{node.name}</span>
        {node.document && (
          <span className="ml-auto rounded bg-white/5 px-1.5 py-0.5 text-[9px] text-slate-500">
            v{node.document.current_version || '-'}
          </span>
        )}
      </div>

      {isDirectory && node.expanded && node.children && (
        <div>
          {node.children.map((child) => (
            <FileTreeNode
              key={child.path}
              node={child}
              level={level + 1}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function MarkdownPreview({ content }: { content: string }) {
  const renderMarkdown = (text: string): string => {
    return text
      .replace(/^# (.*$)/gim, '<h1 class="mb-4 text-2xl font-bold text-slate-100">$1</h1>')
      .replace(/^## (.*$)/gim, '<h2 class="mb-3 mt-6 text-xl font-semibold text-slate-200">$1</h2>')
      .replace(/^### (.*$)/gim, '<h3 class="mb-2 mt-4 text-lg font-medium text-slate-300">$1</h3>')
      .replace(/\*\*(.*?)\*\*/g, '<strong class="text-slate-200">$1</strong>')
      .replace(/\*(.*?)\*/g, '<em class="text-slate-300">$1</em>')
      .replace(/`([^`]+)`/g, '<code class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-amber-400">$1</code>')
      .replace(/^- (.*$)/gim, '<li class="ml-4 text-slate-300">$1</li>')
      .replace(/\n/g, '<br />');
  };

  return (
    <div
      className="prose prose-invert prose-amber max-w-none p-6"
      dangerouslySetInnerHTML={{ __html: sanitizeMarkdown(renderMarkdown(content)) }}
    />
  );
}

function filterTree(nodes: FileNode[], query: string): FileNode[] {
  return nodes.reduce<FileNode[]>((acc, node) => {
    const matches = node.name.toLowerCase().includes(query) || node.displayPath.toLowerCase().includes(query);

    if (node.type === 'directory' && node.children) {
      const filteredChildren = filterTree(node.children, query);
      if (matches || filteredChildren.length > 0) {
        acc.push({
          ...node,
          expanded: true,
          children: filteredChildren,
        });
      }
    } else if (matches) {
      acc.push(node);
    }

    return acc;
  }, []);
}
