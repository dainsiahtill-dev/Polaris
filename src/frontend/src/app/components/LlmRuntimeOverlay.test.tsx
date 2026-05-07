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

  it('does not render blocked roles when LLM status is idle/unknown', () => {
    render(
      <LlmRuntimeOverlay
        {...defaultProps}
        llmState="unknown"
        llmRequiredRoles={['pm', 'director', 'qa']}
        llmBlockedRoles={['pm', 'director', 'qa']}
      />
    );

    expect(screen.getByText('LLM IDLE')).toBeInTheDocument();
    expect(screen.queryByText(/required:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/blocked:/)).not.toBeInTheDocument();
  });

  it('does not show a blocked alarm while runtime is idle', () => {
    render(
      <LlmRuntimeOverlay
        {...defaultProps}
        llmState="blocked"
        llmRequiredRoles={['pm', 'director', 'qa']}
        llmBlockedRoles={['pm', 'director']}
      />
    );

    expect(screen.getByText('LLM IDLE')).toBeInTheDocument();
    expect(screen.queryByText('LLM BLOCKED')).not.toBeInTheDocument();
    expect(screen.queryByText('required: pm, director, qa')).not.toBeInTheDocument();
    expect(screen.queryByText('blocked: pm, director')).not.toBeInTheDocument();
  });

  it('renders blocked roles only when a runtime flow is active', () => {
    render(
      <LlmRuntimeOverlay
        {...defaultProps}
        pmRunning={true}
        llmState="blocked"
        llmRequiredRoles={['pm', 'director', 'qa']}
        llmBlockedRoles={['pm', 'director']}
      />
    );

    expect(screen.getByText('LLM BLOCKED')).toBeInTheDocument();
    expect(screen.getByText('required: pm, director, qa')).toBeInTheDocument();
    expect(screen.getByText('blocked: pm, director')).toBeInTheDocument();
  });

  it('filters structured JSON fragments from the real-time event list', () => {
    const timestamp = new Date().toISOString();
    render(
      <LlmRuntimeOverlay
        {...defaultProps}
        pmRunning={true}
        llmState="unknown"
        llmStreamEvents={[
          {
            id: 'json-close',
            timestamp,
            level: 'info',
            source: 'Engine',
            message: '}',
          },
          {
            id: 'json-error',
            timestamp,
            level: 'info',
            source: 'Engine',
            message: '"error": ""',
          },
          {
            id: 'useful',
            timestamp,
            level: 'info',
            source: 'Engine',
            message: 'Director workflow completed',
          },
        ]}
      />
    );

    expect(screen.getAllByText('Director workflow completed').length).toBeGreaterThan(0);
    expect(screen.queryByText('}')).not.toBeInTheDocument();
    expect(screen.queryByText('"error": ""')).not.toBeInTheDocument();
  });

  it('filters structured JSON fragments from details and shows latest file edit evidence', () => {
    const timestamp = new Date().toISOString();
    render(
      <LlmRuntimeOverlay
        {...defaultProps}
        pmRunning={true}
        llmState="unknown"
        llmStreamEvents={[
          {
            id: 'useful-with-noisy-details',
            timestamp,
            level: 'info',
            source: 'Engine',
            message: 'Director workflow completed',
            details: '"summary": {},',
          },
        ]}
        fileEditEvents={[
          {
            id: 'file-1',
            filePath: 'src/app.tsx',
            operation: 'modify',
            contentSize: 42,
            timestamp,
          },
        ]}
      />
    );

    expect(screen.getAllByText('Director workflow completed').length).toBeGreaterThan(0);
    expect(screen.queryByText('"summary": {},')).not.toBeInTheDocument();
    expect(screen.getByTestId('llm-runtime-file-edit')).toHaveTextContent('src/app.tsx');
    expect(screen.getByTestId('llm-runtime-file-edit')).toHaveTextContent('modify');
  });

  it('does not fall back to JSON fragments when every recent event is structured noise', () => {
    const timestamp = new Date().toISOString();
    render(
      <LlmRuntimeOverlay
        {...defaultProps}
        pmRunning={true}
        llmState="unknown"
        llmStreamEvents={[
          {
            id: 'json-close',
            timestamp,
            level: 'info',
            source: 'Engine',
            message: '}',
          },
          {
            id: 'json-error',
            timestamp,
            level: 'info',
            source: 'Engine',
            message: '"error": ""',
          },
          {
            id: 'json-updated',
            timestamp,
            level: 'info',
            source: 'Engine',
            message: '"updated_at": "2026-05-07T07:16:25Z",',
          },
        ]}
      />
    );

    expect(screen.getByText('0 events')).toBeInTheDocument();
    expect(screen.getByText('等待 LLM 事件流...')).toBeInTheDocument();
    expect(screen.queryByText('}')).not.toBeInTheDocument();
    expect(screen.queryByText('"error": ""')).not.toBeInTheDocument();
    expect(screen.queryByText('"updated_at": "2026-05-07T07:16:25Z",')).not.toBeInTheDocument();
  });
});
