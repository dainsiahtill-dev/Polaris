import { createElement } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  listInstances: vi.fn(),
  subscribeChannels: vi.fn(() => vi.fn()),
  registerMessageHandler: vi.fn(() => vi.fn()),
  action: vi.fn(),
}));

vi.mock('@/runtime/transport', () => ({
  useConnectionState: () => ({ connected: true, reconnecting: false, error: null }),
  useMessageHandler: () => ({ registerMessageHandler: mocks.registerMessageHandler }),
  useTransportActions: () => ({ subscribeChannels: mocks.subscribeChannels }),
}));

vi.mock('@/services/instances', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/instances')>(),
  listInstances: mocks.listInstances,
  startInstance: mocks.action,
  stopInstance: mocks.action,
  restartInstance: mocks.action,
  deleteInstance: mocks.action,
  getInstanceLogs: mocks.action,
}));

import { LauncherWorkspace } from './LauncherWorkspace';

beforeEach(() => {
  window.history.replaceState(null, '', '/launcher?backend=http%3A%2F%2F127.0.0.1%3A49977&workspace=%2Fproject&token=example-token');
  mocks.listInstances.mockReset();
  mocks.listInstances.mockResolvedValue({ ok: true, data: { instances: [] } });
  mocks.subscribeChannels.mockClear();
  mocks.registerMessageHandler.mockClear();
  mocks.action.mockClear();
});

describe('Launcher embedded foundation documentation', () => {
  it('opens the documentation inside Launcher, preserves the instance binding and restores browser back', async () => {
    render(createElement(LauncherWorkspace));
    await screen.findByRole('heading', { name: '启动实例' });
    fireEvent.click(screen.getByRole('button', { name: '原理研究' }));
    expect(screen.getByRole('heading', { name: 'Polaris 底座原理' })).toBeInTheDocument();
    const search = new URLSearchParams(window.location.search);
    expect(search.get('view')).toBe('foundation');
    expect(search.get('workspace')).toBe('/project');
    expect(search.get('token')).toBe('example-token');
    expect(search.get('backend')).toBe('http://127.0.0.1:49977');
    window.history.replaceState(null, '', '/launcher?workspace=%2Fproject');
    fireEvent(window, new PopStateEvent('popstate'));
    await screen.findByRole('heading', { name: '启动实例' });
    expect(mocks.action).not.toHaveBeenCalled();
  });

  it('opens a deep-linked article without mounting the instance control view', async () => {
    window.history.replaceState(null, '', '/launcher?view=foundation&topic=contextos&workspace=%2Fproject');
    render(createElement(LauncherWorkspace));
    expect(screen.getByRole('heading', { name: 'Polaris 底座原理' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'ContextOS', level: 2 })).toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.listInstances).not.toHaveBeenCalled();
      expect(mocks.subscribeChannels).not.toHaveBeenCalled();
    });
    fireEvent.click(screen.getByRole('button', { name: '返回实例总控' }));
    await screen.findByRole('heading', { name: '启动实例' });
    expect(new URLSearchParams(window.location.search).get('workspace')).toBe('/project');
  });
});
