import { useState, useEffect, useCallback } from 'react';
import {
  FileText,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  RefreshCw,
  Save,
  Eye,
  Edit3,
  Search,
  Plus,
  MoreHorizontal,
  FilePlus,
  FolderPlus,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { cn } from '@/app/components/ui/utils';
import { fileService } from '@/services/api';
import { toast } from 'sonner';
import { sanitizeMarkdown } from '@/app/utils/xssSanitizer';

interface PMDocumentPanelProps {
  workspace: string;
  selectedPath: string | null;
  onDocumentSelect: (path: string) => void;
}

interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileNode[];
  expanded?: boolean;
  content?: string;
}

export function PMDocumentPanel({
  workspace,
  selectedPath,
  onDocumentSelect,
}: PMDocumentPanelProps) {
  const [fileTree, setFileTree] = useState<FileNode[]>([]);
  const [selectedFile, setSelectedFile] = useState<FileNode | null>(null);
  const [fileContent, setFileContent] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [viewMode, setViewMode] = useState<'preview' | 'edit'>('preview');

  // Load file tree on mount
  useEffect(() => {
    loadFileTree();
  }, [workspace]);

  const loadFileTree = async () => {
    // Mock file tree for now - in real implementation, this would fetch from backend
    const mockTree: FileNode[] = [
      {
        name: 'docs',
        path: 'docs',
        type: 'directory',
        expanded: true,
        children: [
          { name: 'PRD.md', path: 'docs/PRD.md', type: 'file' },
          { name: 'Architecture.md', path: 'docs/Architecture.md', type: 'file' },
          { name: 'API.md', path: 'docs/API.md', type: 'file' },
        ],
      },
      {
        name: 'AGENTS.md',
        path: 'AGENTS.md',
        type: 'file',
      },
      {
        name: 'TASKS.md',
        path: 'TASKS.md',
        type: 'file',
      },
      {
        name: 'README.md',
        path: 'README.md',
        type: 'file',
      },
    ];
    setFileTree(mockTree);
  };

  const handleFileSelect = useCallback(async (node: FileNode) => {
    if (node.type === 'directory') {
      toggleDirectory(node);
      return;
    }

    setIsLoading(true);
    setSelectedFile(node);
    onDocumentSelect(node.path);

    try {
      const result = await fileService.read(node.path);
      if (result.ok && result.data) {
        setFileContent(result.data.content || '');
      } else {
        // Mock content for demonstration
        setFileContent(`# ${node.name}\n\n这是 ${node.name} 的内容。\n\n## 概述\n\n文档内容将在这里显示...\n\n## 详细信息\n\n- 项目: Polaris\n- 路径: ${node.path}\n- 类型: 文档\n`);
      }
    } catch {
      toast.error('加载文件失败');
    } finally {
      setIsLoading(false);
    }
  }, [onDocumentSelect]);

  const toggleDirectory = (node: FileNode) => {
    const updateTree = (nodes: FileNode[]): FileNode[] => {
      return nodes.map((n) => {
        if (n.path === node.path) {
          return { ...n, expanded: !n.expanded };
        }
        if (n.children) {
          return { ...n, children: updateTree(n.children) };
        }
        return n;
      });
    };
    setFileTree(updateTree(fileTree));
  };

  const handleSave = async () => {
    if (!selectedFile) return;

    try {
      // In real implementation, this would call the save API
      toast.success('文件已保存');
      setIsEditing(false);
      setViewMode('preview');
    } catch {
      toast.error('保存失败');
    }
  };

  const filteredTree = searchQuery.trim()
    ? filterTree(fileTree, searchQuery.toLowerCase())
    : fileTree;

  return (
    <div className="h-full flex">
      {/* File Tree Sidebar */}
      <div className="w-64 flex flex-col border-r border-white/10 bg-slate-950/30">
        {/* Toolbar */}
        <div className="h-14 flex items-center justify-between px-3 border-b border-white/10">
          <span className="text-sm font-medium text-slate-300">文档</span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-slate-400 hover:text-slate-200"
              onClick={loadFileTree}
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-slate-400 hover:text-slate-200"
            >
              <FilePlus className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-slate-400 hover:text-slate-200"
            >
              <FolderPlus className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>

        {/* Search */}
        <div className="p-2 border-b border-white/10">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500" />
            <Input
              placeholder="搜索文档..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-7 h-8 text-xs bg-white/5 border-white/10 text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50"
            />
          </div>
        </div>

        {/* File Tree */}
        <div className="flex-1 overflow-auto py-2">
          {filteredTree.map((node) => (
            <FileTreeNode
              key={node.path}
              node={node}
              level={0}
              selectedPath={selectedFile?.path}
              onSelect={handleFileSelect}
            />
          ))}
        </div>
      </div>

      {/* Document Editor */}
      <div className="flex-1 flex flex-col min-w-0">
        {selectedFile ? (
          <>
            {/* Editor Header */}
            <div className="h-14 flex items-center justify-between px-4 border-b border-white/10 bg-white/[0.02]">
              <div className="flex items-center gap-3">
                <FileText className="w-4 h-4 text-amber-400" />
                <div>
                  <h3 className="text-sm font-medium text-slate-200">
                    {selectedFile.name}
                  </h3>
                  <p className="text-[10px] text-slate-500">{selectedFile.path}</p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                {/* View Mode Toggle */}
                <div className="flex items-center p-1 rounded-lg bg-white/5 border border-white/10">
                  <button
                    onClick={() => setViewMode('preview')}
                    className={cn(
                      'px-2.5 py-1 rounded-md text-xs font-medium transition-all flex items-center gap-1',
                      viewMode === 'preview'
                        ? 'bg-amber-500/20 text-amber-400'
                        : 'text-slate-500 hover:text-slate-300'
                    )}
                  >
                    <Eye className="w-3 h-3" />
                    预览
                  </button>
                  <button
                    onClick={() => setViewMode('edit')}
                    className={cn(
                      'px-2.5 py-1 rounded-md text-xs font-medium transition-all flex items-center gap-1',
                      viewMode === 'edit'
                        ? 'bg-amber-500/20 text-amber-400'
                        : 'text-slate-500 hover:text-slate-300'
                    )}
                  >
                    <Edit3 className="w-3 h-3" />
                    编辑
                  </button>
                </div>

                {viewMode === 'edit' && (
                  <Button
                    size="sm"
                    onClick={handleSave}
                    className="bg-amber-600 hover:bg-amber-700 text-white"
                  >
                    <Save className="w-3.5 h-3.5 mr-1.5" />
                    保存
                  </Button>
                )}

                <Button
                  variant="ghost"
                  size="icon"
                  className="text-slate-400 hover:text-slate-200"
                >
                  <MoreHorizontal className="w-4 h-4" />
                </Button>
              </div>
            </div>

            {/* Editor Content */}
            <div className="flex-1 overflow-auto">
              {isLoading ? (
                <div className="h-full flex items-center justify-center text-slate-500">
                  <RefreshCw className="w-5 h-5 animate-spin" />
                </div>
              ) : viewMode === 'edit' ? (
                <textarea
                  value={fileContent}
                  onChange={(e) => setFileContent(e.target.value)}
                  className="w-full h-full p-4 bg-slate-950 text-slate-200 font-mono text-sm resize-none focus:outline-none"
                  spellCheck={false}
                />
              ) : (
                <MarkdownPreview content={fileContent} />
              )}
            </div>
          </>
        ) : (
          <div className="h-full flex flex-col items-center justify-center text-slate-500">
            <FolderOpen className="w-12 h-12 mb-4 opacity-20" />
            <p className="text-sm">选择文档以查看</p>
            <p className="text-xs text-slate-600 mt-1">从左侧列表选择文件</p>
          </div>
        )}
      </div>
    </div>
  );
}

