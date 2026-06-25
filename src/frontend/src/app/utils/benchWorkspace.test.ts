import { describe, expect, it, vi } from 'vitest';
import {
  applyBenchObservedWorkspaceChange,
  resolveBenchObservedWorkspace,
} from './benchWorkspace';

describe('benchWorkspace utilities', () => {
  it('resolves relative observed bench workspaces under the active settings workspace', () => {
    expect(resolveBenchObservedWorkspace('factory-bench-l1-01', '/tmp/polaris-root')).toBe(
      '/tmp/polaris-root/factory-bench-l1-01',
    );
    expect(resolveBenchObservedWorkspace('/tmp/factory-bench-l1-01', '/tmp/polaris-root')).toBe(
      '/tmp/factory-bench-l1-01',
    );
  });

  it('updates only the observed workspace state and does not persist global settings', () => {
    const setProgressSnapshot = vi.fn();
    const setBenchObservedWorkspace = vi.fn();
    const updateSettings = vi.fn();

    const applied = applyBenchObservedWorkspaceChange({
      nextWorkspace: '/tmp/factory-bench-l1-04/project',
      settingsWorkspace: '/home/user/project',
      currentWorkspace: '/home/user/project',
      setProgressSnapshot,
      setBenchObservedWorkspace,
    });

    expect(applied).toBe('/tmp/factory-bench-l1-04/project');
    expect(setProgressSnapshot).toHaveBeenCalledWith(null);
    expect(setBenchObservedWorkspace).toHaveBeenCalledWith('/tmp/factory-bench-l1-04/project');
    expect(updateSettings).not.toHaveBeenCalled();
  });

  it('ignores empty or unchanged observed workspace values', () => {
    const setProgressSnapshot = vi.fn();
    const setBenchObservedWorkspace = vi.fn();

    const unchanged = applyBenchObservedWorkspaceChange({
      nextWorkspace: '/tmp/factory-bench-l1-04/project',
      settingsWorkspace: '/home/user/project',
      currentWorkspace: '/tmp/factory-bench-l1-04/project',
      setProgressSnapshot,
      setBenchObservedWorkspace,
    });

    const empty = applyBenchObservedWorkspaceChange({
      nextWorkspace: '',
      settingsWorkspace: '/home/user/project',
      currentWorkspace: '/home/user/project',
      setProgressSnapshot,
      setBenchObservedWorkspace,
    });

    expect(unchanged).toBe('');
    expect(empty).toBe('');
    expect(setProgressSnapshot).not.toHaveBeenCalled();
    expect(setBenchObservedWorkspace).not.toHaveBeenCalled();
  });
});
