import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, ChevronDown, ChevronRight, Edit3, Eye, FileText, FolderOpen, GitCompare, History, RefreshCw, Save, Search, Trash2, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { cn } from '@/app/components/ui/utils';
import { sanitizeMarkdown } from '@/app/utils/xssSanitizer';
import { pmDocumentService, } from '@/services/pmService';
import { toast } from 'sonner';
function EndpointBadge({ endpoint, method, testId, }) {
    return (_jsx("span", { className: "shrink-0 rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] font-medium text-slate-500", title: endpoint, "data-endpoint": endpoint, "data-testid": testId, children: method ? `${method} API` : 'API' }));
}
export function PMDocumentPanel({ workspace, selectedPath, onDocumentSelect, }) {
    const [fileTree, setFileTree] = useState([]);
    const [selectedFile, setSelectedFile] = useState(null);
    const [fileContent, setFileContent] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [isTreeLoading, setIsTreeLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isDeleting, setIsDeleting] = useState(false);
    const [treeError, setTreeError] = useState(null);
    const [contentError, setContentError] = useState(null);
    const [deleteError, setDeleteError] = useState(null);
    const [deleteResult, setDeleteResult] = useState(null);
    const [showDeletePanel, setShowDeletePanel] = useState(false);
    const [deleteBackingFile, setDeleteBackingFile] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState([]);
    const [isSearchLoading, setIsSearchLoading] = useState(false);
    const [searchError, setSearchError] = useState(null);
    const [documentVersions, setDocumentVersions] = useState([]);
    const [isVersionsLoading, setIsVersionsLoading] = useState(false);
    const [versionsError, setVersionsError] = useState(null);
    const [documentDiff, setDocumentDiff] = useState(null);
    const [isDiffLoading, setIsDiffLoading] = useState(false);
    const [diffError, setDiffError] = useState(null);
    const [viewMode, setViewMode] = useState('preview');
    const [selectedDocumentVersion, setSelectedDocumentVersion] = useState(null);
    const [isVersionLoading, setIsVersionLoading] = useState(false);
    const [versionReadError, setVersionReadError] = useState(null);
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
            if (!isCurrent)
                return;
            if (result.ok && result.data) {
                setSearchResults(result.data.results || []);
            }
            else {
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
    const toggleDirectory = useCallback((node) => {
        const updateTree = (nodes) => nodes.map((current) => {
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
    const loadDocumentVersions = useCallback(async (path) => {
        setIsVersionsLoading(true);
        setVersionsError(null);
        const result = await pmDocumentService.versions(path, workspace);
        if (result.ok && result.data) {
            setDocumentVersions(result.data.versions || []);
        }
        else {
            setDocumentVersions([]);
            setVersionsError(result.error || '加载 PM 文档版本失败');
        }
        setIsVersionsLoading(false);
    }, [workspace]);
    const loadDocumentNode = useCallback(async (node) => {
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
        }
        else {
            const message = result.error || '加载 PM 文档失败';
            setFileContent('');
            setContentError(message);
            toast.error(message);
        }
        setIsLoading(false);
    }, [loadDocumentVersions, onDocumentSelect, workspace]);
    const handleFileSelect = useCallback(async (node) => {
        if (node.type === 'directory') {
            toggleDirectory(node);
            return;
        }
        await loadDocumentNode(node);
    }, [loadDocumentNode, toggleDirectory]);
    const handleSearchResultSelect = useCallback(async (result) => {
        const path = readSearchResultPath(result);
        if (!path)
            return;
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
        if (!selectedFile)
            return;
        if (selectedDocumentVersion) {
            toast.error('历史版本为只读，无法保存');
            return;
        }
        setIsSaving(true);
        const result = await pmDocumentService.save(selectedFile.path, fileContent, 'Updated from PM document workspace', workspace);
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
        }
        else {
            toast.error(result.error || '保存失败');
        }
        setIsSaving(false);
    };
    const handleLoadDocumentVersion = async (version) => {
        if (!selectedFile)
            return;
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
        }
        else {
            setVersionReadError(result.error || '读取 PM 文档版本失败');
        }
        setIsVersionLoading(false);
    };
    const handleDelete = async () => {
        if (!selectedFile)
            return;
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
        }
        else {
            setDeleteError(result.error || '删除 PM 文档失败');
        }
        setIsDeleting(false);
    };
    const handleCompareLatest = async () => {
        if (!selectedFile)
            return;
        const pair = latestDocumentVersionPair(documentVersions);
        if (!pair)
            return;
        setIsDiffLoading(true);
        setDiffError(null);
        setDocumentDiff(null);
        const result = await pmDocumentService.compare(selectedFile.path, pair.oldVersion, pair.newVersion, workspace);
        if (result.ok && result.data) {
            setDocumentDiff(result.data);
        }
        else {
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
    return (_jsxs("div", { "data-testid": "pm-document-panel", className: "flex h-full", children: [_jsxs("div", { className: "flex w-64 flex-col border-r border-white/10 bg-slate-950/30", children: [_jsxs("div", { className: "flex h-14 items-center justify-between border-b border-white/10 px-3", children: [_jsx("span", { className: "text-sm font-medium text-slate-300", children: "\u6587\u6863" }), _jsx(Button, { variant: "ghost", size: "icon", className: "h-7 w-7 text-slate-400 hover:text-slate-200", onClick: () => void loadFileTree(), disabled: isTreeLoading, "aria-label": "\u5237\u65B0\u6587\u6863\u5217\u8868", children: _jsx(RefreshCw, { className: cn('h-3.5 w-3.5', isTreeLoading && 'animate-spin') }) })] }), _jsx("div", { className: "border-b border-white/10 p-2", children: _jsxs("div", { className: "relative", children: [_jsx(Search, { className: "absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" }), _jsx(Input, { placeholder: "\u641C\u7D22\u6587\u6863...", value: searchQuery, onChange: (event) => setSearchQuery(event.target.value), className: "h-8 border-white/10 bg-white/5 pl-7 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50" })] }) }), showBackendSearch && (_jsxs("div", { className: "border-b border-white/10 px-2 py-2", "data-testid": "pm-document-search-panel", children: [_jsxs("div", { className: "mb-1 flex items-center justify-between px-1 text-[10px] uppercase tracking-wider text-slate-500", children: [_jsx("span", { children: "\u5185\u5BB9\u641C\u7D22" }), _jsx("span", { "data-testid": "pm-document-search-count", children: isSearchLoading ? 'searching' : `${validSearchResults.length} matches` })] }), isSearchLoading ? (_jsxs("div", { className: "flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-400", children: [_jsx(RefreshCw, { className: "h-3.5 w-3.5 animate-spin text-amber-400" }), "\u6B63\u5728\u8C03\u7528\u540E\u7AEF\u6587\u6863\u641C\u7D22"] })) : searchError ? (_jsx("div", { className: "rounded-md border border-red-500/20 bg-red-500/10 px-2 py-2 text-[11px] leading-relaxed text-red-200", "data-testid": "pm-document-search-error", children: searchError })) : validSearchResults.length > 0 ? (_jsx("div", { className: "max-h-52 space-y-1 overflow-auto", "data-testid": "pm-document-search-results", children: validSearchResults.map((result, index) => (_jsx(SearchResultRow, { result: result, workspace: workspace, onSelect: () => void handleSearchResultSelect(result) }, `${readSearchResultPath(result)}-${index}`))) })) : (_jsx("div", { className: "rounded-md border border-white/10 bg-white/[0.03] px-2 py-2 text-[11px] text-slate-500", "data-testid": "pm-document-search-empty", children: "\u540E\u7AEF\u672A\u8FD4\u56DE\u5339\u914D\u6587\u6863" }))] })), _jsx("div", { className: "flex-1 overflow-auto py-2", "data-testid": "pm-document-tree", children: treeError ? (_jsx(PanelMessage, { icon: _jsx(AlertCircle, { className: "h-4 w-4 text-red-400" }), title: "\u6587\u6863\u7D22\u5F15\u4E0D\u53EF\u7528", description: treeError, testId: "pm-document-error" })) : isTreeLoading ? (_jsx(PanelMessage, { icon: _jsx(RefreshCw, { className: "h-4 w-4 animate-spin text-amber-400" }), title: "\u6B63\u5728\u8BFB\u53D6\u771F\u5B9E PM \u6587\u6863\u7D22\u5F15", description: "\u6765\u6E90\uFF1APM \u6587\u6863\u5408\u540C" })) : filteredTree.length > 0 ? (filteredTree.map((node) => (_jsx(FileTreeNode, { node: node, level: 0, selectedPath: selectedFile?.path ?? selectedPath ?? undefined, onSelect: handleFileSelect }, node.path)))) : (_jsx(PanelMessage, { icon: _jsx(FolderOpen, { className: "h-4 w-4 text-slate-500" }), title: "\u6682\u65E0\u5DF2\u8DDF\u8E2A\u6587\u6863", description: "\u8FD0\u884C Architect/PM \u5E76\u751F\u6210\u6587\u6863\u540E\uFF0C\u8FD9\u91CC\u624D\u4F1A\u663E\u793A\u771F\u5B9E\u5DE5\u4EF6\u3002", testId: "pm-document-empty" })) })] }), _jsx("div", { className: "flex min-w-0 flex-1 flex-col", children: selectedFile ? (_jsxs(_Fragment, { children: [_jsxs("div", { className: "flex h-14 items-center justify-between border-b border-white/10 bg-white/[0.02] px-4", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-3", children: [_jsx(FileText, { className: "h-4 w-4 flex-shrink-0 text-amber-400" }), _jsxs("div", { className: "min-w-0", children: [_jsx("h3", { className: "truncate text-sm font-medium text-slate-200", children: selectedFile.name }), _jsx("p", { className: "truncate text-[10px] text-slate-500", children: selectedFile.displayPath }), _jsx("p", { className: "mt-0.5 truncate text-[10px] text-amber-300/80", "data-testid": "pm-document-provenance", title: buildDocumentProvenance(selectedFile), children: buildDocumentProvenance(selectedFile) })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("div", { className: "flex items-center rounded-lg border border-white/10 bg-white/5 p-1", children: [_jsxs("button", { onClick: () => setViewMode('preview'), className: cn('flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-all', viewMode === 'preview'
                                                        ? 'bg-amber-500/20 text-amber-400'
                                                        : 'text-slate-500 hover:text-slate-300'), children: [_jsx(Eye, { className: "h-3 w-3" }), "\u9884\u89C8"] }), _jsxs("button", { onClick: () => setViewMode('edit'), disabled: Boolean(selectedDocumentVersion), title: selectedDocumentVersion ? '历史版本为只读' : undefined, className: cn('flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-all', viewMode === 'edit'
                                                        ? 'bg-amber-500/20 text-amber-400'
                                                        : selectedDocumentVersion
                                                            ? 'cursor-not-allowed text-slate-700'
                                                            : 'text-slate-500 hover:text-slate-300'), children: [_jsx(Edit3, { className: "h-3 w-3" }), "\u7F16\u8F91"] })] }), viewMode === 'edit' && (_jsxs(Button, { size: "sm", onClick: handleSave, disabled: isSaving, className: "bg-amber-600 text-white hover:bg-amber-700", children: [_jsx(Save, { className: cn('mr-1.5 h-3.5 w-3.5', isSaving && 'animate-pulse') }), isSaving ? '保存中' : '保存'] })), _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => setShowDeletePanel((current) => !current), disabled: isDeleting, "data-testid": "pm-document-delete-toggle", className: cn('text-red-200 hover:bg-red-500/10 hover:text-red-100', showDeletePanel && 'bg-red-500/10 text-red-100'), children: [_jsx(Trash2, { className: "mr-1.5 h-3.5 w-3.5" }), "\u5220\u9664"] })] })] }), (showDeletePanel || deleteError || deleteResult) && (_jsxs("div", { className: cn('border-b px-4 py-3 text-xs', deleteError
                                ? 'border-red-500/20 bg-red-500/10 text-red-100'
                                : 'border-red-500/[0.15] bg-slate-950/45 text-slate-300'), "data-testid": "pm-document-delete-panel", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 font-semibold text-red-100", children: [_jsx(Trash2, { className: "h-3.5 w-3.5" }), "PM document delete"] }), _jsx("div", { className: "mt-1 flex items-center", children: _jsx(EndpointBadge, { endpoint: `/v2/pm/documents/${selectedFile.displayPath}`, method: "DELETE", testId: "pm-document-delete-endpoint" }) })] }), _jsxs("label", { className: "flex cursor-pointer items-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-2 py-1.5 text-[11px] text-slate-300", children: [_jsx("input", { type: "checkbox", checked: deleteBackingFile, onChange: (event) => setDeleteBackingFile(event.target.checked), "data-testid": "pm-document-delete-delete-file", className: "h-3.5 w-3.5 accent-red-500" }), "\u5220\u9664\u5B9E\u9645\u6587\u4EF6"] }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => { void handleDelete(); }, disabled: isDeleting, "data-testid": "pm-document-delete-submit", className: "border-red-500/35 bg-red-500/10 text-red-100 hover:bg-red-500/20 hover:text-red-50", children: [_jsx(Trash2, { className: cn('mr-1.5 h-3.5 w-3.5', isDeleting && 'animate-pulse') }), isDeleting ? '删除中' : '确认删除'] })] }), _jsx("div", { className: "mt-2 rounded-md border border-white/10 bg-slate-950/55 px-2 py-1.5 text-[11px]", "data-testid": "pm-document-delete-evidence", children: deleteError ? (_jsx("span", { className: "text-red-100", children: deleteError })) : deleteResult ? (_jsxs("span", { className: "text-emerald-300", children: ["deleted \u00B7 ", deleteResult.path, " \u00B7 delete_file=", String(deleteBackingFile)] })) : (_jsxs("span", { className: "text-slate-400", children: ["\u9ED8\u8BA4\u4EC5\u5220\u9664 PM \u6587\u6863\u8BB0\u5F55\uFF1B\u52FE\u9009\u540E\u540C\u65F6\u5220\u9664\u5DE5\u4F5C\u533A\u6587\u4EF6\u3002delete_file=", String(deleteBackingFile)] })) })] })), _jsx(DocumentVersionPanel, { versions: documentVersions, isLoading: isVersionsLoading, error: versionsError, diff: documentDiff, isDiffLoading: isDiffLoading, diffError: diffError, selectedVersion: selectedDocumentVersion, versionReadError: versionReadError, isVersionLoading: isVersionLoading, onLoadVersion: (version) => void handleLoadDocumentVersion(version), onLoadCurrent: () => void handleLoadDocumentVersion(null), onCompareLatest: () => void handleCompareLatest() }), _jsx("div", { className: "flex-1 overflow-auto", children: isLoading ? (_jsx("div", { className: "flex h-full items-center justify-center text-slate-500", children: _jsx(RefreshCw, { className: "h-5 w-5 animate-spin" }) })) : contentError ? (_jsx("div", { className: "flex h-full items-center justify-center p-6", children: _jsxs("div", { className: "max-w-md rounded-lg border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-200", children: [_jsxs("div", { className: "flex items-center gap-2 font-medium", children: [_jsx(AlertCircle, { className: "h-4 w-4" }), "\u6587\u6863\u8BFB\u53D6\u5931\u8D25"] }), _jsx("p", { className: "mt-2 text-xs text-red-200/80", children: contentError })] }) })) : viewMode === 'edit' ? (_jsx("textarea", { value: fileContent, onChange: (event) => setFileContent(event.target.value), className: "h-full w-full resize-none bg-slate-950 p-4 font-mono text-sm text-slate-200 focus:outline-none", spellCheck: false })) : (_jsx(MarkdownPreview, { content: fileContent })) })] })) : (_jsxs("div", { className: "flex h-full flex-col items-center justify-center text-slate-500", children: [_jsx(FolderOpen, { className: "mb-4 h-12 w-12 opacity-20" }), _jsx("p", { className: "text-sm", children: "\u9009\u62E9\u6587\u6863\u4EE5\u67E5\u770B" }), _jsx("p", { className: "mt-1 text-xs text-slate-600", children: "\u5DE6\u4FA7\u53EA\u663E\u793A PM \u5DF2\u8DDF\u8E2A\u7684\u771F\u5B9E\u6587\u6863" })] })) })] }));
}
function formatDocumentTimestamp(value) {
    const raw = typeof value === 'string' ? value.trim() : '';
    if (!raw)
        return 'modified unknown';
    return `modified ${raw}`;
}
function buildDocumentProvenance(node) {
    const version = String(node.document?.current_version || '-').trim() || '-';
    const modified = formatDocumentTimestamp(node.document?.last_modified);
    return `PM docs API · v${version} · ${modified}`;
}
function normalizeDocumentPath(path) {
    return path.replace(/\\/g, '/').toLowerCase();
}
function findDocumentInfo(nodes, path) {
    const targetPath = normalizeDocumentPath(path);
    for (const node of nodes) {
        if (node.type === 'file' && normalizeDocumentPath(node.path) === targetPath) {
            return node.document;
        }
        if (node.children) {
            const nested = findDocumentInfo(node.children, path);
            if (nested)
                return nested;
        }
    }
    return undefined;
}
function readSearchResultPath(result) {
    return typeof result.path === 'string' ? result.path.trim() : '';
}
function readSearchResultString(result, keys) {
    for (const key of keys) {
        const value = result[key];
        if (typeof value === 'string' && value.trim()) {
            return value.trim();
        }
    }
    return '';
}
function readSearchResultSnippet(result) {
    return readSearchResultString(result, ['snippet', 'match', 'preview', 'content', 'line_text']);
}
function basenameFromPath(displayPath, fallbackPath) {
    const segments = displayPath.split('/').filter(Boolean);
    if (segments.length > 0)
        return segments[segments.length - 1];
    const fallbackSegments = fallbackPath.replace(/\\/g, '/').split('/').filter(Boolean);
    return fallbackSegments[fallbackSegments.length - 1] || fallbackPath;
}
function formatSearchResultMeta(result) {
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
function displayDocumentPath(path, workspace) {
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
function sortTree(nodes) {
    return [...nodes]
        .sort((left, right) => {
        if (left.type !== right.type)
            return left.type === 'directory' ? -1 : 1;
        return left.name.localeCompare(right.name);
    })
        .map((node) => ({
        ...node,
        children: node.children ? sortTree(node.children) : undefined,
    }));
}
function buildFileTree(documents, workspace) {
    const roots = [];
    const directories = new Map();
    for (const document of documents) {
        const displayPath = displayDocumentPath(document.path, workspace);
        const segments = displayPath.split('/').filter(Boolean);
        if (segments.length === 0)
            continue;
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
function PanelMessage({ icon, title, description, testId, }) {
    return (_jsxs("div", { "data-testid": testId, className: "px-3 py-6 text-center", children: [_jsx("div", { className: "mx-auto mb-2 flex h-8 w-8 items-center justify-center rounded-lg bg-white/5", children: icon }), _jsx("p", { className: "text-xs font-medium text-slate-300", children: title }), _jsx("p", { className: "mt-1 text-[10px] leading-relaxed text-slate-500", children: description })] }));
}
function SearchResultRow({ result, workspace, onSelect, }) {
    const path = readSearchResultPath(result);
    const displayPath = displayDocumentPath(path, workspace);
    const snippet = readSearchResultSnippet(result);
    return (_jsxs("button", { type: "button", onClick: onSelect, className: "w-full cursor-pointer rounded-md border border-white/10 bg-white/[0.035] px-2 py-2 text-left transition-colors hover:border-amber-400/30 hover:bg-amber-500/10", "data-testid": "pm-document-search-result", title: displayPath, children: [_jsxs("div", { className: "flex min-w-0 items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-xs font-medium text-slate-200", children: basenameFromPath(displayPath, path) }), _jsx("span", { className: "shrink-0 text-[9px] text-slate-500", children: formatSearchResultMeta(result) })] }), _jsx("p", { className: "mt-0.5 truncate text-[10px] text-slate-500", children: displayPath }), snippet ? (_jsx("p", { className: "mt-1 line-clamp-2 text-[11px] leading-relaxed text-slate-300", children: snippet })) : null] }));
}
function parseDocumentVersion(value) {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
}
function sortedDocumentVersions(versions) {
    return [...versions].sort((left, right) => {
        const leftNumber = parseDocumentVersion(left.version);
        const rightNumber = parseDocumentVersion(right.version);
        if (leftNumber !== null && rightNumber !== null)
            return leftNumber - rightNumber;
        return left.version.localeCompare(right.version);
    });
}
function latestDocumentVersionPair(versions) {
    const sorted = sortedDocumentVersions(versions);
    if (sorted.length < 2)
        return null;
    return {
        oldVersion: sorted[sorted.length - 2].version,
        newVersion: sorted[sorted.length - 1].version,
    };
}
function DocumentVersionPanel({ versions, isLoading, error, diff, isDiffLoading, diffError, selectedVersion, versionReadError, isVersionLoading, onLoadVersion, onLoadCurrent, onCompareLatest, }) {
    const latestPair = latestDocumentVersionPair(versions);
    const visibleVersions = sortedDocumentVersions(versions).slice(-4).reverse();
    return (_jsxs("div", { className: "border-b border-white/10 bg-slate-950/20 px-4 py-2", "data-testid": "pm-document-version-panel", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-medium text-slate-300", children: [_jsx(History, { className: "h-3.5 w-3.5 text-amber-300" }), "\u7248\u672C\u5386\u53F2", _jsx("span", { className: "rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-500", children: versions.length })] }), _jsxs("div", { className: "flex items-center gap-2", children: [selectedVersion ? (_jsxs(Button, { variant: "ghost", size: "sm", onClick: onLoadCurrent, disabled: isVersionLoading, "data-testid": "pm-document-current-version", className: "h-7 text-xs text-slate-400 hover:text-slate-200", children: [_jsx(RefreshCw, { className: cn('mr-1.5 h-3.5 w-3.5', isVersionLoading && 'animate-spin') }), "\u5F53\u524D\u7248\u672C"] })) : null, latestPair ? (_jsxs(Button, { variant: "ghost", size: "sm", onClick: onCompareLatest, disabled: isDiffLoading, className: "h-7 text-xs text-slate-400 hover:text-slate-200", children: [_jsx(GitCompare, { className: cn('mr-1.5 h-3.5 w-3.5', isDiffLoading && 'animate-pulse') }), isDiffLoading ? '比较中' : '比较最新'] })) : null] })] }), (selectedVersion || versionReadError || isVersionLoading) ? (_jsx("div", { className: cn('mb-2 rounded-md border px-2 py-1.5 text-[11px]', versionReadError
                    ? 'border-red-500/20 bg-red-500/10 text-red-200'
                    : 'border-amber-400/20 bg-amber-500/5 text-amber-100'), "data-testid": "pm-document-version-read-evidence", children: versionReadError ? (versionReadError) : isVersionLoading ? ('正在读取历史版本') : (`只读历史版本 · version=${selectedVersion}`) })) : null, isLoading ? (_jsxs("div", { className: "flex items-center gap-2 text-[11px] text-slate-500", children: [_jsx(RefreshCw, { className: "h-3.5 w-3.5 animate-spin text-amber-400" }), "\u6B63\u5728\u8BFB\u53D6\u6587\u6863\u5386\u53F2\u7248\u672C"] })) : error ? (_jsx("div", { className: "rounded-md border border-red-500/20 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-200", children: error })) : visibleVersions.length > 0 ? (_jsx("div", { className: "flex gap-2 overflow-x-auto pb-1", "data-testid": "pm-document-version-list", children: visibleVersions.map((version) => (_jsxs("div", { className: "min-w-36 rounded-md border border-white/10 bg-white/[0.035] px-2 py-1.5", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsxs("span", { className: "text-xs font-medium text-amber-200", children: ["v", version.version] }), _jsx("span", { className: "truncate text-[9px] text-slate-500", children: version.created_by || 'unknown' })] }), _jsx("p", { className: "mt-1 truncate text-[10px] text-slate-400", title: version.change_summary, children: version.change_summary || 'no summary' }), _jsx("p", { className: "mt-0.5 truncate text-[9px] text-slate-600", children: version.created_at }), _jsx("button", { type: "button", onClick: () => onLoadVersion(version.version), disabled: isVersionLoading, "data-testid": "pm-document-version-open", className: cn('mt-2 w-full rounded border px-2 py-1 text-[10px] transition-colors', selectedVersion === version.version
                                ? 'border-amber-400/40 bg-amber-500/[0.15] text-amber-100'
                                : 'border-white/10 bg-slate-950/40 text-slate-400 hover:border-amber-400/30 hover:text-amber-100'), children: selectedVersion === version.version ? '正在查看' : '查看版本' })] }, version.version))) })) : (_jsx("div", { className: "text-[11px] text-slate-500", "data-testid": "pm-document-version-empty", children: "\u540E\u7AEF\u672A\u8FD4\u56DE\u7248\u672C\u5386\u53F2" })), diffError ? (_jsx("div", { className: "mt-2 rounded-md border border-red-500/20 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-200", "data-testid": "pm-document-diff-error", children: diffError })) : null, diff ? (_jsxs("div", { className: "mt-2 rounded-md border border-cyan-400/20 bg-cyan-500/5 p-2", "data-testid": "pm-document-diff", children: [_jsxs("div", { className: "mb-1 flex flex-wrap items-center gap-2 text-[10px] text-cyan-100", children: [_jsxs("span", { children: ["v", diff.old_version, " ", '->', " v", diff.new_version] }), _jsxs("span", { children: ["impact ", diff.impact_score] }), _jsxs("span", { children: ["sections ", diff.changed_sections.length] }), _jsxs("span", { children: ["+req ", diff.added_requirements.length] }), _jsxs("span", { children: ["-req ", diff.removed_requirements.length] })] }), _jsx("pre", { className: "max-h-28 overflow-auto rounded border border-white/10 bg-slate-950/70 p-2 text-[10px] leading-relaxed text-slate-300", children: diff.diff_text || 'No textual diff returned' })] })) : null] }));
}
function FileTreeNode({ node, level, selectedPath, onSelect }) {
    const isSelected = selectedPath === node.path;
    const isDirectory = node.type === 'directory';
    const paddingLeft = level * 12 + 12;
    return (_jsxs("div", { children: [_jsxs("div", { onClick: () => onSelect(node), style: { paddingLeft }, className: cn('flex cursor-pointer items-center gap-1.5 py-1.5 pr-3 transition-colors', isSelected
                    ? 'bg-amber-500/10 text-amber-400'
                    : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'), children: [isDirectory ? (_jsx("span", { className: "text-slate-500", children: node.expanded ? (_jsx(ChevronDown, { className: "h-3.5 w-3.5" })) : (_jsx(ChevronRight, { className: "h-3.5 w-3.5" })) })) : (_jsx("span", { className: "w-3.5" })), isDirectory ? (_jsx(FolderOpen, { className: "h-4 w-4 text-amber-500/70" })) : (_jsx(FileText, { className: "h-4 w-4 text-slate-500" })), _jsx("span", { className: "truncate text-xs", children: node.name }), node.document && (_jsxs("span", { className: "ml-auto rounded bg-white/5 px-1.5 py-0.5 text-[9px] text-slate-500", children: ["v", node.document.current_version || '-'] }))] }), isDirectory && node.expanded && node.children && (_jsx("div", { children: node.children.map((child) => (_jsx(FileTreeNode, { node: child, level: level + 1, selectedPath: selectedPath, onSelect: onSelect }, child.path))) }))] }));
}
function MarkdownPreview({ content }) {
    const renderMarkdown = (text) => {
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
    return (_jsx("div", { className: "prose prose-invert prose-amber max-w-none p-6", dangerouslySetInnerHTML: { __html: sanitizeMarkdown(renderMarkdown(content)) } }));
}
function filterTree(nodes, query) {
    return nodes.reduce((acc, node) => {
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
        }
        else if (matches) {
            acc.push(node);
        }
        return acc;
    }, []);
}