// File Tree Node Component
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
          'flex items-center gap-1.5 py-1.5 pr-3 cursor-pointer transition-colors',
          isSelected
            ? 'bg-amber-500/10 text-amber-400'
            : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
        )}
      >
        {isDirectory && (
          <span className="text-slate-500">
            {node.expanded ? (
              <ChevronDown className="w-3.5 h-3.5" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5" />
            )}
          </span>
        )}
        {!isDirectory && <span className="w-3.5" />}

        {isDirectory ? (
          <FolderOpen className="w-4 h-4 text-amber-500/70" />
        ) : (
          <FileText className="w-4 h-4 text-slate-500" />
        )}

        <span className="text-xs truncate">{node.name}</span>
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

// Markdown Preview Component
function MarkdownPreview({ content }: { content: string }) {
  // Simple markdown rendering - in production, use a proper markdown library
  const renderMarkdown = (text: string): string => {
    return text
      .replace(/^# (.*$)/gim, '<h1 class="text-2xl font-bold text-slate-100 mb-4">$1</h1>')
      .replace(/^## (.*$)/gim, '<h2 class="text-xl font-semibold text-slate-200 mb-3 mt-6">$1</h2>')
      .replace(/^### (.*$)/gim, '<h3 class="text-lg font-medium text-slate-300 mb-2 mt-4">$1</h3>')
      .replace(/\*\*(.*?)\*\*/g, '<strong class="text-slate-200">$1</strong>')
      .replace(/\*(.*?)\*/g, '<em class="text-slate-300">$1</em>')
      .replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-slate-800 text-amber-400 text-xs font-mono">$1</code>')
      .replace(/^- (.*$)/gim, '<li class="text-slate-300 ml-4">$1</li>')
      .replace(/\n/g, '<br />');
  };

  return (
    <div
      className="p-6 prose prose-invert prose-amber max-w-none"
      dangerouslySetInnerHTML={{ __html: sanitizeMarkdown(renderMarkdown(content)) }}
    />
  );
}

// Helper function to filter tree
function filterTree(nodes: FileNode[], query: string): FileNode[] {
  return nodes.reduce<FileNode[]>((acc, node) => {
    const matches = node.name.toLowerCase().includes(query);

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
