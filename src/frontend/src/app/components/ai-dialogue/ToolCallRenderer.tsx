/**
 * ToolCallRenderer - 工具调用渲染组件
 *
 * 为标准化工具提供图标、颜色和视觉效果：
 * - search_code / ripgrep / grep: 搜索工具
 * - read_file / list_directory / glob / file_exists: 只读工具
 * - write_file / edit_file / search_replace / append_to_file / execute_command: 执行工具
 */

import React, { useState } from 'react';
import { cn } from '@/app/components/ui/utils';
import {
  // 工具特定图标
  GitCompare,
  Network,
  Database,
  Copy,
  PackageCheck,
  Activity,
  Search,
  Sparkles,
  // 通用图标
  ChevronDown,
  ChevronRight,
  Terminal,
  CheckCircle2,
  XCircle,
  Clock,
  Zap,
  Code2,
  FileSearch,
  Layers,
  GitBranch,
  Radar,
} from 'lucide-react';

// ═══════════════════════════════════════════════════════════════════════════
// 工具配置定义 - 每个工具的独特视觉标识
// ═══════════════════════════════════════════════════════════════════════════

export interface ToolConfig {
  name: string;
  icon: React.ElementType;
  color: string;
  bgGradient: string;
  borderColor: string;
  glowColor: string;
  badgeText: string;
  description: string;
  animation?: string;
}

export const TOOL_CONFIGS: Record<string, ToolConfig> = {
  // 1. 代码变更分析 - 蓝紫色 Diff 风格
  search_code: {
    name: 'search_code',
    icon: GitCompare,
    color: 'text-indigo-400',
    bgGradient: 'from-indigo-950/40 via-blue-950/30 to-slate-950/50',
    borderColor: 'border-indigo-500/30',
    glowColor: 'shadow-indigo-500/20',
    badgeText: '代码分析',
    description: '分析变更影响范围和风险等级',
    animation: 'diff-pulse',
  },

  // 2. 语义上下文 - 青色网络图风格
  read_file: {
    name: 'read_file',
    icon: Network,
    color: 'text-cyan-400',
    bgGradient: 'from-cyan-950/40 via-teal-950/30 to-slate-950/50',
    borderColor: 'border-cyan-500/30',
    glowColor: 'shadow-cyan-500/20',
    badgeText: '语义上下文',
    description: '获取代码结构和依赖关系',
    animation: 'network-pulse',
  },

  // 3. 索引构建 - 琥珀色数据库风格
  list_directory: {
    name: 'list_directory',
    icon: Database,
    color: 'text-amber-400',
    bgGradient: 'from-amber-950/40 via-orange-950/30 to-slate-950/50',
    borderColor: 'border-amber-500/30',
    glowColor: 'shadow-amber-500/20',
    badgeText: '索引构建',
    description: '构建或更新代码索引',
    animation: 'database-pulse',
  },

  // 4. 相似代码查找 - 粉色复制检测风格
  glob: {
    name: 'glob',
    icon: Copy,
    color: 'text-pink-400',
    bgGradient: 'from-pink-950/40 via-rose-950/30 to-slate-950/50',
    borderColor: 'border-pink-500/30',
    glowColor: 'shadow-pink-500/20',
    badgeText: '相似代码',
    description: '查找重复或相似代码片段',
    animation: 'copy-pulse',
  },

  // 5. 导入验证 - 绿色检查风格
  file_exists: {
    name: 'file_exists',
    icon: PackageCheck,
    color: 'text-emerald-400',
    bgGradient: 'from-emerald-950/40 via-green-950/30 to-slate-950/50',
    borderColor: 'border-emerald-500/30',
    glowColor: 'shadow-emerald-500/20',
    badgeText: '导入验证',
    description: '验证导入语句正确性',
    animation: 'check-pulse',
  },

  // 6. 影响分析 - 红色波纹风格
  grep: {
    name: 'grep',
    icon: Activity,
    color: 'text-rose-400',
    bgGradient: 'from-rose-950/40 via-red-950/30 to-slate-950/50',
    borderColor: 'border-rose-500/30',
    glowColor: 'shadow-rose-500/20',
    badgeText: '影响分析',
    description: '分析变更的级联影响',
    animation: 'impact-ripple',
  },

  // 7. ripgrep - 靛青色 Sparkle 风格
  ripgrep: {
    name: 'ripgrep',
    icon: Search,
    color: 'text-violet-400',
    bgGradient: 'from-violet-950/40 via-purple-950/30 to-slate-950/50',
    borderColor: 'border-violet-500/30',
    glowColor: 'shadow-violet-500/20',
    badgeText: '快速检索',
    description: '基于 ripgrep 的高性能代码搜索',
    animation: 'search-sparkle',
  },
};

