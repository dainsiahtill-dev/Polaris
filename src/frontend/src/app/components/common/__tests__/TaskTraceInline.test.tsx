import { render, screen } from '@testing-library/react';
import { TaskTraceInline } from '../TaskTraceInline';
import { TaskTraceEvent } from '../../../types/taskTrace';

describe('TaskTraceInline', () => {
  const mockTraces: TaskTraceEvent[] = [
    {
      event_id: '1',
      run_id: 'run-1',
      role: 'director',
      task_id: 'task-1',
      seq: 1,
      phase: 'planning',
      step_kind: 'phase',
      step_title: 'Planning started',
      step_detail: 'Starting planning',
      status: 'running',
      attempt: 0,
      visibility: 'summary',
      ts: new Date().toISOString(),
      refs: {},
    },
  ];

  it('renders latest trace', () => {
    render(<TaskTraceInline traces={mockTraces} />);
    expect(screen.getByText('Planning started')).toBeInTheDocument();
  });

  it('returns null for empty traces', () => {
    const { container } = render(<TaskTraceInline traces={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders status dot with correct color for running status', () => {
    render(<TaskTraceInline traces={mockTraces} />);
    const dot = document.querySelector('.rounded-full');
    expect(dot).toBeInTheDocument();
    expect(dot).toHaveClass('bg-blue-400');
  });

  it('renders status dot with correct color for completed status', () => {
    const completedTraces: TaskTraceEvent[] = [
      {
        ...mockTraces[0],
        status: 'completed',
      },
    ];
    render(<TaskTraceInline traces={completedTraces} />);
    const dot = document.querySelector('.rounded-full');
    expect(dot).toHaveClass('bg-green-400');
  });

  it('renders status dot with correct color for failed status', () => {
    const failedTraces: TaskTraceEvent[] = [
      {
        ...mockTraces[0],
        status: 'failed',
      },
    ];
    render(<TaskTraceInline traces={failedTraces} />);
    const dot = document.querySelector('.rounded-full');
    expect(dot).toHaveClass('bg-red-400');
  });

  it('renders status dot with correct color for started status', () => {
    const startedTraces: TaskTraceEvent[] = [
      {
        ...mockTraces[0],
        status: 'started',
      },
    ];
    render(<TaskTraceInline traces={startedTraces} />);
    const dot = document.querySelector('.rounded-full');
    expect(dot).toHaveClass('bg-yellow-400');
  });

  it('renders status dot with correct color for retry status', () => {
    const retryTraces: TaskTraceEvent[] = [
      {
        ...mockTraces[0],
        status: 'retry' as any,
      },
    ];
    render(<TaskTraceInline traces={retryTraces} />);
    const dot = document.querySelector('.rounded-full');
    expect(dot).toHaveClass('bg-orange-400');
  });

  it('renders timestamp when available', () => {
    const testDate = new Date('2024-01-15T10:30:00.000Z');
    const tracesWithTimestamp: TaskTraceEvent[] = [
      {
        ...mockTraces[0],
        ts: testDate.toISOString(),
      },
    ];
    render(<TaskTraceInline traces={tracesWithTimestamp} />);
    // Timestamp should be rendered in a span with text-gray-500 class
    // Use flexible matcher since time format depends on locale
    const timestamp = screen.getByText(/\d{1,2}:\d{2}:\d{2}/);
    expect(timestamp).toBeInTheDocument();
    expect(timestamp).toHaveClass('text-gray-500');
  });

  it('renders step detail when maxLines > 1', () => {
    render(<TaskTraceInline traces={mockTraces} maxLines={2} />);
    expect(screen.getByText('Starting planning')).toBeInTheDocument();
  });

  it('does not render step detail when maxLines = 1', () => {
    render(<TaskTraceInline traces={mockTraces} maxLines={1} />);
    expect(screen.queryByText('Starting planning')).not.toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(
      <TaskTraceInline traces={mockTraces} className="custom-class" />
    );
    expect(container.firstChild).toHaveClass('custom-class');
  });

  it('renders latest trace from multiple traces', () => {
    const multipleTraces: TaskTraceEvent[] = [
      mockTraces[0],
      {
        ...mockTraces[0],
        event_id: '2',
        seq: 2,
        step_title: 'Executing task',
        step_detail: 'Running tool execution',
        status: 'running',
      },
    ];
    render(<TaskTraceInline traces={multipleTraces} />);
    expect(screen.getByText('Executing task')).toBeInTheDocument();
    expect(screen.queryByText('Planning started')).not.toBeInTheDocument();
  });

  it('renders correctly with different roles', () => {
    const roles: TaskTraceEvent['role'][] = ['pm', 'director', 'qa', 'architect', 'chief_engineer'];
    roles.forEach((role) => {
      const roleTraces: TaskTraceEvent[] = [
        {
          ...mockTraces[0],
          role,
        },
      ];
      const { container } = render(<TaskTraceInline traces={roleTraces} />);
      expect(container.firstChild).not.toBeNull();
    });
  });

  it('renders correctly with different step kinds', () => {
    const kinds: TaskTraceEvent['step_kind'][] = ['phase', 'llm', 'tool', 'validation', 'retry', 'system'];
    kinds.forEach((step_kind) => {
      const kindTraces: TaskTraceEvent[] = [
        {
          ...mockTraces[0],
          step_kind,
          step_title: `${step_kind} step`,
        },
      ];
      const { container } = render(<TaskTraceInline traces={kindTraces} />);
      expect(container.firstChild).not.toBeNull();
      expect(screen.getByText(`${step_kind} step`)).toBeInTheDocument();
    });
  });
});
