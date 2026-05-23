import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useUIState } from './useUIState';

describe('useUIState', () => {
  it('updates cognition mode through an explicit action', () => {
    const { result } = renderHook(() => useUIState({ showCognition: false }));

    act(() => {
      result.current.actions.setShowCognition(true);
    });

    expect(result.current.state.showCognition).toBe(true);

    act(() => {
      result.current.actions.setShowCognition(false);
    });

    expect(result.current.state.showCognition).toBe(false);
  });

  it('dismisses a logs banner without closing the logs modal', () => {
    const { result } = renderHook(() => useUIState());

    act(() => {
      result.current.actions.openLogs('pm-subprocess', 'diagnostic details');
    });

    expect(result.current.state.isLogsOpen).toBe(true);
    expect(result.current.state.logsBanner).toBe('diagnostic details');

    act(() => {
      result.current.actions.dismissLogsBanner();
    });

    expect(result.current.state.isLogsOpen).toBe(true);
    expect(result.current.state.logsBanner).toBeNull();
  });
});
