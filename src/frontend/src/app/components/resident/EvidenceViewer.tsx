/**
 * EvidenceViewer - 决策证据包展示组件
 *
 * Phase 1.1: 展示决策关联的 EvidenceBundle，包括代码 diff、测试结果等
 */

import { useEffect, useState } from 'react';
import { FileCode, GitCommit, TestTube, BarChart3, X, ChevronDown, ChevronRight } from 'lucide-react';

import { Button } from '@/app/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/app/components/ui/card';
import { Badge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';

interface FileChange {
  path: string;
  change_type: 'added' | 'modified' | 'deleted' | 'renamed';
  patch?: string;
  language?: string;
  lines_added: number;
  lines_deleted: number;
}

interface EvidenceBundle {
  bundle_id: string;
  created_at: string;
  base_sha: string;
  head_sha?: string;
  working_tree_dirty: boolean;
  change_set: FileChange[];
  source_type: string;
  test_results?: {
    test_command: string;
    exit_code: number;
    total_tests: number;
    passed: number;
    failed: number;
    skipped?: number;
  };
  performance_snapshot?: {
    metrics: Record<string, number>;
  };
}

interface EvidenceViewerProps {
  decisionId: string;
  workspace: string;
  onClose: () => void;
}

export function EvidenceViewer({ decisionId, workspace, onClose }: EvidenceViewerProps) {
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());

  useEffect(() => {
    const fetchEvidence = async () => {
      try {
        setLoading(true);
        const response = await fetch(
          `/v2/resident/decisions/${encodeURIComponent(decisionId)}/evidence?workspace=${encodeURIComponent(workspace)}`
        );
        if (!response.ok) {
          if (response.status === 404) {
            setError('该决策暂无关联的证据包');
          } else {
            throw new Error(`Failed to fetch evidence: ${response.status}`);
          }
          return;
        }
        const data = await response.json();
        setBundle(data.bundle);
        // Auto-expand first file if only one
        if (data.bundle?.change_set?.length === 1) {
          setExpandedFiles(new Set([data.bundle.change_set[0].path]));
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load evidence');
      } finally {
        setLoading(false);
      }
    };

    void fetchEvidence();
  }, [decisionId, workspace]);

  const toggleFile = (path: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  if (loading) {
    return (
      <Card className="border-slate-800 bg-slate-900">
        <CardContent className="py-8 text-center text-slate-500">
          <div className="animate-pulse">加载证据包...</div>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="border-slate-800 bg-slate-900">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-sm text-slate-400">证据包</CardTitle>
          <Button size="sm" variant="ghost" onClick={onClose}>
            <X className="size-4" />
          </Button>
        </CardHeader>
        <CardContent className="py-4 text-center text-sm text-slate-500">
          {error}
        </CardContent>
      </Card>
    );
  }

  if (!bundle) return null;

  const totalAdded = bundle.change_set.reduce((sum, c) => sum + c.lines_added, 0);
  const totalDeleted = bundle.change_set.reduce((sum, c) => sum + c.lines_deleted, 0);

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <div className="flex items-center gap-2">
          <GitCommit className="size-4 text-cyan-400" />
          <CardTitle className="text-sm font-medium text-slate-200">
            变更证据
          </CardTitle>
          <Badge variant="outline" className="border-slate-700 text-slate-400 text-xs">
            {bundle.change_set.length} 文件
          </Badge>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose} className="text-slate-400 hover:text-white">
          <X className="size-4" />
        </Button>
      </CardHeader>

      <CardContent className="space-y-3">
        {/* Stats */}
        <div className="flex items-center gap-4 text-xs">
          <span className="text-emerald-400">+{totalAdded}</span>
          <span className="text-red-400">-{totalDeleted}</span>
          <span className="text-slate-500">
            {bundle.working_tree_dirty ? '工作区' : `Commit ${bundle.base_sha.slice(0, 7)}`}
          </span>
        </div>

        {/* File List */}
        <div className="space-y-1">
          {bundle.change_set.map((change) => (
            <FileChangeItem
              key={change.path}
              change={change}
              expanded={expandedFiles.has(change.path)}
              onToggle={() => toggleFile(change.path)}
            />
          ))}
        </div>

        {/* Test Results */}
        {bundle.test_results && (
          <TestResultsView results={bundle.test_results} />
        )}

        {/* Performance */}
        {bundle.performance_snapshot && Object.keys(bundle.performance_snapshot.metrics).length > 0 && (
          <PerformanceView metrics={bundle.performance_snapshot.metrics} />
        )}
      </CardContent>
    </Card>
  );
}

