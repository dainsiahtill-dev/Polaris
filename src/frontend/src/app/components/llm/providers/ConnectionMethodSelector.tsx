/**
 * ConnectionMethodSelector Component
 * 连接方式选择器
 */

import React, { useMemo } from 'react';
import { useProviderContext } from '../state';
import type { ConnectionMethodId } from '../state';

interface ConnectionMethodMeta {
  id: ConnectionMethodId;
  label: string;
  description: string;
  pros: string[];
  cons: string[];
  recommended?: boolean;
  accent: string;
  accentText: string;
  accentBorder: string;
}

const CONNECTION_METHODS: ConnectionMethodMeta[] = [
  {
    id: 'sdk',
    label: 'SDK 方式',
    description: '官方 SDK 集成，功能完整且稳定',
    pros: ['官方支持', '原生 thinking / streaming', '更好的错误处理', '更完整的功能'],
    cons: ['需要安装 SDK 依赖', '配置项稍多'],
    recommended: true,
    accent: 'bg-emerald-500/15',
    accentText: 'text-emerald-200',
    accentBorder: 'border-emerald-400/40',
  },
  {
    id: 'api',
    label: 'HTTP API 方式',
    description: 'REST API 访问，兼容性最好',
    pros: ['无需 SDK 依赖', '兼容多种服务', '部署简单'],
    cons: ['部分高级功能受限', '流式支持取决于服务端'],
    recommended: false,
    accent: 'bg-cyan-500/15',
    accentText: 'text-cyan-200',
    accentBorder: 'border-cyan-400/40',
  },
  {
    id: 'cli',
    label: '命令行方式',
    description: '使用 CLI 工具，适合本地开发',
    pros: ['本地工具链', '参数灵活', '适合快速试用'],
    cons: ['输出解析复杂', '依赖 CLI 安装'],
    recommended: false,
    accent: 'bg-fuchsia-500/15',
    accentText: 'text-fuchsia-200',
    accentBorder: 'border-fuchsia-400/40',
  },
];

interface ConnectionMethodSelectorProps {
  availableMethods?: ConnectionMethodId[];
}

export function ConnectionMethodSelector({ availableMethods }: ConnectionMethodSelectorProps) {
  const { state, selectMethod } = useProviderContext();
  const { selectedMethod } = state;

  const methods = useMemo(() => {
    if (!availableMethods || availableMethods.length === 0) {
      return CONNECTION_METHODS;
    }
    return CONNECTION_METHODS.filter((m) => availableMethods.includes(m.id));
  }, [availableMethods]);

  return (
    <div className="rounded-2xl border border-white/10 bg-[rgba(35,25,14,0.55)] p-4 shadow-[0_0_22px_rgba(34,211,238,0.12)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold text-text-main">连接方式选择</div>
          <div className="text-[10px] text-text-dim">先选连接方式，再选具体提供商。</div>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-text-dim">
          <span>推荐优先：</span>
          <span className="px-2 py-1 rounded border border-emerald-400/40 bg-emerald-500/10 text-emerald-200">
            SDK 方式
          </span>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3">
        {methods.map((method) => {
          const selected = selectedMethod === method.id;
          return (
            <button
              key={method.id}
              type="button"
              onClick={() => selectMethod(method.id)}
              className={`text-left rounded-xl border p-3 transition-all ${
                selected
                  ? `${method.accentBorder} ${method.accent} shadow-[0_0_18px_rgba(34,211,238,0.15)]`
                  : 'border-white/10 bg-[rgba(35,25,14,0.3)] hover:border-white/30'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className={`text-xs font-semibold ${selected ? method.accentText : 'text-text-main'}`}>
                  {method.label}
                </span>
                {method.recommended && (
                  <span className="text-[9px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-200 border border-emerald-500/40">
                    推荐
                  </span>
                )}
              </div>
              <div className="mt-1 text-[10px] text-text-dim">{method.description}</div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-[10px] text-text-dim">
                <div className="space-y-1">
                  <div className="text-[9px] uppercase tracking-wider text-text-dim">优势</div>
                  <div className="flex flex-wrap gap-1">
                    {method.pros.slice(0, 2).map((item) => (
                      <span key={item} className="px-2 py-0.5 rounded bg-white/5 text-text-dim">
                        {item}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="space-y-1">
                  <div className="text-[9px] uppercase tracking-wider text-text-dim">限制</div>
                  <div className="flex flex-wrap gap-1">
                    {method.cons.slice(0, 2).map((item) => (
                      <span key={item} className="px-2 py-0.5 rounded bg-white/5 text-text-dim">
                        {item}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export { CONNECTION_METHODS };
export type { ConnectionMethodMeta };
