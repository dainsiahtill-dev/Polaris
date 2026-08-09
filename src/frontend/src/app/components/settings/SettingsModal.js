import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
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
];
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
export function SettingsModal({ isOpen, initialTab = 'general', onClose, onLlmStatusChange, settings, onSave, }) {
    const [activeTab, setActiveTab] = useState(initialTab);
    // Reset tab when modal opens
    useEffect(() => {
        if (isOpen) {
            setActiveTab(initialTab);
        }
    }, [isOpen, initialTab]);
    // Close on escape key
    useEffect(() => {
        if (!isOpen)
            return;
        const handleEscape = (e) => {
            if (e.key === 'Escape') {
                onClose();
            }
        };
        document.addEventListener('keydown', handleEscape);
        return () => document.removeEventListener('keydown', handleEscape);
    }, [isOpen, onClose]);
    if (!isOpen)
        return null;
    return (_jsx("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-[#020610]/78 backdrop-blur-xl p-4", onClick: (e) => {
            if (e.target === e.currentTarget) {
                onClose();
            }
        }, children: _jsxs("div", { className: "soft-panel relative w-full max-w-6xl h-[85vh] rounded-xl overflow-hidden flex flex-col text-text-main", children: [_jsxs("div", { className: "soft-panel-subtle flex items-center justify-between px-6 py-4 border-b", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsx("h2", { className: "text-lg font-semibold text-text-main", children: "\u8BBE\u7F6E" }), _jsx("nav", { className: "flex items-center gap-1 ml-4", children: TABS.map((tab) => (_jsx("button", { onClick: () => setActiveTab(tab.id), className: cn('px-4 py-1.5 text-sm font-medium rounded-lg transition-all', activeTab === tab.id
                                            ? 'soft-raised text-text-main'
                                            : 'text-text-muted hover:text-text-main hover:bg-white/60'), children: tab.label }, tab.id))) })] }), _jsx("button", { onClick: onClose, className: "p-2 rounded-lg text-text-muted hover:text-text-main hover:bg-white/70 transition-colors", "aria-label": "\u5173\u95ED", children: _jsx(X, { className: "w-5 h-5" }) })] }), _jsx("div", { className: "flex-1 overflow-hidden", children: _jsxs("div", { className: "h-full overflow-y-auto p-6", children: [activeTab === 'general' && (_jsx(GeneralSettingsTab, { settings: settings, onSave: onSave })), activeTab === 'llm' && (_jsx(LLMSettingsBridge, { onLlmStatusChange: onLlmStatusChange })), activeTab === 'workflow' && (_jsx(WorkflowSettingsTab, { settings: settings, onSave: onSave })), activeTab === 'system' && _jsx(SystemServicesTabHost, {})] }) })] }) }));
}
export default SettingsModal;
