import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useSettings } from '@/hooks';
import { useEngineeringRun } from '@/hooks/useEngineeringRun';
import { useFactoryBench } from '@/hooks/useFactoryBench';
import type { EngineeringRole } from '@/services/api.types';
import { RuntimeTransportProvider } from '@/runtime/transport';
import { EngineeringContextView } from '@/app/components/contextos/ContextOSWorkspace';
import { WorkspaceFilesPage } from '@/app/components/workspace-files/WorkspaceFilesPage';
import { RuntimeDiagnosticsWorkspace } from '@/app/components/RuntimeDiagnosticsWorkspace';
import { LegacyRunHistory } from '@/app/components/factory/LegacyRunHistory';
import { BenchPanel } from '@/app/components/factory/BenchPanel';
import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { Toaster } from '@/app/components/ui/sonner';
import { LauncherWorkspace } from '@/app/launcher/LauncherWorkspace';
import { shouldEnableGlobalBenchObserver } from '@/app/runtimeScope';
import { useRuntime } from './hooks/useRuntime';
import { ConsoleShell, EvidenceLedgerView, MissionControlView } from './console';
import { ROLE_NAV_LABELS } from './console/format';
import {
  IconCompass, IconContext, IconFlask, IconFolder, IconGauge, IconInbox,
  IconLedger, IconMonitor, IconSliders,
} from './console/icons';
import { devLogger } from '@/app/utils/devLogger';

const SettingsModal = lazy(() => import('./components/SettingsModal').then((module) => ({ default: module.SettingsModal })));

const VIEW_LABELS: Record<View, string> = {
  console: '运行控制台',
  ledger: '证据账本',
  contextos: 'ContextOS',
  files: '文件',
  history: '运行记录',
  diagnostics: '运行诊断',
  bench: '内部 Bench 观测',
};

type View = 'console' | 'ledger' | 'contextos' | 'files' | 'history' | 'diagnostics' | 'bench';
const ROLE_IDS: EngineeringRole[] = ['pm', 'chief_engineer', 'director', 'qa'];

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
    role: (['pm', 'chief_engineer', 'director', 'qa'] as const).find((id) => id === params.get('engineering_role')) || 'director',
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
  const [view, setView] = useState<View>('console');
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
  const railIcon = 'h-4.5 w-4.5 shrink-0';
  const railTop = ROLE_IDS.map((id) => ({
    id: `role-${id}`,
    label: ROLE_NAV_LABELS[id],
    icon: <IconMonitor className={railIcon} />,
    pressed: view === 'console' && role === id,
    onClick: () => { setRole(id); setView('console'); },
  }));
  const railMid = [
    { id: 'ledger', label: '证据账本', icon: <IconLedger className={railIcon} />, pressed: view === 'ledger', onClick: () => setView('ledger') },
    { id: 'contextos', label: 'ContextOS', icon: <IconContext className={railIcon} />, pressed: view === 'contextos', onClick: () => setView('contextos') },
    { id: 'files', label: '文件', icon: <IconFolder className={railIcon} />, pressed: view === 'files', onClick: () => setView('files') },
    { id: 'history', label: '运行记录', icon: <IconInbox className={railIcon} />, pressed: view === 'history', onClick: () => setView('history') },
    { id: 'diagnostics', label: '运行诊断', icon: <IconGauge className={railIcon} />, pressed: view === 'diagnostics', onClick: () => setView('diagnostics') },
    ...(globalBenchObserverEnabled ? [{ id: 'bench', label: '内部 Bench 观测', icon: <IconFlask className={railIcon} />, pressed: view === 'bench', onClick: () => setView('bench' as View) }] : []),
  ];
  const returnToConsole = () => setView('console');
  return <ErrorBoundaryClass onError={(error) => devLogger.error(error.message)}>
    <ConsoleShell
      workspace={workspace}
      viewLabel={VIEW_LABELS[view]}
      connected={engineering.connected}
      reconnecting={engineering.reconnecting}
      footerStatus={engineering.connected ? '运行中' : '连接断开'}
      railTop={railTop}
      railMid={railMid}
      railBottom={[
        { id: 'settings', label: '系统配置', icon: <IconSliders className={railIcon} />, pressed: settingsOpen, onClick: () => setSettingsOpen(true) },
        { id: 'launcher', label: 'Launcher', icon: <IconCompass className={railIcon} />, href: launcherUrl },
      ]}
    >
      {bindingError ? <p role="alert" className="p-6 text-sm text-[var(--cx-red)]">{bindingError}</p>
        : view === 'ledger' ? <div className="min-h-0 flex-1 overflow-y-auto cx-scroll"><EvidenceLedgerView binding={engineering} /></div>
          : view === 'contextos' ? <EngineeringContextView engineering={engineering} workspace={workspace} onBackToMain={returnToConsole} />
            : view === 'files' ? <WorkspaceFilesPage workspace={workspace} onBackToMain={returnToConsole} />
              : view === 'diagnostics' ? <RuntimeDiagnosticsWorkspace workspace={workspace} connectionState={{ live: engineering.connected, reconnecting: engineering.reconnecting, attemptCount: 0 }} onBackToMain={returnToConsole} />
                : view === 'history' ? <LegacyRunHistory workspace={workspace} onBackToMain={returnToConsole} />
                  : view === 'bench' && globalBenchObserverEnabled ? <BenchPanel enabled bench={bench} />
                    : <MissionControlView workspace={workspace} binding={engineering} role={role} onRoleChange={setRole} />}
      {settingsError && <p role="alert" className="border-t border-[var(--cx-line)] bg-[#fbfaf5] px-5 py-2 text-sm text-[var(--cx-red)]">{settingsError}</p>}
      <Suspense fallback={<p role="status" className="border-t border-[var(--cx-line)] bg-[#fbfaf5] px-5 py-2 text-xs text-[var(--cx-ink-dim)]">正在载入系统配置…</p>}>
        {settingsOpen && <SettingsModal isOpen settings={settings} onClose={() => setSettingsOpen(false)} onSave={async (updates) => {
          try { setSettingsError(''); await updateSettings(updates); setSettingsOpen(false); }
          catch (error) { setSettingsError(error instanceof Error ? error.message : '保存配置失败'); }
        }} />}
      </Suspense>
      <Toaster position="bottom-right" />
    </ConsoleShell>
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
