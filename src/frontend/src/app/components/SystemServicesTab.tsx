/**
 * System Services Tab — displays status of backend services:
 * - MCP Policy Server (MCP Policy)
 * - Code Search Engine (Code Search)
 * - Director Capabilities (Director Capabilities)
 * - Vision Service (视觉服务)
 */
import { useState, useEffect, useCallback } from 'react';
import { Shield, Search, Eye, Cpu, RefreshCw, CheckCircle, XCircle, Loader2, Terminal, FileCode } from 'lucide-react';
import { apiFetch } from '@/api';
import { toast } from 'sonner';

interface ServiceStatus {
  name: string;
  icon: React.ReactNode;
  status: 'online' | 'offline' | 'loading' | 'unknown';
  detail: string;
  extra?: {
    capabilities?: string[];
    tools?: string[];
    [key: string]: unknown;
  };
}

// Search result from code search service
export interface SearchResult {
  file_path: string;
  line_start: number;
  line_end: number;
  text?: string;
  score?: number;
}

export function SystemServicesTab() {
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [indexing, setIndexing] = useState(false);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    const results: ServiceStatus[] = [];

    // MCP Status
    try {
      const res = await apiFetch('/arsenal/mcp/status');
      const data = await res.json();
      results.push({
        name: 'MCP Policy Service',
        icon: <Shield className="w-4 h-4" />,
        status: data.available ? 'online' : 'offline',
        detail: data.available
          ? `${data.tools?.length || 0} 项器用可用`
          : '未见服务',
        extra: data,
      });
    } catch {
      results.push({
        name: 'MCP Policy Service',
        icon: <Shield className="w-4 h-4" />,
        status: 'unknown',
        detail: '暂无法核验',
      });
    }

    // Director Capabilities
    try {
      const res = await apiFetch('/arsenal/director/capabilities');
      const data = await res.json();
      results.push({
        name: 'Director Capabilities Overview',
        icon: <Cpu className="w-4 h-4" />,
        status: data.capabilities?.length > 0 ? 'online' : 'offline',
        detail: data.capabilities?.length
          ? `${data.capabilities.length} 项权限已启用`
          : '尚未配置',
        extra: data,
      });
    } catch {
      results.push({
        name: 'Director Capabilities Overview',
        icon: <Cpu className="w-4 h-4" />,
        status: 'unknown',
        detail: '暂无法核验',
      });
    }

    // Vision Service
    try {
      const res = await apiFetch('/arsenal/vision/status');
      const data = await res.json();
      results.push({
        name: '视察司服务',
        icon: <Eye className="w-4 h-4" />,
        status: data.pil_available ? 'online' : 'offline',
        detail: data.model_loaded
          ? `模型：${data.model_name}`
          : data.pil_available
            ? '基础模式（PIL）'
            : '不可用',
        extra: data,
      });
    } catch {
      results.push({
        name: '视察司服务',
        icon: <Eye className="w-4 h-4" />,
        status: 'unknown',
        detail: '暂无法核验',
      });
    }

    // Code Search
    results.push({
      name: 'Code Search Engine',
      icon: <Search className="w-4 h-4" />,
      status: 'online',
      detail: '已就绪，先为 workspace 索引后可检索',
    });

    setServices(results);
    setLoading(false);
  }, []);

  useEffect(() => {
    let mounted = true;
    fetchStatus().then(() => {
      if (mounted) return;
      // silently ignore - component unmounted
    }).catch(() => {
      if (!mounted) return;
      // silently ignore - component unmounted
    });
    return () => { mounted = false; };
  }, [fetchStatus]);

  const handleIndex = async () => {
    setIndexing(true);
    try {
      const res = await apiFetch('/arsenal/code/index', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        toast.success(`Indexed ${data.files} files (${data.chunks} chunks)`);
      } else {
        toast.error(`Index failed: ${data.error}`);
      }
    } catch (e: unknown) {
      const errorMessage = e instanceof Error ? e.message : String(e);
      toast.error(`Index error: ${errorMessage}`);
    }
    setIndexing(false);
    fetchStatus();
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const res = await apiFetch('/arsenal/code/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: searchQuery, limit: 20 }),
      });
      const data = await res.json();
      setSearchResults(data.results || []);
    } catch {
      setSearchResults([]);
    }
    setSearching(false);
  };

  const statusIcon = (s: string) => {
    if (s === 'online') return <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />;
    if (s === 'offline') return <XCircle className="w-3.5 h-3.5 text-red-400" />;
    if (s === 'loading') return <Loader2 className="w-3.5 h-3.5 text-yellow-400 animate-spin" />;
    return <XCircle className="w-3.5 h-3.5 text-gray-400" />;
  };

  return (
    <div className="space-y-6 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-cyan-300 tracking-wide flex items-center gap-2">
          <Terminal className="w-4 h-4" />
          内务司总览（六部职能）
        </h3>
        <button
          onClick={fetchStatus}
          disabled={loading}
          className="text-xs text-gray-400 hover:text-cyan-400 flex items-center gap-1 transition-colors"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {/* Services Grid */}
      <div className="grid grid-cols-1 gap-3">
        {services.map((svc, idx) => (
          <div
            key={idx}
            className="bg-black/40 backdrop-blur-sm rounded-lg border border-cyan-500/20 p-3 flex items-center gap-3"
          >
            <div className="text-cyan-400">{svc.icon}</div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium text-gray-200">{svc.name}</div>
              <div className="text-[10px] text-gray-400 truncate">{svc.detail}</div>
              {svc.extra?.capabilities && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {svc.extra.capabilities.map((cap: string) => (
                    <span key={cap} className="text-[9px] bg-cyan-500/10 text-cyan-300 px-1.5 py-0.5 rounded border border-cyan-500/20">
                      {cap}
                    </span>
                  ))}
                </div>
              )}
              {svc.extra?.tools && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {svc.extra.tools.map((tool: string) => (
                    <span key={tool} className="text-[9px] bg-purple-500/10 text-purple-300 px-1.5 py-0.5 rounded border border-purple-500/20">
                      {tool}
                    </span>
                  ))}
                </div>
              )}
            </div>
            {statusIcon(svc.status)}
          </div>
        ))}
      </div>

      {/* Code Search Section */}
      <div className="bg-black/40 backdrop-blur-sm rounded-lg border border-cyan-500/20 p-4 space-y-3">
        <div className="flex items-center gap-2">
          <FileCode className="w-4 h-4 text-cyan-400" />
          <h4 className="text-xs font-semibold text-cyan-300">Code Search</h4>
        </div>

        <div className="flex gap-2">
          <button
            onClick={handleIndex}
            disabled={indexing}
            className="text-xs bg-purple-500/20 hover:bg-purple-500/30 text-purple-300 px-3 py-1.5 rounded border border-purple-500/30 flex items-center gap-1 transition-colors disabled:opacity-50"
          >
            {indexing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
            {indexing ? '索引中...' : '为 Workspace 索引'}
          </button>
        </div>

        <div className="flex gap-2">
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="搜索经籍与代码..."
            className="flex-1 text-xs bg-black/60 border border-cyan-500/20 rounded px-3 py-1.5 text-gray-200 placeholder:text-gray-500 focus:border-cyan-500/50 focus:outline-none"
          />
          <button
            onClick={handleSearch}
            disabled={searching || !searchQuery.trim()}
            className="text-xs bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 px-3 py-1.5 rounded border border-cyan-500/30 flex items-center gap-1 transition-colors disabled:opacity-50"
          >
            {searching ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
            搜索
          </button>
        </div>

        {searchResults.length > 0 && (
          <div className="max-h-64 overflow-y-auto space-y-2">
            {searchResults.map((r, i) => (
              <div key={i} className="bg-black/30 rounded border border-gray-700/50 p-2">
                <div className="text-[10px] text-cyan-300 font-mono">{r.file_path}:{r.line_start}-{r.line_end}</div>
                <pre className="text-[10px] text-gray-400 font-mono mt-1 whitespace-pre-wrap max-h-20 overflow-hidden">
                  {r.text?.slice(0, 300)}
                </pre>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
