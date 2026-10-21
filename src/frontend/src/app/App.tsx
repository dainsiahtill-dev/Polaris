import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useSettings } from '@/hooks';
import { useEngineeringRun } from '@/hooks/useEngineeringRun';
import { useFactoryBench } from '@/hooks/useFactoryBench';
import type { EngineeringRole } from '@/services/api.types';
import { RuntimeTransportProvider } from '@/runtime/transport';
import { LegacyRunHistory } from '@/app/components/factory/LegacyRunHistory';
import { EngineeringRunWorkspace } from '@/app/components/factory/EngineeringRunWorkspace';
import { EngineeringContextView } from '@/app/components/contextos/ContextOSWorkspace';
import { BenchPanel } from '@/app/components/factory/BenchPanel';
import { WorkspaceFilesPage } from '@/app/components/workspace-files/WorkspaceFilesPage';
import { RuntimeDiagnosticsWorkspace } from '@/app/components/RuntimeDiagnosticsWorkspace';
import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { Toaster } from '@/app/components/ui/sonner';
import { LauncherWorkspace } from '@/app/launcher/LauncherWorkspace';
import { shouldEnableGlobalBenchObserver } from '@/app/runtimeScope';
import { useRuntime } from './hooks/useRuntime';
import { devLogger } from '@/app/utils/devLogger';

const SettingsModal = lazy(() => import('./components/SettingsModal').then((module) => ({ default: module.SettingsModal })));
const roles: { id: EngineeringRole; label: string }[] = [
  { id: 'director', label: 'Director 工程' },
  { id: 'pm', label: 'PM 规划' },
  { id: 'chief_engineer', label: 'CE 设计' },
  { id: 'qa', label: 'QA 审阅' },
];
type View = EngineeringRole | 'contextos' | 'files' | 'history' | 'diagnostics' | 'bench';

function readInitialRuntimeScope() {
  const params = new URLSearchParams(window.location.search);
  const readBinding = (...values: unknown[]): string => values.find(
    (value): value is string => typeof value === 'string' && Boolean(value.trim()),
  )?.trim() || '';
  return {
    workspace: readBinding(params.get('workspace'), params.get('polarisWorkspace'), import.meta.env.VITE_POLARIS_WORKSPACE, import.meta.env.VITE_WORKSPACE),
    instance: readBinding(params.get('instance'), params.get('instanceId'), params.get('polarisInstance'), import.meta.env.VITE_POLARIS_INSTANCE_ID),
    globalObserverRequested: readBinding(params.get('globalObserver') ?? import.meta.env.VITE_POLARIS_GLOBAL_BENCH_OBSERVER) === '1',
    run: params.get('engineering_run') || '',
    role: roles.find(({ id }) => id === params.get('engineering_role'))?.id || 'director',
  };
}