// 通用工具回退配置
const DEFAULT_TOOL_CONFIG: ToolConfig = {
  name: 'unknown_tool',
  icon: Terminal,
  color: 'text-slate-400',
  bgGradient: 'from-slate-950/40 via-gray-950/30 to-slate-950/50',
  borderColor: 'border-slate-500/30',
  glowColor: 'shadow-slate-500/20',
  badgeText: '工具调用',
  description: '执行工具操作',
};

// ═══════════════════════════════════════════════════════════════════════════
// 解析工具调用内容
// ═══════════════════════════════════════════════════════════════════════════

// Tool call parameter type
export interface ToolCallParams {
  [key: string]: unknown;
}

interface ParsedToolCall {
  toolName: string;
  params: ToolCallParams;
  rawContent: string;
}

function parseToolCall(content: string): ParsedToolCall {
  // 尝试匹配 [TOOL_NAME]...[/TOOL_NAME] 格式
  const toolPattern = /\[(\w+)\]([\s\S]*?)\[\/\w+\]/i;
  const match = content.match(toolPattern);

  if (match) {
    const toolName = match[1].toLowerCase();
    const paramsText = match[2].trim();

    // 解析参数 (key: value 格式)
    const params: ToolCallParams = {};
    const paramLines = paramsText.split('\n');

    for (const line of paramLines) {
      const colonIndex = line.indexOf(':');
      if (colonIndex > 0) {
        const key = line.slice(0, colonIndex).trim();
        let value: unknown = line.slice(colonIndex + 1).trim();

        // 尝试解析为 JSON (数组、对象、数字、布尔值)
        if (typeof value === 'string' && (value.startsWith('[') || value.startsWith('{') ||
            value === 'true' || value === 'false' ||
            /^\d+$/.test(value))) {
          try {
            value = JSON.parse(value as string);
          } catch {
            // 保持为字符串
          }
        }

        // 去除引号
        if (typeof value === 'string' &&
            ((value.startsWith('"') && value.endsWith('"')) ||
             (value.startsWith("'") && value.endsWith("'")))) {
          value = value.slice(1, -1);
        }

        params[key] = value;
      }
    }

    return { toolName, params, rawContent: content };
  }

  // 尝试匹配 JSON 格式
  try {
    const json = JSON.parse(content);
    if (json.tool || json.name) {
      return {
        toolName: (json.tool || json.name).toLowerCase(),
        params: json.params || json.arguments || {},
        rawContent: content,
      };
    }
  } catch {
    // 不是 JSON 格式
  }

  // 无法解析，返回原始内容
  return { toolName: 'unknown', params: {}, rawContent: content };
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具特定动画组件
// ═══════════════════════════════════════════════════════════════════════════

// 代码分析动画 - Diff 对比效果
function DiffAnimation() {
  return (
    <div className="flex items-center gap-1">
      <div className="w-2 h-4 bg-emerald-500/40 rounded-sm animate-pulse" style={{ animationDelay: '0ms' }} />
      <div className="w-2 h-6 bg-indigo-500/40 rounded-sm animate-pulse" style={{ animationDelay: '100ms' }} />
      <div className="w-2 h-3 bg-rose-500/40 rounded-sm animate-pulse" style={{ animationDelay: '200ms' }} />
      <div className="w-2 h-5 bg-indigo-500/40 rounded-sm animate-pulse" style={{ animationDelay: '300ms' }} />
    </div>
  );
}

// 网络图动画 - 节点连接效果
function NetworkAnimation() {
  return (
    <div className="relative w-8 h-8">
      <div className="absolute top-1/2 left-1/2 w-2 h-2 bg-cyan-400 rounded-full -translate-x-1/2 -translate-y-1/2 animate-pulse" />
      <div className="absolute top-0 left-1/4 w-1.5 h-1.5 bg-cyan-400/60 rounded-full animate-ping" style={{ animationDuration: '1.5s' }} />
      <div className="absolute bottom-0 right-1/4 w-1.5 h-1.5 bg-cyan-400/60 rounded-full animate-ping" style={{ animationDuration: '2s', animationDelay: '0.5s' }} />
      <div className="absolute top-1/4 right-0 w-1.5 h-1.5 bg-cyan-400/60 rounded-full animate-ping" style={{ animationDuration: '1.8s', animationDelay: '0.3s' }} />
      {/* 连接线 */}
      <svg className="absolute inset-0 w-full h-full opacity-30">
        <line x1="50%" y1="50%" x2="25%" y2="0" stroke="currentColor" className="text-cyan-400" strokeWidth="1" />
        <line x1="50%" y1="50%" x2="75%" y2="100%" stroke="currentColor" className="text-cyan-400" strokeWidth="1" />
        <line x1="50%" y1="50%" x2="100%" y2="25%" stroke="currentColor" className="text-cyan-400" strokeWidth="1" />
      </svg>
    </div>
  );
}

// 数据库动画 - 堆叠层效果
function DatabaseAnimation() {
  return (
    <div className="flex flex-col items-center gap-0.5">
      <div className="w-4 h-1.5 bg-amber-400/60 rounded-full animate-pulse" style={{ animationDelay: '0ms' }} />
      <div className="w-5 h-1.5 bg-amber-400/50 rounded-full animate-pulse" style={{ animationDelay: '100ms' }} />
      <div className="w-5 h-1.5 bg-amber-400/40 rounded-full animate-pulse" style={{ animationDelay: '200ms' }} />
      <div className="w-4 h-1.5 bg-amber-400/30 rounded-full animate-pulse" style={{ animationDelay: '300ms' }} />
    </div>
  );
}

// 相似代码动画 - 复制检测效果
function CopyAnimation() {
  return (
    <div className="relative w-8 h-6">
      <div className="absolute left-0 top-0 w-5 h-4 bg-pink-500/30 rounded border border-pink-400/40 animate-pulse" />
      <div className="absolute left-2 top-1 w-5 h-4 bg-pink-500/20 rounded border border-pink-400/30 animate-pulse" style={{ animationDelay: '200ms' }} />
      <div className="absolute -bottom-1 left-1 text-[8px] text-pink-400 font-mono">~90%</div>
    </div>
  );
}

// 检查动画 - 勾选效果
function CheckAnimation() {
  return (
    <div className="relative w-6 h-6">
      <svg viewBox="0 0 24 24" className="w-full h-full">
        <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="2" className="text-emerald-400/30" />
        <path d="M8 12l2.5 2.5L16 9" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-emerald-400 animate-draw-check" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

// 影响分析动画 - 波纹效果
function ImpactAnimation() {
  return (
    <div className="relative w-8 h-8 flex items-center justify-center">
      <div className="absolute w-2 h-2 bg-rose-400 rounded-full" />
      <div className="absolute w-4 h-4 border border-rose-400/60 rounded-full animate-ping" style={{ animationDuration: '1s' }} />
      <div className="absolute w-6 h-6 border border-rose-400/40 rounded-full animate-ping" style={{ animationDuration: '1.5s', animationDelay: '0.2s' }} />
      <div className="absolute w-8 h-8 border border-rose-400/20 rounded-full animate-ping" style={{ animationDuration: '2s', animationDelay: '0.4s' }} />
    </div>
  );
}

// 智能搜索动画 - Sparkle 效果
function SearchSparkleAnimation() {
  return (
    <div className="relative w-6 h-6">
      <Search className="w-full h-full text-violet-400" />
      <Sparkles className="absolute -top-1 -right-1 w-3 h-3 text-violet-300 animate-pulse" />
    </div>
  );
}

// 获取工具特定的动画组件
function getToolAnimation(toolName: string): React.ReactNode {
  // 转换为小写以支持大小写不敏感匹配
  const normalizedToolName = toolName.toLowerCase();
  switch (normalizedToolName) {
    case 'search_code': return <DiffAnimation />;
    case 'read_file': return <NetworkAnimation />;
    case 'list_directory': return <DatabaseAnimation />;
    case 'glob': return <CopyAnimation />;
    case 'file_exists': return <CheckAnimation />;
    case 'grep': return <ImpactAnimation />;
    case 'ripgrep': return <SearchSparkleAnimation />;
    default: return null;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 主渲染组件
// ═══════════════════════════════════════════════════════════════════════════

interface ToolCallRendererProps {
  content: string;
  className?: string;
}

export function ToolCallRenderer({ content, className }: ToolCallRendererProps) {
  const [isExpanded, setIsExpanded] = useState(true);
  const [showRaw, setShowRaw] = useState(false);

  const parsed = parseToolCall(content);
  // 将工具名转换为小写以查找配置（支持大小写）
  const config = TOOL_CONFIGS[parsed.toolName.toLowerCase()] || DEFAULT_TOOL_CONFIG;
  const Icon = config.icon;

  // 格式化参数显示
  const formatParamValue = (value: unknown): string => {
    if (Array.isArray(value)) {
      return `[${value.length} items]`;
    }
    if (value !== null && typeof value === 'object') {
      return '{...}';
    }
    return String(value);
  };

  return (
    <div className={cn(
      'my-3 rounded-xl overflow-hidden border backdrop-blur-sm',
      'bg-gradient-to-br', config.bgGradient,
      config.borderColor,
      className
    )}>
      {/* 头部 - 工具标识 */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className={cn(
          'w-full flex items-center justify-between px-4 py-3',
          'transition-colors duration-200',
          'hover:bg-white/5'
        )}
      >
        <div className="flex items-center gap-3">
          {/* 工具图标 */}
          <div className={cn(
            'relative p-2 rounded-lg',
            'bg-gradient-to-br from-white/10 to-white/5',
            'border border-white/10',
            config.color
          )}>
            <Icon className="w-5 h-5" />
            {/* 状态指示点 */}
            <span className={cn(
              'absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full',
              'animate-pulse',
              config.color.replace('text-', 'bg-')
            )} />
          </div>

          {/* 工具信息 */}
          <div className="flex flex-col items-start">
            <div className="flex items-center gap-2">
              <span className={cn('text-sm font-semibold', config.color)}>
                {config.badgeText}
              </span>
              <span className="text-[10px] text-slate-500 font-mono px-1.5 py-0.5 rounded bg-slate-800/50">
                {parsed.toolName}
              </span>
            </div>
            <span className="text-xs text-slate-400">
              {config.description}
            </span>
          </div>

          {/* 工具特定动画 */}
          <div className="ml-2 hidden sm:block">
            {getToolAnimation(parsed.toolName)}
          </div>
        </div>

        {/* 展开/折叠按钮 */}
        <div className="flex items-center gap-2">
          <ChevronDown className={cn(
            'w-5 h-5 text-slate-400 transition-transform duration-300',
            isExpanded && 'rotate-180'
          )} />
        </div>
      </button>

      {/* 内容区域 */}
      {isExpanded && (
        <div className="border-t border-white/5">
          {/* 参数表格 */}
          {Object.keys(parsed.params).length > 0 && (
            <div className="p-4">
              <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2 font-semibold">
                参数
              </div>
              <div className="grid gap-2">
                {Object.entries(parsed.params).map(([key, value]) => (
                  <div
                    key={key}
                    className="flex items-center gap-3 text-sm p-2 rounded-lg bg-black/20"
                  >
                    <span className="text-slate-400 font-mono text-xs min-w-[100px]">
                      {key}
                    </span>
                    <span className={cn(
                      'flex-1 font-mono truncate',
                      Array.isArray(value) ? 'text-amber-300' :
                      typeof value === 'boolean' ? (value ? 'text-emerald-400' : 'text-rose-400') :
                      typeof value === 'number' ? 'text-cyan-300' :
                      'text-slate-200'
                    )}>
                      {formatParamValue(value)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 原始内容切换 */}
          <div className="px-4 pb-3">
            <button
              onClick={() => setShowRaw(!showRaw)}
              className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors flex items-center gap-1"
            >
              <Terminal className="w-3 h-3" />
              {showRaw ? '隐藏原始数据' : '查看原始数据'}
            </button>

            {showRaw && (
              <pre className="mt-2 p-3 rounded-lg bg-black/40 text-xs text-slate-400 font-mono overflow-x-auto">
                <code>{parsed.rawContent}</code>
              </pre>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具结果渲染组件
// ═══════════════════════════════════════════════════════════════════════════

function formatResultForDisplay(result: unknown): string {
  if (result === null || result === undefined) {
    return '';
  }
  if (typeof result === 'string') {
    return result;
  }
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

interface ToolResultRendererProps {
  toolName: string;
  result: unknown;
  status: 'running' | 'success' | 'error';
  className?: string;
}

export function ToolResultRenderer({ toolName, result, status, className }: ToolResultRendererProps) {
  const config = TOOL_CONFIGS[toolName.toLowerCase()] || DEFAULT_TOOL_CONFIG;

  return (
    <div className={cn(
      'my-2 rounded-lg overflow-hidden border',
      status === 'success' && 'border-emerald-500/20 bg-emerald-950/10',
      status === 'error' && 'border-rose-500/20 bg-rose-950/10',
      status === 'running' && config.borderColor,
      className
    )}>
      <div className="flex items-center gap-2 px-3 py-2">
        {status === 'success' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
        {status === 'error' && <XCircle className="w-4 h-4 text-rose-400" />}
        {status === 'running' && <Clock className="w-4 h-4 text-amber-400 animate-pulse" />}

        <span className={cn(
          'text-xs',
          status === 'success' && 'text-emerald-400',
          status === 'error' && 'text-rose-400',
          status === 'running' && 'text-amber-400'
        )}>
          {status === 'success' && '执行成功'}
          {status === 'error' && '执行失败'}
          {status === 'running' && '执行中...'}
        </span>

        <span className="text-[10px] text-slate-500 font-mono ml-auto">
          {config.badgeText}
        </span>
      </div>

      {result !== null && result !== undefined && (
        <div className="px-3 pb-3">
          <pre className="p-2 rounded bg-black/30 text-xs text-slate-300 font-mono overflow-x-auto max-h-40 overflow-y-auto">
            <code>{formatResultForDisplay(result)}</code>
          </pre>
        </div>
      )}
    </div>
  );
}

export default ToolCallRenderer;

