/**
 * Settings Components
 *
 * Modular settings UI components following the Canonical Contract pattern.
 *
 * @example
 * ```tsx
 * import { SettingsModal } from './settings';
 *
 * function App() {
 *   return (
 *     <SettingsModal
 *       isOpen={showSettings}
 *       onClose={() => setShowSettings(false)}
 *       settings={appSettings}
 *       onSave={handleSave}
 *     />
 *   );
 * }
 * ```
 */

// Main entry
export { SettingsModal } from './SettingsModal';

// Individual tabs (for advanced usage)
export { GeneralSettingsTab } from './GeneralSettingsTab';
export { LLMSettingsBridge } from './LLMSettingsBridge';
export { WorkflowSettingsTab } from './WorkflowSettingsTab';
export { SystemServicesTabHost } from './SystemServicesTabHost';
