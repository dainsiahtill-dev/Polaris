import { useCallback, useEffect, useState, type ReactNode } from 'react';
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Edit3,
  Eye,
  FileText,
  FolderOpen,
  GitCompare,
  History,
  RefreshCw,
  Save,
  Search,
  Trash2,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { cn } from '@/app/components/ui/utils';
import { sanitizeMarkdown } from '@/app/utils/xssSanitizer';
import {
  pmDocumentService,
  type PmDocumentDeleteResponse,
  type PmDocumentDiffResponse,
  type PmDocumentInfo,
  type PmDocumentSearchResult,
  type PmDocumentVersionInfo,
} from '@/services/pmService';
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

function EndpointBadge({
  endpoint,
  method,
  testId,
}: {
  endpoint: string;
  method?: string;
  testId?: string;
}) {
  return (
    <span
      className="shrink-0 rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] font-medium text-slate-500"
      title={endpoint}
      data-endpoint={endpoint}
      data-testid={testId}
    >
      {method ? `${method} API` : 'API'}
    </span>
  );
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
  const [isDeleting, setIsDeleting] = useState(false);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteResult, setDeleteResult] = useState<PmDocumentDeleteResponse | null>(null);
  const [showDeletePanel, setShowDeletePanel] = useState(false);
  const [deleteBackingFile, setDeleteBackingFile] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<PmDocumentSearchResult[]>([]);
  const [isSearchLoading, setIsSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [documentVersions, setDocumentVersions] = useState<PmDocumentVersionInfo[]>([]);
  const [isVersionsLoading, setIsVersionsLoading] = useState(false);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [documentDiff, setDocumentDiff] = useState<PmDocumentDiffResponse | null>(null);
  const [isDiffLoading, setIsDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'preview' | 'edit'>('preview');
  const [selectedDocumentVersion, setSelectedDocumentVersion] = useState<string | null>(null);
  const [isVersionLoading, setIsVersionLoading] = useState(false);
  const [versionReadError, setVersionReadError] = useState<string | null>(null);

  const loadFileTree = useCallback(async () => {
    setIsTreeLoading(true);
    setTreeError(null);

    const result = await pmDocumentService.list(workspace);
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

  useEffect(() => {
    const query = searchQuery.trim();
    if (query.length < 2) {
      setSearchResults([]);
      setSearchError(null);
      setIsSearchLoading(false);
      return undefined;
    }

    let isCurrent = true;
    setIsSearchLoading(true);
    setSearchError(null);

    const timeoutId = window.setTimeout(async () => {
      const result = await pmDocumentService.search(query, 20, workspace);
      if (!isCurrent) return;

      if (result.ok && result.data) {
        setSearchResults(result.data.results || []);
      } else {
        setSearchResults([]);
        setSearchError(result.error || 'PM 文档搜索不可用');
      }

      setIsSearchLoading(false);
    }, 250);

    return () => {
      isCurrent = false;
      window.clearTimeout(timeoutId);
    };
  }, [searchQuery, workspace]);

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

  const loadDocumentVersions = useCallback(async (path: string) => {
    setIsVersionsLoading(true);
    setVersionsError(null);

    const result = await pmDocumentService.versions(path, workspace);
    if (result.ok && result.data) {
      setDocumentVersions(result.data.versions || []);
    } else {
      setDocumentVersions([]);
      setVersionsError(result.error || '加载 PM 文档版本失败');
    }

    setIsVersionsLoading(false);
  }, [workspace]);

  const loadDocumentNode = useCallback(async (node: FileNode) => {
    setIsLoading(true);
    setContentError(null);
    setDocumentDiff(null);
    setDiffError(null);
    setDeleteError(null);
    setDeleteResult(null);
    setShowDeletePanel(false);
    setDeleteBackingFile(false);
    setSelectedDocumentVersion(null);
    setVersionReadError(null);
    setSelectedFile(node);
    onDocumentSelect(node.path);
    void loadDocumentVersions(node.path);

    const result = await pmDocumentService.get(node.path, null, workspace);
    if (result.ok && result.data) {
      setFileContent(result.data.content || '');
      setSelectedFile((current) => current?.path === node.path
        ? { ...current, document: result.data }
        : current);
    } else {
      const message = result.error || '加载 PM 文档失败';
      setFileContent('');
      setContentError(message);
      toast.error(message);
    }

    setIsLoading(false);
  }, [loadDocumentVersions, onDocumentSelect, workspace]);

  const handleFileSelect = useCallback(async (node: FileNode) => {
    if (node.type === 'directory') {
      toggleDirectory(node);
      return;
    }

    await loadDocumentNode(node);
  }, [loadDocumentNode, toggleDirectory]);

  const handleSearchResultSelect = useCallback(async (result: PmDocumentSearchResult) => {
    const path = readSearchResultPath(result);
    if (!path) return;

    const displayPath = displayDocumentPath(path, workspace);
    await loadDocumentNode({
      name: basenameFromPath(displayPath, path),
      path,
      displayPath,
      type: 'file',
      document: findDocumentInfo(fileTree, path),
    });
  }, [fileTree, loadDocumentNode, workspace]);

  const handleSave = async () => {
    if (!selectedFile) return;
    if (selectedDocumentVersion) {
      toast.error('历史版本为只读，无法保存');
      return;
    }

    setIsSaving(true);
    const result = await pmDocumentService.save(
      selectedFile.path,
      fileContent,
      'Updated from PM document workspace',
      workspace,
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
      await loadDocumentVersions(selectedFile.path);
    } else {
      toast.error(result.error || '保存失败');
    }

    setIsSaving(false);
  };

  const handleLoadDocumentVersion = async (version: string | null) => {
    if (!selectedFile) return;

    setIsVersionLoading(true);
    setVersionReadError(null);
    setDocumentDiff(null);
    setDiffError(null);

    const result = await pmDocumentService.get(selectedFile.path, version, workspace);
    if (result.ok && result.data) {
      setFileContent(result.data.content || '');
      setSelectedDocumentVersion(version);
      setViewMode('preview');
      setSelectedFile((current) => current?.path === selectedFile.path
        ? { ...current, document: result.data }
        : current);
    } else {
      setVersionReadError(result.error || '读取 PM 文档版本失败');
    }

    setIsVersionLoading(false);
  };

  const handleDelete = async () => {
    if (!selectedFile) return;

    const deletedPath = selectedFile.path;
    setIsDeleting(true);
    setDeleteError(null);
    setDeleteResult(null);

    const result = await pmDocumentService.delete(deletedPath, deleteBackingFile, workspace);
    if (result.ok && result.data?.success && result.data.deleted) {
      setDeleteResult(result.data);
      toast.success(deleteBackingFile ? 'PM 文档和文件已删除' : 'PM 文档记录已删除');
      setSelectedFile(null);
      setFileContent('');
      setDocumentVersions([]);
      setDocumentDiff(null);
      setVersionsError(null);
      setDiffError(null);
      setShowDeletePanel(false);
      setDeleteBackingFile(false);
      await loadFileTree();
    } else {
      setDeleteError(result.error || '删除 PM 文档失败');
    }

    setIsDeleting(false);
  };

  const handleCompareLatest = async () => {
    if (!selectedFile) return;

    const pair = latestDocumentVersionPair(documentVersions);
    if (!pair) return;

    setIsDiffLoading(true);
    setDiffError(null);
    setDocumentDiff(null);

    const result = await pmDocumentService.compare(selectedFile.path, pair.oldVersion, pair.newVersion, workspace);
    if (result.ok && result.data) {
      setDocumentDiff(result.data);
    } else {
      setDiffError(result.error || '比较 PM 文档版本失败');
    }

    setIsDiffLoading(false);
  };

  const filteredTree = searchQuery.trim()
    ? filterTree(fileTree, searchQuery.toLowerCase())
    : fileTree;
  const searchTerm = searchQuery.trim();
  const showBackendSearch = searchTerm.length >= 2;
  const validSearchResults = searchResults.filter((result) => Boolean(readSearchResultPath(result)));

  return (
    <div data-testid="pm-document-panel" className="flex h-full">
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

        {showBackendSearch && (
          <div className="border-b border-white/10 px-2 py-2" data-testid="pm-document-search-panel">
            <div className="mb-1 flex items-center justify-between px-1 text-[10px] uppercase tracking-wider text-slate-500">
              <span>内容搜索</span>
              <span data-testid="pm-document-search-count">
                {isSearchLoading ? 'searching' : `${validSearchResults.length} matches`}
              </span>
            </div>
            {isSearchLoading ? (
              <div className="flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-400">
                <RefreshCw className="h-3.5 w-3.5 animate-spin text-amber-400" />
                正在调用后端文档搜索
              </div>
            ) : searchError ? (
              <div
                className="rounded-md border border-red-500/20 bg-red-500/10 px-2 py-2 text-[11px] leading-relaxed text-red-200"
                data-testid="pm-document-search-error"
              >
                {searchError}
              </div>
            ) : validSearchResults.length > 0 ? (
              <div className="max-h-52 space-y-1 overflow-auto" data-testid="pm-document-search-results">
                {validSearchResults.map((result, index) => (
                  <SearchResultRow
                    key={`${readSearchResultPath(result)}-${index}`}
                    result={result}
                    workspace={workspace}
                    onSelect={() => void handleSearchResultSelect(result)}
                  />
                ))}
              </div>
            ) : (
              <div
                className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-500"
                data-testid="pm-document-search-empty"
              >
                后端未返回匹配文档
              </div>
            )}
          </div>
        )}

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
              description="来源：PM 文档合同"
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
                    disabled={Boolean(selectedDocumentVersion)}
                    title={selectedDocumentVersion ? '历史版本为只读' : undefined}
                    className={cn(
                      'flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-all',
                      viewMode === 'edit'
                        ? 'bg-amber-500/20 text-amber-400'
                        : selectedDocumentVersion
                          ? 'cursor-not-allowed text-slate-700'
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
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowDeletePanel((current) => !current)}
                  disabled={isDeleting}
                  data-testid="pm-document-delete-toggle"
                  className={cn(
                    'text-red-200 hover:bg-red-500/10 hover:text-red-100',
                    showDeletePanel && 'bg-red-500/10 text-red-100',
                  )}
                >
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                  删除
                </Button>
              </div>
            </div>

            {(showDeletePanel || deleteError || deleteResult) && (
              <div
                className={cn(
                  'border-b px-4 py-3 text-xs',
                  deleteError
                    ? 'border-red-500/20 bg-red-500/10 text-red-100'
                    : 'border-red-500/[0.15] bg-slate-950/45 text-slate-300',
                )}
                data-testid="pm-document-delete-panel"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 font-semibold text-red-100">
                      <Trash2 className="h-3.5 w-3.5" />
                      PM document delete
                    </div>
                    <div className="mt-1 flex items-center">
                      <EndpointBadge
                        endpoint={`/v2/pm/documents/${selectedFile.displayPath}`}
                        method="DELETE"
                        testId="pm-document-delete-endpoint"
                      />
                    </div>
                  </div>
                  <label className="flex cursor-pointer items-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-2 py-1.5 text-[11px] text-slate-300">
                    <input
                      type="checkbox"
                      checked={deleteBackingFile}
                      onChange={(event) => setDeleteBackingFile(event.target.checked)}
                      data-testid="pm-document-delete-delete-file"
                      className="h-3.5 w-3.5 accent-red-500"
                    />
                    删除实际文件
                  </label>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => { void handleDelete(); }}
                    disabled={isDeleting}
                    data-testid="pm-document-delete-submit"
                    className="border-red-500/35 bg-red-500/10 text-red-100 hover:bg-red-500/20 hover:text-red-50"
                  >
                    <Trash2 className={cn('mr-1.5 h-3.5 w-3.5', isDeleting && 'animate-pulse')} />
                    {isDeleting ? '删除中' : '确认删除'}
                  </Button>
                </div>
                <div
                  className="mt-2 rounded-md border border-white/10 bg-slate-950/55 px-2 py-1.5 text-[11px]"
                  data-testid="pm-document-delete-evidence"
                >
                  {deleteError ? (
                    <span className="text-red-100">{deleteError}</span>
                  ) : deleteResult ? (
                    <span className="text-emerald-300">
                      deleted · {deleteResult.path} · delete_file={String(deleteBackingFile)}
                    </span>
                  ) : (
                    <span className="text-slate-400">
                      默认仅删除 PM 文档记录；勾选后同时删除工作区文件。delete_file={String(deleteBackingFile)}
                    </span>
                  )}
                </div>
              </div>
            )}

            <DocumentVersionPanel
              versions={documentVersions}
              isLoading={isVersionsLoading}
              error={versionsError}
              diff={documentDiff}
              isDiffLoading={isDiffLoading}
              diffError={diffError}
              selectedVersion={selectedDocumentVersion}
              versionReadError={versionReadError}
              isVersionLoading={isVersionLoading}
              onLoadVersion={(version) => void handleLoadDocumentVersion(version)}
              onLoadCurrent={() => void handleLoadDocumentVersion(null)}
              onCompareLatest={() => void handleCompareLatest()}
            />

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

function normalizeDocumentPath(path: string): string {
  return path.replace(/\\/g, '/').toLowerCase();
}

function findDocumentInfo(nodes: FileNode[], path: string): PmDocumentInfo | undefined {
  const targetPath = normalizeDocumentPath(path);

  for (const node of nodes) {
    if (node.type === 'file' && normalizeDocumentPath(node.path) === targetPath) {
      return node.document;
    }

    if (node.children) {
      const nested = findDocumentInfo(node.children, path);
      if (nested) return nested;
    }
  }

  return undefined;
}

function readSearchResultPath(result: PmDocumentSearchResult): string {
  return typeof result.path === 'string' ? result.path.trim() : '';
}

function readSearchResultString(result: PmDocumentSearchResult, keys: string[]): string {
  for (const key of keys) {
    const value = result[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return '';
}

function readSearchResultSnippet(result: PmDocumentSearchResult): string {
  return readSearchResultString(result, ['snippet', 'match', 'preview', 'content', 'line_text']);
}

function basenameFromPath(displayPath: string, fallbackPath: string): string {
  const segments = displayPath.split('/').filter(Boolean);
  if (segments.length > 0) return segments[segments.length - 1];

  const fallbackSegments = fallbackPath.replace(/\\/g, '/').split('/').filter(Boolean);
  return fallbackSegments[fallbackSegments.length - 1] || fallbackPath;
}

function formatSearchResultMeta(result: PmDocumentSearchResult): string {
  const parts = ['PM search API'];
  const line = typeof result.line === 'number' ? result.line : result.line_number;
  if (typeof line === 'number') {
    parts.push(`line ${line}`);
  }
  if (typeof result.score === 'number') {
    parts.push(`score ${result.score.toFixed(2)}`);
  }
  return parts.join(' · ');
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

function SearchResultRow({
  result,
  workspace,
  onSelect,
}: {
  result: PmDocumentSearchResult;
  workspace: string;
  onSelect: () => void;
}) {
  const path = readSearchResultPath(result);
  const displayPath = displayDocumentPath(path, workspace);
  const snippet = readSearchResultSnippet(result);

  return (
    <button
      type="button"
      onClick={onSelect}
      className="w-full cursor-pointer rounded-md border border-white/10 bg-white/[0.035] px-2 py-2 text-left transition-colors hover:border-amber-400/30 hover:bg-amber-500/10"
      data-testid="pm-document-search-result"
      title={displayPath}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-slate-200">
          {basenameFromPath(displayPath, path)}
        </span>
        <span className="shrink-0 text-[9px] text-slate-500">{formatSearchResultMeta(result)}</span>
      </div>
      <p className="mt-0.5 truncate text-[10px] text-slate-500">{displayPath}</p>
      {snippet ? (
        <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-slate-300">{snippet}</p>
      ) : null}
    </button>
  );
}

function parseDocumentVersion(value: string): number | null {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function sortedDocumentVersions(versions: PmDocumentVersionInfo[]): PmDocumentVersionInfo[] {
  return [...versions].sort((left, right) => {
    const leftNumber = parseDocumentVersion(left.version);
    const rightNumber = parseDocumentVersion(right.version);
    if (leftNumber !== null && rightNumber !== null) return leftNumber - rightNumber;
    return left.version.localeCompare(right.version);
  });
}

function latestDocumentVersionPair(
  versions: PmDocumentVersionInfo[],
): { oldVersion: string; newVersion: string } | null {
  const sorted = sortedDocumentVersions(versions);
  if (sorted.length < 2) return null;
  return {
    oldVersion: sorted[sorted.length - 2].version,
    newVersion: sorted[sorted.length - 1].version,
  };
}

function DocumentVersionPanel({
  versions,
  isLoading,
  error,
  diff,
  isDiffLoading,
  diffError,
  selectedVersion,
  versionReadError,
  isVersionLoading,
  onLoadVersion,
  onLoadCurrent,
  onCompareLatest,
}: {
  versions: PmDocumentVersionInfo[];
  isLoading: boolean;
  error: string | null;
  diff: PmDocumentDiffResponse | null;
  isDiffLoading: boolean;
  diffError: string | null;
  selectedVersion: string | null;
  versionReadError: string | null;
  isVersionLoading: boolean;
  onLoadVersion: (version: string) => void;
  onLoadCurrent: () => void;
  onCompareLatest: () => void;
}) {
  const latestPair = latestDocumentVersionPair(versions);
  const visibleVersions = sortedDocumentVersions(versions).slice(-4).reverse();

  return (
    <div className="border-b border-white/10 bg-slate-950/20 px-4 py-2" data-testid="pm-document-version-panel">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
          <History className="h-3.5 w-3.5 text-amber-300" />
          版本历史
          <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-500">
            {versions.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {selectedVersion ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={onLoadCurrent}
              disabled={isVersionLoading}
              data-testid="pm-document-current-version"
              className="h-7 text-xs text-slate-400 hover:text-slate-200"
            >
              <RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', isVersionLoading && 'animate-spin')} />
              当前版本
            </Button>
          ) : null}
          {latestPair ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={onCompareLatest}
              disabled={isDiffLoading}
              className="h-7 text-xs text-slate-400 hover:text-slate-200"
            >
              <GitCompare className={cn('mr-1.5 h-3.5 w-3.5', isDiffLoading && 'animate-pulse')} />
              {isDiffLoading ? '比较中' : '比较最新'}
            </Button>
          ) : null}
        </div>
      </div>

      {(selectedVersion || versionReadError || isVersionLoading) ? (
        <div
          className={cn(
            'mb-2 rounded-md border px-2 py-1.5 text-[11px]',
            versionReadError
              ? 'border-red-500/20 bg-red-500/10 text-red-200'
              : 'border-amber-400/20 bg-amber-500/5 text-amber-100',
          )}
          data-testid="pm-document-version-read-evidence"
        >
          {versionReadError ? (
            versionReadError
          ) : isVersionLoading ? (
            '正在读取历史版本'
          ) : (
            `只读历史版本 · version=${selectedVersion}`
          )}
        </div>
      ) : null}

      {isLoading ? (
        <div className="flex items-center gap-2 text-[11px] text-slate-500">
          <RefreshCw className="h-3.5 w-3.5 animate-spin text-amber-400" />
          正在读取文档历史版本
        </div>
      ) : error ? (
        <div className="rounded-md border border-red-500/20 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-200">
          {error}
        </div>
      ) : visibleVersions.length > 0 ? (
        <div className="flex gap-2 overflow-x-auto pb-1" data-testid="pm-document-version-list">
          {visibleVersions.map((version) => (
            <div
              key={version.version}
              className="min-w-36 rounded-md border border-white/10 bg-white/[0.035] px-2 py-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-amber-200">v{version.version}</span>
                <span className="truncate text-[9px] text-slate-500">{version.created_by || 'unknown'}</span>
              </div>
              <p className="mt-1 truncate text-[10px] text-slate-400" title={version.change_summary}>
                {version.change_summary || 'no summary'}
              </p>
              <p className="mt-0.5 truncate text-[9px] text-slate-600">{version.created_at}</p>
              <button
                type="button"
                onClick={() => onLoadVersion(version.version)}
                disabled={isVersionLoading}
                data-testid="pm-document-version-open"
                className={cn(
                  'mt-2 w-full rounded border px-2 py-1 text-[10px] transition-colors',
                  selectedVersion === version.version
                    ? 'border-amber-400/40 bg-amber-500/[0.15] text-amber-100'
                    : 'border-white/10 bg-slate-950/40 text-slate-400 hover:border-amber-400/30 hover:text-amber-100',
                )}
              >
                {selectedVersion === version.version ? '正在查看' : '查看版本'}
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-[11px] text-slate-500" data-testid="pm-document-version-empty">
          后端未返回版本历史
        </div>
      )}

      {diffError ? (
        <div
          className="mt-2 rounded-md border border-red-500/20 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-200"
          data-testid="pm-document-diff-error"
        >
          {diffError}
        </div>
      ) : null}

      {diff ? (
        <div className="mt-2 rounded-md border border-cyan-400/20 bg-cyan-500/5 p-2" data-testid="pm-document-diff">
          <div className="mb-1 flex flex-wrap items-center gap-2 text-[10px] text-cyan-100">
            <span>v{diff.old_version} {'->'} v{diff.new_version}</span>
            <span>impact {diff.impact_score}</span>
            <span>sections {diff.changed_sections.length}</span>
            <span>+req {diff.added_requirements.length}</span>
            <span>-req {diff.removed_requirements.length}</span>
          </div>
          <pre className="max-h-28 overflow-auto rounded border border-white/10 bg-slate-950/70 p-2 text-[10px] leading-relaxed text-slate-300">
            {diff.diff_text || 'No textual diff returned'}
          </pre>
        </div>
      ) : null}
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