function FileChangeItem({
  change,
  expanded,
  onToggle,
}: {
  change: FileChange;
  expanded: boolean;
  onToggle: () => void;
}) {
  const changeTypeColors = {
    added: 'text-emerald-400',
    modified: 'text-amber-400',
    deleted: 'text-red-400',
    renamed: 'text-blue-400',
  };

  const changeTypeLabels = {
    added: '新增',
    modified: '修改',
    deleted: '删除',
    renamed: '重命名',
  };

  return (
    <div className="rounded border border-slate-800 bg-slate-950">
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-slate-900"
      >
        <div className="flex items-center gap-2">
          {expanded ? (
            <ChevronDown className="size-4 text-slate-500" />
          ) : (
            <ChevronRight className="size-4 text-slate-500" />
          )}
          <FileCode className="size-4 text-slate-400" />
          <span className="text-sm text-slate-300">{change.path}</span>
          <Badge
            variant="outline"
            className={cn('text-xs border-transparent', changeTypeColors[change.change_type])}
          >
            {changeTypeLabels[change.change_type]}
          </Badge>
        </div>
        <div className="flex items-center gap-2 text-xs">
          {change.lines_added > 0 && (
            <span className="text-emerald-400">+{change.lines_added}</span>
          )}
          {change.lines_deleted > 0 && (
            <span className="text-red-400">-{change.lines_deleted}</span>
          )}
        </div>
      </button>

      {expanded && change.patch && (
        <div className="border-t border-slate-800">
          <DiffView patch={change.patch} />
        </div>
      )}
    </div>
  );
}

function DiffView({ patch }: { patch: string }) {
  const lines = patch.split('\n');

  return (
    <div className="max-h-64 overflow-auto p-3 text-xs">
      <pre className="font-mono leading-relaxed">
        {lines.map((line, i) => {
          let lineClass = 'text-slate-300';
          if (line.startsWith('+') && !line.startsWith('+++')) {
            lineClass = 'bg-emerald-500/10 text-emerald-300';
          } else if (line.startsWith('-') && !line.startsWith('---')) {
            lineClass = 'bg-red-500/10 text-red-300';
          } else if (line.startsWith('@@')) {
            lineClass = 'text-cyan-400';
          } else if (line.startsWith('diff') || line.startsWith('index') || line.startsWith('---') || line.startsWith('+++')) {
            lineClass = 'text-slate-500';
          }

          return (
            <div key={i} className={lineClass}>
              {line || ' '}
            </div>
          );
        })}
      </pre>
    </div>
  );
}

function TestResultsView({
  results,
}: {
  results: EvidenceBundle['test_results'];
}) {
  if (!results) return null;

  const isSuccess = results.exit_code === 0 && results.failed === 0;

  return (
    <div className="rounded border border-slate-800 bg-slate-950 p-3">
      <div className="flex items-center gap-2 mb-2">
        <TestTube className={cn('size-4', isSuccess ? 'text-emerald-400' : 'text-red-400')} />
        <span className="text-sm font-medium text-slate-200">测试结果</span>
        <Badge
          variant="outline"
          className={cn(
            'text-xs border-transparent',
            isSuccess ? 'text-emerald-400' : 'text-red-400'
          )}
        >
          {isSuccess ? '通过' : '失败'}
        </Badge>
      </div>
      <div className="flex gap-4 text-xs text-slate-400">
        <span>总计: {results.total_tests}</span>
        <span className="text-emerald-400">通过: {results.passed}</span>
        {results.failed > 0 && <span className="text-red-400">失败: {results.failed}</span>}
        {(results.skipped ?? 0) > 0 && <span className="text-amber-400">跳过: {results.skipped}</span>}
      </div>
    </div>
  );
}

function PerformanceView({ metrics }: { metrics: Record<string, number> }) {
  return (
    <div className="rounded border border-slate-800 bg-slate-950 p-3">
      <div className="flex items-center gap-2 mb-2">
        <BarChart3 className="size-4 text-cyan-400" />
        <span className="text-sm font-medium text-slate-200">性能指标</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {Object.entries(metrics).map(([key, value]) => (
          <div key={key} className="flex justify-between text-xs">
            <span className="text-slate-500">{key}</span>
            <span className="text-slate-300">{value.toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default EvidenceViewer;
