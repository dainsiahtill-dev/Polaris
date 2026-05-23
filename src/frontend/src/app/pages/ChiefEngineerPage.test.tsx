import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ChiefEngineerPage } from './ChiefEngineerPage';

const chiefEngineerWorkspaceProps = vi.hoisted(() => vi.fn());
const runtimeOverlayProps = vi.hoisted(() => vi.fn());

vi.mock('@/app/components/chief-engineer', () => ({
  ChiefEngineerWorkspace: (props: {
    onOpenSettings?: () => void;
    onEnterDirectorWorkspace: () => void;
  }) => {
    chiefEngineerWorkspaceProps(props);
    return (
      <div>
        <button type="button" data-testid="chief-engineer-page-settings" onClick={props.onOpenSettings}>
          Settings
        </button>
        <button type="button" data-testid="chief-engineer-page-enter-director" onClick={props.onEnterDirectorWorkspace}>
          Director
        </button>
      </div>
    );
  },
}));

vi.mock('@/app/components/LlmRuntimeOverlay', () => ({
  LlmRuntimeOverlay: (props: { activeView: string; pmRunning?: boolean; directorRunning?: boolean }) => {
    runtimeOverlayProps(props);
    return <div data-testid="runtime-overlay">{props.activeView}</div>;
  },
}));

vi.mock('@/app/components/ui/sonner', () => ({
  Toaster: () => <div data-testid="toaster" />,
}));

function renderPage(overrides: Partial<Parameters<typeof ChiefEngineerPage>[0]> = {}): void {
  render(
    <ChiefEngineerPage
      workspace="C:/Temp/Product"
      engineStatus={null}
      tasks={[]}
      workers={[]}
      pmState={null}
      directorRunning={false}
      isStartingDirector={false}
      onBackToMain={vi.fn()}
      onEnterDirectorWorkspace={vi.fn()}
      onToggleDirector={vi.fn()}
      websocketLive={true}
      websocketReconnecting={false}
      websocketAttemptCount={0}
      llmRuntimeState={{
        state: 'READY',
        blockedRoles: [],
        requiredRoles: ['chief_engineer'],
        lastUpdated: '2026-05-23T00:00:00Z',
      }}
      notifyError={vi.fn()}
      {...overrides}
    />,
  );
}

describe('ChiefEngineerPage', () => {
  it('forwards settings and Director navigation callbacks to ChiefEngineerWorkspace', () => {
    const onOpenSettings = vi.fn();
    const onEnterDirectorWorkspace = vi.fn();

    renderPage({ onOpenSettings, onEnterDirectorWorkspace });

    fireEvent.click(screen.getByTestId('chief-engineer-page-settings'));
    fireEvent.click(screen.getByTestId('chief-engineer-page-enter-director'));

    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(onEnterDirectorWorkspace).toHaveBeenCalledTimes(1);
    expect(chiefEngineerWorkspaceProps).toHaveBeenCalledWith(expect.objectContaining({
      onOpenSettings,
      onEnterDirectorWorkspace,
    }));
  });

  it('binds runtime overlay evidence to the Chief Engineer role view', () => {
    renderPage({ pmRunning: true, directorRunning: true });

    expect(screen.getByTestId('runtime-overlay')).toHaveTextContent('chief_engineer');
    expect(runtimeOverlayProps).toHaveBeenCalledWith(expect.objectContaining({
      activeView: 'chief_engineer',
      pmRunning: true,
      directorRunning: true,
    }));
  });
});
