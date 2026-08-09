import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Editor, { loader } from '@monaco-editor/react';
import { AlertTriangle, Binary, Braces, ChevronDown, ChevronRight, Code2, Cpu, File, FileCode2, FileImage, FileJson, FileText, Folder, FolderGit2, Gauge, HardDrive, Image as ImageIcon, Loader2, Lock, Package, RefreshCw, Search, Server, TerminalSquare, Workflow, X, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { cn } from '@/app/components/ui/utils';
import { listWorkspaceFileTree, readScopedFile, } from '@/services';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
const SOURCE_SCOPES = [
    { id: 'workspace', label: 'Workspace', description: '当前项目目录' },
    { id: 'runtime', label: 'Runtime', description: '运行证据目录' },
    { id: 'config', label: 'Config', description: 'KernelOne 配置' },
];
const TEXT_PREVIEW_LIMIT = 600000;
const monacoGlobal = globalThis;
let localMonacoConfiguration = null;
function configureLocalMonaco() {
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
                getWorker(_workerId, label) {
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
function formatBytes(value) {
    const size = Number(value || 0);
    if (!Number.isFinite(size) || size <= 0)
        return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let current = size;
    let index = 0;
    while (current >= 1024 && index < units.length - 1) {
        current /= 1024;
        index += 1;
    }
    return `${current >= 10 || index === 0 ? current.toFixed(0) : current.toFixed(1)} ${units[index]}`;
}
function formatTimestamp(value) {
    if (!value)
        return 'n/a';
    const date = new Date(value);
    if (Number.isNaN(date.getTime()))
        return value;
    return date.toLocaleString();
}
function nodeKindLabel(node) {
    if (!node)
        return '未选择';
    if (node.type === 'directory')
        return '目录';
    if (node.is_binary)
        return '二进制';
    return node.language || node.extension || 'text';
}
function monacoLanguage(node) {
    const language = String(node?.language || '').trim();
    if (!language)
        return 'plaintext';
    if (language === 'shell')
        return 'shell';
    if (language === 'log')
        return 'plaintext';
    return language;
}
function fileIcon(node, className = 'size-4') {
    if (node.type === 'directory') {
        if (node.name === '.git')
            return _jsx(FolderGit2, { className: cn(className, 'text-orange-300') });
        if (node.name === 'node_modules')
            return _jsx(Package, { className: cn(className, 'text-lime-300') });
        return _jsx(Folder, { className: cn(className, 'text-cyan-300') });
    }
    const icon = node.icon || '';
    const extension = node.extension || '';
    if (node.is_binary) {
        if (icon === 'image' || ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.avif'].includes(extension)) {
            return _jsx(FileImage, { className: cn(className, 'text-fuchsia-300') });
        }
        return _jsx(Binary, { className: cn(className, 'text-slate-400') });
    }
    if (icon === 'react')
        return _jsx(Code2, { className: cn(className, 'text-sky-300') });
    if (icon === 'typescript')
        return _jsx(FileCode2, { className: cn(className, 'text-blue-300') });
    if (icon === 'javascript')
        return _jsx(FileCode2, { className: cn(className, 'text-yellow-300') });
    if (icon === 'python')
        return _jsx(TerminalSquare, { className: cn(className, 'text-emerald-300') });
    if (icon === 'rust')
        return _jsx(Cpu, { className: cn(className, 'text-orange-300') });
    if (icon === 'go')
        return _jsx(Workflow, { className: cn(className, 'text-cyan-300') });
    if (icon === 'json')
        return _jsx(FileJson, { className: cn(className, 'text-amber-300') });
    if (icon === 'markdown')
        return _jsx(FileText, { className: cn(className, 'text-violet-200') });
    if (icon === 'yaml')
        return _jsx(Braces, { className: cn(className, 'text-rose-300') });
    if (icon === 'image')
        return _jsx(ImageIcon, { className: cn(className, 'text-fuchsia-300') });
    return _jsx(File, { className: cn(className, 'text-slate-300') });
}
function walkNodes(node, output = []) {
    if (!node)
        return output;
    output.push(node);
    for (const child of node.children || []) {
        walkNodes(child, output);
    }
    return output;
}
function findFirstTextFile(node) {
    if (!node)
        return null;
    if (node.type === 'file' && !node.is_binary)
        return node;
    for (const child of node.children || []) {
        const found = findFirstTextFile(child);
        if (found)
            return found;
    }
    return null;
}
function nodeMatchesQuery(node, query) {
    if (!query)
        return true;
    if (node.path.toLowerCase().includes(query) || node.name.toLowerCase().includes(query)) {
        return true;
    }
    return (node.children || []).some((child) => nodeMatchesQuery(child, query));
}
function TextCodePreview({ content, language, degraded = false, }) {
    return (_jsxs("div", { "data-testid": "workspace-code-text-preview", className: "flex size-full min-h-0 flex-col bg-[#080c18] text-slate-200", children: [degraded ? (_jsx("div", { className: "shrink-0 border-b border-amber-300/15 bg-amber-300/10 px-4 py-2 text-xs text-amber-100", children: "Monaco \u7F16\u8F91\u5668\u672A\u5C31\u7EEA\uFF0C\u5DF2\u5207\u6362\u4E3A\u7A33\u5B9A\u6587\u672C\u9884\u89C8\u3002" })) : null, _jsx("pre", { className: "min-h-0 flex-1 overflow-auto p-4 font-mono text-[12px] leading-6 text-slate-200", children: _jsx("code", { "data-language": language, children: content || ' ' }) })] }));
}
function FileTreeRow({ node, selectedPath, expanded, query, onToggle, onSelect, }) {
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
    return (_jsxs("div", { children: [_jsxs("button", { type: "button", "data-testid": isDirectory ? 'workspace-file-tree-dir' : 'workspace-file-tree-file', "data-file-path": node.path, onClick: () => isDirectory ? onToggle(node) : onSelect(node), className: cn('group flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs transition-colors', isSelected
                    ? 'border border-cyan-300/40 bg-cyan-300/15 text-cyan-50 shadow-[0_0_18px_rgba(34,211,238,0.12)]'
                    : 'text-slate-300 hover:bg-white/10 hover:text-white'), style: { paddingLeft: `${8 + Math.min(node.depth, 12) * 12}px` }, children: [_jsx("span", { className: "flex size-4 shrink-0 items-center justify-center text-slate-500", children: isDirectory ? (isExpanded ? _jsx(ChevronDown, { className: "size-3.5" }) : _jsx(ChevronRight, { className: "size-3.5" })) : (_jsx("span", { className: "size-3.5" })) }), fileIcon(node), _jsx("span", { className: "min-w-0 flex-1 truncate font-medium", title: node.path || node.name, children: node.name }), node.is_symlink ? _jsx(Lock, { className: "size-3 text-amber-300" }) : null, node.type === 'file' ? (_jsx("span", { className: "shrink-0 font-mono text-[10px] text-slate-500", children: formatBytes(node.size) })) : null] }), isDirectory && (isExpanded || normalizedQuery) ? (_jsx("div", { children: visibleChildren.map((child) => (_jsx(FileTreeRow, { node: child, selectedPath: selectedPath, expanded: expanded, query: query, onToggle: onToggle, onSelect: onSelect }, child.id))) })) : null] }));
}
export function WorkspaceFilesPage({ workspace, onBackToMain }) {
    const [scope, setScope] = useState('workspace');
    const [includeIgnored, setIncludeIgnored] = useState(false);
    const [query, setQuery] = useState('');
    const [tree, setTree] = useState(null);
    const [treeLoading, setTreeLoading] = useState(false);
    const [treeError, setTreeError] = useState('');
    const [expanded, setExpanded] = useState(new Set());
    const [selectedNode, setSelectedNode] = useState(null);
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
        const nextExpanded = new Set([result.data.tree.id]);
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
            if (!mounted)
                return;
            setIsMonacoReady(true);
            setMonacoLoadFailed(false);
        })
            .catch(() => {
            if (!mounted)
                return;
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
            if (controller.signal.aborted)
                return;
            if (!result.ok || !result.data) {
                setContent('');
                setPreviewError(result.error || '文件读取失败');
                return;
            }
            setContent(result.data.content || '');
        })
            .catch((error) => {
            if (controller.signal.aborted)
                return;
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
    const visibleFileCount = useMemo(() => allNodes.filter((node) => node.type === 'file' && (!query || node.path.toLowerCase().includes(query.toLowerCase()))).length, [allNodes, query]);
    const handleToggle = useCallback((node) => {
        setExpanded((previous) => {
            const next = new Set(previous);
            if (next.has(node.id))
                next.delete(node.id);
            else
                next.add(node.id);
            return next;
        });
    }, []);
    const handleSelect = useCallback((node) => {
        setSelectedNode(node);
    }, []);
    const selectedTitle = selectedNode?.path || selectedNode?.name || '选择文件';
    const workspaceName = workspaceLabel(workspace, '未选定工作区');
    const treeWarning = tree && (tree.truncated || tree.stats.omitted > 0) ? tree : null;
    return (_jsxs("div", { "data-testid": "workspace-files-page", className: "polaris-soft-scope flex size-full flex-col overflow-hidden bg-bg text-text-main", children: [_jsxs("div", { className: "flex h-14 shrink-0 items-center justify-between border-b border-white/10 bg-slate-950/80 px-4 backdrop-blur-xl", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-3", children: [_jsx("button", { type: "button", onClick: onBackToMain, className: "rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-semibold text-slate-200 transition-colors hover:border-cyan-300/40 hover:text-cyan-100", children: "\u8FD4\u56DE" }), _jsx("div", { className: "flex size-9 items-center justify-center rounded-lg border border-cyan-300/25 bg-cyan-300/10 text-cyan-200 shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]", children: _jsx(HardDrive, { className: "size-4" }) }), _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("h1", { className: "truncate text-sm font-semibold text-white", children: "Workspace \u6587\u4EF6\u6D4F\u89C8\u5668" }), _jsx("span", { className: "rounded border border-cyan-300/20 bg-cyan-300/10 px-1.5 py-0.5 font-mono text-[10px] uppercase text-cyan-200", children: "SaaS ready" })] }), _jsx("p", { className: "truncate text-xs text-slate-400", title: workspace, children: workspaceName })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("div", { className: "hidden items-center gap-1 rounded-lg border border-white/10 bg-white/5 p-1 md:flex", children: SOURCE_SCOPES.map((item) => (_jsx("button", { type: "button", onClick: () => setScope(item.id), className: cn('rounded-md px-2.5 py-1 text-xs font-medium transition-colors', scope === item.id ? 'bg-cyan-300/15 text-cyan-100' : 'text-slate-400 hover:text-white'), title: item.description, children: item.label }, item.id))) }), _jsxs(Button, { type: "button", variant: "ghost", size: "sm", onClick: () => setIncludeIgnored((value) => !value), className: cn('border border-white/10 text-xs', includeIgnored ? 'bg-amber-300/15 text-amber-100' : 'text-slate-300'), children: [_jsx(Package, { className: "size-3.5" }), "vendor/cache"] }), _jsxs(Button, { type: "button", variant: "ghost", size: "sm", onClick: () => { void loadTree(); }, className: "border border-white/10 text-slate-200", children: [treeLoading ? _jsx(Loader2, { className: "size-3.5 animate-spin" }) : _jsx(RefreshCw, { className: "size-3.5" }), "\u5237\u65B0"] })] })] }), _jsxs("div", { className: "grid min-h-0 flex-1 grid-cols-[minmax(260px,360px)_minmax(0,1fr)_minmax(240px,300px)] overflow-hidden", children: [_jsxs("aside", { className: "flex min-h-0 flex-col border-r border-white/10 bg-slate-950/60", children: [_jsxs("div", { className: "space-y-3 border-b border-white/10 p-3", children: [_jsxs("div", { className: "relative", children: [_jsx(Search, { className: "pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-slate-500" }), _jsx(Input, { value: query, onChange: (event) => setQuery(event.currentTarget.value), placeholder: "\u641C\u7D22\u6587\u4EF6...", className: "h-9 border-white/10 bg-slate-900/80 pl-9 text-xs text-slate-100 placeholder:text-slate-500" }), query ? (_jsx("button", { type: "button", onClick: () => setQuery(''), className: "absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-500 hover:text-slate-100", "aria-label": "\u6E05\u7A7A\u641C\u7D22", children: _jsx(X, { className: "size-3.5" }) })) : null] }), _jsxs("div", { className: "grid grid-cols-3 gap-2", children: [_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 p-2", children: [_jsx("div", { className: "font-mono text-sm text-white", children: tree?.stats.files.toLocaleString() || '0' }), _jsx("div", { className: "text-[10px] text-slate-500", children: "files" })] }), _jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 p-2", children: [_jsx("div", { className: "font-mono text-sm text-white", children: tree?.stats.directories.toLocaleString() || '0' }), _jsx("div", { className: "text-[10px] text-slate-500", children: "dirs" })] }), _jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 p-2", children: [_jsx("div", { className: "font-mono text-sm text-white", children: visibleFileCount.toLocaleString() }), _jsx("div", { className: "text-[10px] text-slate-500", children: "visible" })] })] })] }), _jsx("div", { className: "min-h-0 flex-1 overflow-auto p-2", children: treeError ? (_jsx("div", { className: "rounded-lg border border-red-400/20 bg-red-500/10 p-3 text-xs text-red-100", children: treeError })) : treeLoading && !tree ? (_jsxs("div", { className: "flex h-full items-center justify-center text-xs text-slate-500", children: [_jsx(Loader2, { className: "mr-2 size-4 animate-spin" }), "\u6B63\u5728\u626B\u63CF\u76EE\u5F55"] })) : tree?.tree ? (_jsx(FileTreeRow, { node: tree.tree, selectedPath: selectedNode?.path || '', expanded: expanded, query: query, onToggle: handleToggle, onSelect: handleSelect })) : (_jsx("div", { className: "flex h-full items-center justify-center text-xs text-slate-500", children: "\u6682\u65E0\u6587\u4EF6\u6811" })) })] }), _jsxs("main", { className: "flex min-h-0 flex-col bg-[#080c18]", children: [_jsxs("div", { className: "flex h-12 shrink-0 items-center justify-between border-b border-white/10 bg-slate-950/70 px-4", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [selectedNode ? fileIcon(selectedNode, 'size-4') : _jsx(File, { className: "size-4 text-slate-500" }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-sm font-semibold text-slate-100", title: selectedTitle, children: selectedTitle }), _jsx("div", { className: "font-mono text-[10px] text-slate-500", children: nodeKindLabel(selectedNode) })] })] }), selectedNode?.type === 'file' && !selectedNode.is_binary ? (_jsxs("span", { className: "rounded border border-cyan-300/20 bg-cyan-300/10 px-2 py-1 font-mono text-[10px] text-cyan-100", children: ["read-only \u00B7 ", monacoLanguage(selectedNode)] })) : null] }), _jsx("div", { className: "min-h-0 flex-1", children: !selectedNode ? (_jsxs("div", { className: "flex h-full flex-col items-center justify-center text-slate-500", children: [_jsx(Server, { className: "mb-3 size-10 text-cyan-300/30" }), _jsx("p", { className: "text-sm", children: "\u9009\u62E9\u6587\u4EF6\u67E5\u770B\u4EE3\u7801" })] })) : selectedNode.type === 'directory' ? (_jsxs("div", { className: "flex h-full flex-col items-center justify-center text-slate-500", children: [_jsx(Folder, { className: "mb-3 size-10 text-cyan-300/30" }), _jsx("p", { className: "text-sm", children: "\u8FD9\u662F\u76EE\u5F55\uFF0C\u9009\u62E9\u4E00\u4E2A\u6587\u4EF6\u6253\u5F00\u9884\u89C8" })] })) : selectedNode.is_binary ? (_jsxs("div", { className: "flex h-full flex-col items-center justify-center px-8 text-center text-slate-500", children: [fileIcon(selectedNode, 'mb-4 size-12'), _jsx("p", { className: "text-sm text-slate-300", children: "\u4E8C\u8FDB\u5236\u6587\u4EF6\u4E0D\u5728\u6D4F\u89C8\u5668\u4E2D\u76F4\u63A5\u8BFB\u53D6" }), _jsx("p", { className: "mt-2 max-w-md text-xs leading-6 text-slate-500", children: "\u6587\u4EF6\u7C7B\u578B\u3001\u5927\u5C0F\u548C\u4FEE\u6539\u65F6\u95F4\u4ECD\u53EF\u5728\u53F3\u4FA7\u67E5\u770B\u3002\u4EE3\u7801\u9884\u89C8\u53EA\u8BFB\u53D6\u6587\u672C\u6587\u4EF6\uFF0C\u907F\u514D\u628A\u56FE\u7247\u3001\u5B57\u4F53\u6216\u6784\u5EFA\u4EA7\u7269\u8BEF\u89E3\u7801\u3002" })] })) : previewLoading ? (_jsxs("div", { className: "flex h-full items-center justify-center text-xs text-slate-500", children: [_jsx(Loader2, { className: "mr-2 size-4 animate-spin" }), "\u6B63\u5728\u8BFB\u53D6\u6587\u4EF6"] })) : previewError ? (_jsxs("div", { className: "m-4 rounded-lg border border-red-400/20 bg-red-500/10 p-4 text-sm text-red-100", children: [_jsx(AlertTriangle, { className: "mb-2 size-4" }), previewError] })) : !isMonacoReady || monacoLoadFailed ? (_jsx(TextCodePreview, { content: content.slice(0, TEXT_PREVIEW_LIMIT), language: monacoLanguage(selectedNode), degraded: monacoLoadFailed })) : (_jsx(Editor, { height: "100%", language: monacoLanguage(selectedNode), value: content.slice(0, TEXT_PREVIEW_LIMIT), theme: "vs-dark", loading: (_jsx(TextCodePreview, { content: content.slice(0, TEXT_PREVIEW_LIMIT), language: monacoLanguage(selectedNode) })), onMount: () => {
                                        setMonacoLoadFailed(false);
                                    }, options: {
                                        readOnly: true,
                                        minimap: { enabled: true },
                                        fontSize: 13,
                                        fontLigatures: true,
                                        wordWrap: 'on',
                                        scrollBeyondLastLine: false,
                                        automaticLayout: true,
                                        renderLineHighlight: 'line',
                                    } })) })] }), _jsxs("aside", { className: "flex min-h-0 flex-col border-l border-white/10 bg-slate-950/70 p-4", children: [_jsxs("div", { className: "mb-4 flex items-center gap-2", children: [_jsx(Gauge, { className: "size-4 text-cyan-200" }), _jsx("h2", { className: "text-sm font-semibold text-white", children: "\u6587\u4EF6\u6001\u52BF" })] }), _jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 p-3", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-slate-500", children: "scope" }), _jsx("div", { className: "mt-1 text-sm font-semibold text-slate-100", children: scope })] }), _jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 p-3", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-slate-500", children: "selected" }), _jsx("div", { className: "mt-1 break-all font-mono text-xs text-slate-200", children: selectedTitle })] }), _jsxs("div", { className: "grid grid-cols-2 gap-2", children: [_jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 p-3", children: [_jsx("div", { className: "text-[10px] text-slate-500", children: "size" }), _jsx("div", { className: "mt-1 font-mono text-sm text-white", children: formatBytes(selectedNode?.size) })] }), _jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 p-3", children: [_jsx("div", { className: "text-[10px] text-slate-500", children: "binary" }), _jsx("div", { className: "mt-1 font-mono text-sm text-white", children: selectedNode?.is_binary ? 'yes' : 'no' })] })] }), _jsxs("div", { className: "rounded-lg border border-white/10 bg-white/5 p-3", children: [_jsx("div", { className: "text-[10px] uppercase tracking-wide text-slate-500", children: "modified" }), _jsx("div", { className: "mt-1 text-xs text-slate-200", children: formatTimestamp(selectedNode?.mtime) })] }), treeWarning ? (_jsxs("div", { className: "rounded-lg border border-amber-300/20 bg-amber-300/10 p-3 text-xs text-amber-100", children: [_jsxs("div", { className: "mb-1 flex items-center gap-2 font-semibold", children: [_jsx(AlertTriangle, { className: "size-3.5" }), "\u76EE\u5F55\u5DF2\u9632\u62A4\u88C1\u526A"] }), _jsxs("p", { className: "leading-5", children: ["omitted ", treeWarning.stats.omitted.toLocaleString(), " \u00B7 max ", treeWarning.max_entries.toLocaleString(), " entries"] }), treeWarning.excluded.length > 0 ? (_jsxs("p", { className: "mt-2 break-words text-amber-100/70", children: [treeWarning.excluded.slice(0, 8).join(', '), treeWarning.excluded.length > 8 ? '...' : ''] })) : null] })) : null] }), _jsxs("div", { className: "mt-auto rounded-lg border border-cyan-300/15 bg-cyan-300/10 p-3 text-xs leading-5 text-cyan-100/80", children: [_jsx("p", { children: "HTTP \u4EC5\u7528\u4E8E\u521D\u59CB\u76EE\u5F55\u5FEB\u7167\u3001\u7528\u6237\u5237\u65B0\u548C\u5355\u6B21\u6587\u4EF6\u8BFB\u53D6\u3002" }), _jsx("p", { className: "mt-1 text-cyan-100/55", children: "\u8FD0\u884C\u6001\u5B9E\u65F6\u6570\u636E\u4ECD\u53EA\u8D70 runtime.v2 WebSocket\u3002" })] })] })] })] }));
}
