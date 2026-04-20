/**
 * Settings Modal
 *
 * Main entry point for application settings.
 * Provides tabbed interface for different setting categories.
 *
 * This is a refactored version that delegates to individual tab components
 * for better maintainability and reduced complexity.
 */

import { useState, useEffect } from 'react';
import { X, Settings } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { apiFetch } from '@/api';
import type { LLMStatus } from '@/app/components/llm/types';

// Import tab components
import { GeneralSettingsTab } from './GeneralSettingsTab';
import { LLMSettingsBridge } from './LLMSettingsBridge';
import { WorkflowSettingsTab } from './WorkflowSettingsTab';
import { SystemServicesTabHost } from './SystemServicesTabHost';

// Tab definitions
const TABS = [
  { id: 'general', label: '通用', icon: Settings },
  { id: 'llm', label: 'LLM', icon: null },
  { id: 'workflow', label: '工作流', icon: null },
  { id: 'system', label: '系统', icon: null },
] as const;

type TabId = typeof TABS[number]['id'];

interface SettingsModalProps {
  isOpen: boolean;
  initialTab?: TabId;
  onClose: () => void;
  onLlmStatusChange?: (status: LLMStatus | null) => void;
  settings: {
    prompt_profile?: string;
    interval?: number;
    timeout?: number;
    refresh_interval?: number;
    auto_refresh?: boolean;
    show_memory?: boolean;
    io_fsync_mode?: string;
    memory_refs_mode?: string;
    ramdisk_root?: string;
    json_log_path?: string;
    pm_show_output?: boolean;
    pm_runs_director?: boolean;
    pm_director_show_output?: boolean;
    pm_director_timeout?: number;
    pm_director_iterations?: number;
    pm_director_match_mode?: string;
    pm_max_failures?: number;
    pm_max_blocked?: number;
    pm_max_same?: number;
    director_iterations?: number;
    director_execution_mode?: 'serial' | 'parallel' | string;
    director_max_parallel_tasks?: number;
    director_ready_timeout_seconds?: number;
    director_claim_timeout_seconds?: number;
    director_phase_timeout_seconds?: number;
    director_complete_timeout_seconds?: number;
    director_task_timeout_seconds?: number;
    director_forever?: boolean;
    director_show_output?: boolean;
    slm_enabled?: boolean;
    qa_enabled?: boolean;
    debug_tracing?: boolean;
    backend_port?: number;
    frontend_port?: number;
  } | null;
  onSave: (payload: Record<string, unknown>) => Promise<void>;
}

/**
 * Settings Modal Component
 *
 * @example
 * ```tsx
 * <SettingsModal
 *   isOpen={showSettings}
 *   onClose={() => setShowSettings(false)}
 *   settings={appSettings}
 *   onSave={handleSaveSettings}
 * />
 * ```
 */
export function SettingsModal({
  isOpen,
  initialTab = 'general',
  onClose,
  onLlmStatusChange,
  settings,
  onSave,
}: SettingsModalProps) {
  const [activeTab, setActiveTab] = useState<TabId>(initialTab);

  // Reset tab when modal opens
  useEffect(() => {
    if (isOpen) {
      setActiveTab(initialTab);
    }
  }, [isOpen, initialTab]);

  // Close on escape key
  useEffect(() => {
    if (!isOpen) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div className="relative w-full max-w-6xl h-[85vh] bg-slate-900 rounded-2xl border border-slate-700 shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-slate-900/95">
          <div className="flex items-center gap-4">
            <h2 className="text-lg font-semibold text-slate-100">设置</h2>

            {/* Tab Navigation */}
            <nav className="flex items-center gap-1 ml-4">
              {TABS.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={cn(
                    'px-4 py-1.5 text-sm font-medium rounded-lg transition-all',
                    activeTab === tab.id
                      ? 'bg-slate-800 text-slate-100'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>

          <button
            onClick={onClose}
            className="p-2 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors"
            aria-label="关闭"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden">
          <div className="h-full overflow-y-auto p-6">
            {activeTab === 'general' && (
              <GeneralSettingsTab settings={settings} onSave={onSave} />
            )}
            {activeTab === 'llm' && (
              <LLMSettingsBridge onLlmStatusChange={onLlmStatusChange} />
            )}
            {activeTab === 'workflow' && (
              <WorkflowSettingsTab settings={settings} onSave={onSave} />
            )}
            {activeTab === 'system' && <SystemServicesTabHost />}
          </div>
        </div>
      </div>
    </div>
  );
}

export default SettingsModal;
