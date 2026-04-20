/**
 * SmartContentRenderer - 智能内容渲染组件
 *
 * 解析并渲染 AI 输出中的特殊标签：
 * - <thinking> - 思考过程（紫色科技感）
 * - <output> - 最终输出（青色发光效果）
 * - <tool_call> - 工具调用（橙色机械感）
 * - <error> - 错误信息（红色警示）
 * - <warning> - 警告（黄色提醒）
 */

import React, { useState } from 'react';
import { cn } from '@/app/components/ui/utils';
import {
  Brain,
  AlertCircle,
  AlertTriangle,
  ChevronDown,
  Sparkles,
} from 'lucide-react';
import { ToolCallRenderer } from './ToolCallRenderer';

// 标签类型定义
 type TagType = 'thinking' | 'output' | 'tool_call' | 'error' | 'warning' | 'plain';

interface ContentSegment {
  type: TagType;
  content: string;
}

// 标准工具标签列表（支持大小写）
const STANDARD_TOOL_TAGS = [
  'search_code', 'SEARCH_CODE',
  'grep', 'GREP',
  'ripgrep', 'RIPGREP',
  'read_file', 'READ_FILE',
  'write_file', 'WRITE_FILE',
  'execute_command', 'EXECUTE_COMMAND',
  'search_replace', 'SEARCH_REPLACE',
  'edit_file', 'EDIT_FILE',
  'append_to_file', 'APPEND_TO_FILE',
  'list_directory', 'LIST_DIRECTORY',
  'glob', 'GLOB',
  'file_exists', 'FILE_EXISTS',
];

// 解析内容中的标签（支持 XML 标签和方括号工具调用）
function parseContent(content: string): ContentSegment[] {
  const segments: ContentSegment[] = [];

  // 构建完整的正则表达式：
  // 1. XML 标签: <thinking>...</thinking>, <output>...</output> 等
  // 2. 工具调用: [TOOL_NAME]...[/TOOL_NAME]
  const toolNamesPattern = STANDARD_TOOL_TAGS.join('|');
  const xmlTagPattern = '<(thinking|output|tool_call|error|warning)[^>]*>([\\s\\S]*?)<\\/\\1>';
  const bracketToolPattern = `\\[(${toolNamesPattern})\\]([\\s\\S]*?)\\[/\\1\\]`;

  const combinedPattern = new RegExp(`${xmlTagPattern}|${bracketToolPattern}`, 'gi');

  // 检查是否有 <output> 标签 - 如果有，优先只使用 output 内的内容
  const outputPattern = /<output[^>]*>([\s\S]*?)<\/output>/i;
  const outputMatch = content.match(outputPattern);

  if (outputMatch) {
    // 有 <output> 标签，只解析标签内的内容，忽略标签外的重复
    const outputContent = outputMatch[1].trim();

    // 检查 output 内是否还有嵌套标签
    let innerLastIndex = 0;
    let innerMatch;

    while ((innerMatch = combinedPattern.exec(outputContent)) !== null) {
      // 添加标签前的文本
      if (innerMatch.index > innerLastIndex) {
        const plainText = outputContent.slice(innerLastIndex, innerMatch.index).trim();
        if (plainText) {
          segments.push({ type: 'plain', content: plainText });
        }
      }

      // 处理嵌套标签
      let tagType: TagType;
      let tagContent: string;

      if (innerMatch[1]) {
        tagType = innerMatch[1].toLowerCase() as TagType;
        tagContent = innerMatch[2].trim();
      } else if (innerMatch[3]) {
        tagType = 'tool_call';
        const toolName = innerMatch[3];
        const toolParams = innerMatch[4].trim();
        tagContent = `[${toolName}]\n${toolParams}\n[/${toolName}]`;
      } else {
        tagType = 'plain';
        tagContent = innerMatch[0];
      }

      segments.push({ type: tagType, content: tagContent });
      innerLastIndex = innerMatch.index + innerMatch[0].length;
    }

    // 添加剩余文本
    if (innerLastIndex < outputContent.length) {
      const remainingText = outputContent.slice(innerLastIndex).trim();
      if (remainingText) {
        segments.push({ type: 'plain', content: remainingText });
      }
    }

    // 如果 output 内没有解析到任何内容，把整个 output 内容作为 plain
    if (segments.length === 0) {
      segments.push({ type: 'plain', content: outputContent });
    }

    return segments;
  }

  // 没有 <output> 标签，使用原来的解析逻辑
  let lastIndex = 0;
  let match;

  while ((match = combinedPattern.exec(content)) !== null) {
    // 添加标签前的普通文本
    if (match.index > lastIndex) {
      const plainText = content.slice(lastIndex, match.index).trim();
      if (plainText) {
        segments.push({ type: 'plain', content: plainText });
      }
    }

    // 判断匹配类型
    let tagType: TagType;
    let tagContent: string;

    if (match[1]) {
      // XML 标签匹配 (match[1] 是标签名)
      tagType = match[1].toLowerCase() as TagType;
      tagContent = match[2].trim();
    } else if (match[3]) {
      // 方括号工具调用匹配 (match[3] 是工具名)
      tagType = 'tool_call';
      const toolName = match[3];
      const toolParams = match[4].trim();
      tagContent = `[${toolName}]\n${toolParams}\n[/${toolName}]`;
    } else {
      // 未知匹配，作为普通文本
      tagType = 'plain';
      tagContent = match[0];
    }

    segments.push({ type: tagType, content: tagContent });
    lastIndex = match.index + match[0].length;
  }

  // 添加剩余文本
  if (lastIndex < content.length) {
    const remainingText = content.slice(lastIndex).trim();
    if (remainingText) {
      segments.push({ type: 'plain', content: remainingText });
    }
  }

  // 如果没有匹配到任何标签，返回原内容
  if (segments.length === 0) {
    segments.push({ type: 'plain', content: content.trim() });
  }

  return segments;
}

