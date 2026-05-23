import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StrategyEditorPanel, type DirectorExecutionStrategy } from '../StrategyEditorPanel';

vi.mock('@monaco-editor/react', () => ({
  default: ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (value: string | undefined) => void;
  }) => (
    <textarea
      data-testid="strategy-json-editor"
      value={value || ''}
      onChange={(event) => onChange?.(event.currentTarget.value)}
    />
  ),
}));

const baseStrategy: DirectorExecutionStrategy = {
  name: 'director-base',
  version: '1.0.0',
  mode: 'parallel',
  limits: {
    iterations: 1,
    maxParallelTasks: 3,
    readyTimeoutSeconds: 30,
    claimTimeoutSeconds: 30,
    phaseTimeoutSeconds: 900,
    completeTimeoutSeconds: 30,
    taskTimeoutSeconds: 3600,
  },
  observability: {
    forever: false,
    showOutput: true,
  },
};

describe('StrategyEditorPanel', () => {
  it('updates from backend strategy props and saves valid Director settings JSON', async () => {
    const onSave = vi.fn();
    const { rerender } = render(
      <StrategyEditorPanel
        initialStrategy={JSON.stringify(baseStrategy, null, 2)}
        onSave={onSave}
      />,
    );

    expect((screen.getByTestId('strategy-json-editor') as HTMLTextAreaElement).value).toContain('"mode": "parallel"');

    const serialStrategy = {
      ...baseStrategy,
      name: 'director-serial',
      mode: 'serial' as const,
    };
    rerender(
      <StrategyEditorPanel
        initialStrategy={JSON.stringify(serialStrategy, null, 2)}
        onSave={onSave}
      />,
    );
    expect((screen.getByTestId('strategy-json-editor') as HTMLTextAreaElement).value).toContain('"mode": "serial"');

    const editedStrategy = {
      ...serialStrategy,
      limits: {
        ...serialStrategy.limits,
        iterations: 3,
      },
    };
    fireEvent.change(screen.getByTestId('strategy-json-editor'), {
      target: { value: JSON.stringify(editedStrategy, null, 2) },
    });
    fireEvent.click(screen.getByTestId('strategy-editor-save'));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
        mode: 'serial',
        limits: expect.objectContaining({ iterations: 3 }),
      }));
    });
  });

  it('blocks saving invalid strategy JSON and surfaces validation errors', () => {
    const onSave = vi.fn();
    render(
      <StrategyEditorPanel
        initialStrategy={JSON.stringify(baseStrategy, null, 2)}
        onSave={onSave}
      />,
    );

    fireEvent.change(screen.getByTestId('strategy-json-editor'), {
      target: { value: JSON.stringify({ mode: 'parallel' }, null, 2) },
    });

    expect(screen.getByText(/个错误/)).toBeInTheDocument();
    expect(screen.getByTestId('strategy-editor-save')).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });
});