/** Engineering is the sole writable product entry; legacy state is observation only. */
function AppContent() {
  const [initial] = useState(readInitialRuntimeScope);
  const { settings, update: updateSettings } = useSettings();
  const [workspace, setWorkspace] = useState(() => initial.workspace || (initial.instance ? '' : settings?.workspace || ''));
  useEffect(() => {
    if (!workspace && !initial.instance && settings?.workspace) setWorkspace(settings.workspace);
  }, [workspace, initial.instance, settings?.workspace]);
  const bindingError = initial.instance && !initial.workspace
    ? '实例缺少 workspace 绑定。请从 Launcher 重新打开该实例。'
    : !workspace ? '尚未绑定工作区。请检查实例入口或系统配置。' : '';
  const [role, setRole] = useState<EngineeringRole>(initial.role);
  const [view, setView] = useState<View>(initial.role);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsError, setSettingsError] = useState('');
  const engineering = useEngineeringRun({ workspace, sessionId: initial.run, enabled: !bindingError });
  const internalBenchFlag = String(import.meta.env.VITE_POLARIS_INTERNAL_BENCH ?? '').trim();
  const internalBenchEnabled = internalBenchFlag === '1' || (import.meta.env.DEV && internalBenchFlag !== '0');
  const globalBenchObserverEnabled = shouldEnableGlobalBenchObserver(
    internalBenchEnabled, initial.workspace, initial.globalObserverRequested, initial.instance,
  );
  // Legacy runtime notifications remain read-only observation; they never dispatch work.
  useRuntime({ roles: ['pm', 'chief_engineer', 'director', 'qa'], workspace, includeInternalBench: globalBenchObserverEnabled, tailLines: 240 });
  const bench = useFactoryBench({ enabled: globalBenchObserverEnabled, autoSelect: globalBenchObserverEnabled ? 'newest' : 'none' });
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set('engine', 'engineering_v2');
    url.searchParams.set('engineering_role', role);
    if (workspace && (!globalBenchObserverEnabled || engineering.sessionId)) url.searchParams.set('workspace', workspace);
    if (engineering.sessionId) url.searchParams.set('engineering_run', engineering.sessionId);
    else url.searchParams.delete('engineering_run');
    window.history.replaceState(window.history.state, '', url);
  }, [engineering.sessionId, role, workspace, globalBenchObserverEnabled]);
  const launcherUrl = useMemo(() => {
    const url = new URL(window.location.href);
    url.pathname = '/launcher';
    url.searchParams.delete('engineering_run');
    url.searchParams.delete('engineering_role');
    return `${url.pathname}${url.search}`;
  }, []);
  const returnToEngineering = () => setView(role);
  const navClass = 'rounded-lg px-3 py-3 text-sm text-slate-300 hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-400';
  return <ErrorBoundaryClass onError={(error) => devLogger.error(error.message)}>
    <div className="polaris-soft-scope soft-app-bg flex size-full min-h-0 flex-col overflow-hidden text-slate-100">
      <nav aria-label="工作区导航" className="flex shrink-0 flex-wrap items-center gap-2 border-b border-white/10 px-4 py-2">
        {roles.map((entry) => <button key={entry.id} type="button" aria-pressed={view === entry.id} className={navClass} onClick={() => { setRole(entry.id); setView(entry.id); }}>{entry.label}</button>)}
        <button type="button" className={navClass} onClick={() => setView('contextos')}>ContextOS</button>
        <button type="button" className={navClass} onClick={() => setView('files')}>文件</button>
        <button type="button" className={navClass} onClick={() => setView('history')}>历史记录（只读）</button>
        <button type="button" className={navClass} onClick={() => setView('diagnostics')}>运行诊断</button>
        {globalBenchObserverEnabled && <button type="button" className={navClass} onClick={() => setView('bench')}>内部 Bench 观测</button>}
        <button type="button" className={navClass} onClick={() => setSettingsOpen(true)}>系统配置</button>
        <a className={navClass} href={launcherUrl}>Launcher</a>
      </nav>
      <div className="min-h-0 flex-1">
        {bindingError ? <p role="alert" className="p-6 text-red-200">{bindingError}</p>
          : view === 'contextos' ? <EngineeringContextView engineering={engineering} workspace={workspace} onBackToMain={returnToEngineering} />
          : view === 'files' ? <WorkspaceFilesPage workspace={workspace} onBackToMain={returnToEngineering} />
          : view === 'diagnostics' ? <RuntimeDiagnosticsWorkspace workspace={workspace} connectionState={{ live: engineering.connected, reconnecting: engineering.reconnecting, attemptCount: 0 }} onBackToMain={returnToEngineering} />
          : view === 'history' ? <LegacyRunHistory workspace={workspace} onBackToMain={returnToEngineering} />
          : view === 'bench' && globalBenchObserverEnabled ? <BenchPanel enabled bench={bench} />
          : <EngineeringRunWorkspace workspace={workspace} binding={engineering} role={role} onBackToMain={returnToEngineering} />}
      </div>
      {settingsError && <p role="alert" className="p-3 text-red-200">{settingsError}</p>}
      <Suspense fallback={<p role="status">正在载入系统配置…</p>}>
        {settingsOpen && <SettingsModal isOpen settings={settings} onClose={() => setSettingsOpen(false)} onSave={async (updates) => {
          try { setSettingsError(''); await updateSettings(updates); setSettingsOpen(false); }
          catch (error) { setSettingsError(error instanceof Error ? error.message : '保存配置失败'); }
        }} />}
      </Suspense>
      <Toaster position="bottom-right" />
    </div>
  </ErrorBoundaryClass>;
}

export default function App(): React.ReactElement {
  useEffect(() => {
    const handler = (event: PromiseRejectionEvent) => devLogger.error('[Unhandled Promise Rejection]:', event.reason);
    window.addEventListener('unhandledrejection', handler);
    return () => window.removeEventListener('unhandledrejection', handler);
  }, []);
  const launcherMode = window.location.pathname === '/launcher' || new URLSearchParams(window.location.search).get('launcher') === '1';
  return <RuntimeTransportProvider autoConnect>{launcherMode ? <LauncherWorkspace /> : <AppContent />}</RuntimeTransportProvider>;
}