// ═══════════════════════════════════════════════════════════════════════════
// 各个标签的渲染组件
// ═══════════════════════════════════════════════════════════════════════════

// 1. Thinking 标签 - 紫色科技感，脑电波效果
function ThinkingBlock({ content }: { content: string }) {
  const [isExpanded, setIsExpanded] = useState(true);

  return (
    <div className="my-3 rounded-xl overflow-hidden border border-violet-500/30 bg-gradient-to-br from-violet-950/40 via-purple-950/30 to-slate-950/50">
      {/* 头部 */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-violet-500/10 hover:bg-violet-500/15 transition-colors"
      >
        <div className="flex items-center gap-2">
          <div className="relative">
            <Brain className="w-4 h-4 text-violet-400" />
            <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 bg-violet-400 rounded-full animate-pulse" />
          </div>
          <span className="text-xs font-medium text-violet-300">思考过程</span>
          <Sparkles className="w-3 h-3 text-violet-400/60" />
        </div>
        <ChevronDown
          className={cn('w-4 h-4 text-violet-400 transition-transform duration-300', isExpanded && 'rotate-180')}
        />
      </button>

      {/* 内容 */}
      {isExpanded && (
        <div className="relative">
          {/* 左侧发光条 */}
          <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-gradient-to-b from-violet-500/50 via-purple-500/30 to-transparent" />

          <div className="p-4 pl-5">
            {/* 脑电波装饰 */}
            <div className="flex gap-0.5 mb-3 opacity-40">
              {[...Array(12)].map((_, i) => (
                <div
                  key={i}
                  className="w-0.5 bg-violet-400 rounded-full animate-pulse"
                  style={{
                    height: `${Math.random() * 16 + 8}px`,
                    animationDelay: `${i * 0.1}s`,
                    animationDuration: `${0.8 + Math.random() * 0.4}s`
                  }}
                />
              ))}
            </div>

            <p className="text-sm text-violet-200/80 whitespace-pre-wrap leading-relaxed">
              {content}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// 2. Output 标签 - 青色发光效果，重要输出
function OutputBlock({ content }: { content: string }) {
  return (
    <div className="my-3 relative">
      {/* 外层发光 */}
      <div className="absolute -inset-0.5 bg-gradient-to-r from-cyan-500/20 via-teal-500/20 to-emerald-500/20 rounded-xl blur-sm" />

      <div className="relative rounded-xl border border-cyan-500/30 bg-gradient-to-br from-cyan-950/30 via-teal-950/20 to-slate-900/50 overflow-hidden">
        {/* 顶部光条 */}
        <div className="h-1 bg-gradient-to-r from-cyan-500/60 via-teal-500/40 to-emerald-500/30" />

        {/* 内容 */}
        <div className="relative p-4">
          {/* 角落装饰 */}
          <div className="absolute top-2 right-2 w-8 h-8 border-t border-r border-cyan-500/20 rounded-tr-lg" />
          <div className="absolute bottom-2 left-2 w-8 h-8 border-b border-l border-cyan-500/20 rounded-bl-lg" />

          <p className="text-sm text-cyan-100/90 whitespace-pre-wrap leading-relaxed">
            {content}
          </p>
        </div>
      </div>
    </div>
  );
}

// 3. Tool Call 标签 - 使用专门的工具渲染器
function ToolCallBlock({ content }: { content: string }) {
  return <ToolCallRenderer content={content} />;
}

// 4. Error 标签 - 红色警示，故障效果
function ErrorBlock({ content }: { content: string }) {
  return (
    <div className="my-3 rounded-xl overflow-hidden border border-red-500/40 bg-gradient-to-br from-red-950/50 via-rose-950/30 to-slate-950/50">
      {/* 故障动画背景 */}
      <div className="absolute inset-0 opacity-5">
        <div className="h-full w-full bg-[repeating-linear-gradient(0deg,transparent,transparent_2px,#f00_2px,#f00_4px)]" />
      </div>

      {/* 头部 */}
      <div className="relative flex items-center gap-2 px-4 py-3 bg-red-500/15 border-b border-red-500/20">
        <div className="relative">
          <AlertCircle className="w-5 h-5 text-red-400" />
          <span className="absolute inset-0 bg-red-400/30 rounded-full animate-ping" style={{ animationDuration: '1.5s' }} />
        </div>
        <span className="text-sm font-semibold text-red-300">执行错误</span>
        <span className="ml-auto text-[10px] text-red-400/50 font-mono px-1.5 py-0.5 rounded bg-red-500/10 border border-red-500/20">
          ERROR
        </span>
      </div>

      {/* 内容 */}
      <div className="relative p-4">
        {/* 错误代码装饰 */}
        <div className="flex gap-1 mb-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-1 w-8 bg-red-500/30 rounded-full" style={{ opacity: 1 - i * 0.25 }} />
          ))}
        </div>

        <p className="text-sm text-red-200/80 whitespace-pre-wrap font-mono leading-relaxed">
          {content}
        </p>
      </div>
    </div>
  );
}

// 5. Warning 标签 - 黄色提醒，警示效果
function WarningBlock({ content }: { content: string }) {
  return (
    <div className="my-3 rounded-lg overflow-hidden border border-yellow-500/30 bg-gradient-to-br from-yellow-950/30 via-amber-950/20 to-slate-950/50">
      {/* 警示条纹 */}
      <div className="h-1.5 bg-[repeating-linear-gradient(45deg,transparent,transparent_10px,rgba(234,179,8,0.2)_10px,rgba(234,179,8,0.2)_20px)]" />

      {/* 头部 */}
      <div className="flex items-center gap-2 px-3 py-2 bg-yellow-500/10 border-b border-yellow-500/10">
        <AlertTriangle className="w-4 h-4 text-yellow-400" />
        <span className="text-xs font-medium text-yellow-300">警告</span>
      </div>

      {/* 内容 */}
      <div className="p-3">
        <p className="text-sm text-yellow-200/80 whitespace-pre-wrap leading-relaxed">
          {content}
        </p>
      </div>
    </div>
  );
}

// 6. 普通文本 - 默认渲染
function PlainBlock({ content }: { content: string }) {
  return (
    <p className="whitespace-pre-wrap leading-relaxed">
      {content}
    </p>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// 主组件
// ═══════════════════════════════════════════════════════════════════════════

interface SmartContentRendererProps {
  content: string;
  className?: string;
}

export function SmartContentRenderer({ content, className }: SmartContentRendererProps) {
  const segments = parseContent(content);

  if (segments.length === 0) {
    return null;
  }

  // 如果只包含纯文本，直接渲染
  if (segments.length === 1 && segments[0].type === 'plain') {
    return (
      <div className={className}>
        <PlainBlock content={segments[0].content} />
      </div>
    );
  }

  return (
    <div className={cn('space-y-1', className)}>
      {segments.map((segment, index) => {
        const key = `${segment.type}-${index}`;

        switch (segment.type) {
          case 'thinking':
            return <ThinkingBlock key={key} content={segment.content} />;
          case 'output':
            return <OutputBlock key={key} content={segment.content} />;
          case 'tool_call':
            return <ToolCallBlock key={key} content={segment.content} />;
          case 'error':
            return <ErrorBlock key={key} content={segment.content} />;
          case 'warning':
            return <WarningBlock key={key} content={segment.content} />;
          default:
            return <PlainBlock key={key} content={segment.content} />;
        }
      })}
    </div>
  );
}

export default SmartContentRenderer;

