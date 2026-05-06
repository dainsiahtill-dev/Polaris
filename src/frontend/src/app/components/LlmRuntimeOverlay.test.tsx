import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LlmRuntimeOverlay } from './LlmRuntimeOverlay';

const defaultProps = {
  activeView: 'main' as const,
  websocketLive: true,
  websocketReconnecting: false,
  websocketAttemptCount: 0,
  pmRunning: false,
  directorRunning: false,
  llmState: 'ready',
  llmBlockedRoles: [],
  llmRequiredRoles: [],
  llmLastUpdated: null,
  currentPhase: 'idle',
  qualityGate: null,
  executionLogs: [],
  llmStreamEvents: [],
  processStreamEvents: [],
};

describe('LlmRuntimeOverlay', () => {
  it('stays below modal dialogs so onboarding actions remain clickable', () => {
    render(<LlmRuntimeOverlay {...defaultProps} />);

    const overlay = screen.getByTestId('llm-runtime-overlay');

    expect(overlay).toHaveClass('z-40');
    expect(overlay).not.toHaveClass('z-[75]');
  });
});
