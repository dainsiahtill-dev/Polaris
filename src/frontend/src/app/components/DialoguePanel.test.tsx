import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DialoguePanel, type DialogueEvent } from './DialoguePanel';

const createMockEvents = (): DialogueEvent[] => [
  {
    seq: 1,
    eventId: 'event-1',
    speaker: 'PM',
    type: 'log',
    content: 'Assigning task TASK-001: Implement login feature',
    timestamp: '2024-01-15T10:00:00Z',
    refs: { task_id: 'TASK-001', phase: 'planning' },
  },
  {
    seq: 2,
    eventId: 'event-2',
    speaker: 'Director',
    type: 'log',
    content: 'Executing: create file src/login.ts',
    timestamp: '2024-01-15T10:01:00Z',
    refs: { task_id: 'TASK-001', phase: 'implementation' },
  },
  {
    seq: 3,
    eventId: 'event-3',
    speaker: 'QA',
    type: 'log',
    content: 'Reviewer: Found potential security issue',
    timestamp: '2024-01-15T10:02:00Z',
    refs: { task_id: 'TASK-001', phase: 'review' },
  },
  {
    seq: 4,
    eventId: 'event-4',
    speaker: 'System',
    type: 'result',
    content: 'Task TASK-001 completed with SUCCESS',
    timestamp: '2024-01-15T10:03:00Z',
    refs: { task_id: 'TASK-001' },
  },
];

describe('DialoguePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Basic Rendering', () => {
    it('renders the dialogue panel container', () => {
      render(<DialoguePanel events={[]} live={false} />);
      expect(screen.getByText('对话流')).toBeInTheDocument();
    });

    it('shows live indicator when live is true', () => {
      render(<DialoguePanel events={[]} live={true} />);
      expect(screen.getByText('实时')).toBeInTheDocument();
    });

    it('shows offline indicator when live is false', () => {
      render(<DialoguePanel events={[]} live={false} />);
      expect(screen.getByText('离线')).toBeInTheDocument();
    });

    it('shows empty state when no events and not loading', () => {
      render(<DialoguePanel events={[]} live={false} loading={false} />);
      expect(screen.getByText('(暂无任务)')).toBeInTheDocument();
    });
  });

  describe('View Mode Buttons', () => {
    it('shows task and stream view buttons', () => {
      const events = createMockEvents();
      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText('任务视图')).toBeInTheDocument();
      expect(screen.getByText('日志流')).toBeInTheDocument();
    });
  });

  describe('Task Groups', () => {
    it('groups events by task_id', () => {
      const events = createMockEvents();
      render(<DialoguePanel events={events} live={false} />);
      // Should show task ID
      expect(screen.getByText('TASK-001')).toBeInTheDocument();
    });

    it('shows reviewer findings when present', () => {
      const events = createMockEvents();
      render(<DialoguePanel events={events} live={false} />);
      // Should show Reviewer section
      expect(screen.getByText('Reviewer 风险点')).toBeInTheDocument();
    });
  });

  describe('Statistics Footer', () => {
    it('shows total events count', () => {
      const events = createMockEvents();
      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText(/总事件: 4/)).toBeInTheDocument();
    });

    it('shows task count', () => {
      const events = createMockEvents();
      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText(/任务数: 1/)).toBeInTheDocument();
    });
  });

  describe('Clear Logs', () => {
    it('shows clear logs button when onClearLogs is provided', () => {
      const onClearLogs = vi.fn();
      render(<DialoguePanel events={[]} live={false} onClearLogs={onClearLogs} />);
      expect(screen.getByText('清空日志')).toBeInTheDocument();
    });

    it('does not show clear logs button when onClearLogs is not provided', () => {
      render(<DialoguePanel events={[]} live={false} />);
      expect(screen.queryByText('清空日志')).not.toBeInTheDocument();
    });

    it('calls onClearLogs when button clicked', () => {
      const onClearLogs = vi.fn();
      render(<DialoguePanel events={[]} live={false} onClearLogs={onClearLogs} />);
      fireEvent.click(screen.getByText('清空日志'));
      expect(onClearLogs).toHaveBeenCalledTimes(1);
    });

    it('shows clearing state when clearingLogs is true', () => {
      const onClearLogs = vi.fn();
      render(<DialoguePanel events={[]} live={false} onClearLogs={onClearLogs} clearingLogs={true} />);
      expect(screen.getByText('清空中')).toBeInTheDocument();
    });

    it('disables clear button when clearingLogs is true', () => {
      const onClearLogs = vi.fn();
      render(<DialoguePanel events={[]} live={false} onClearLogs={onClearLogs} clearingLogs={true} />);
      const button = screen.getByText('清空中').closest('button');
      expect(button).toBeDisabled();
    });
  });

  describe('Empty Stream View', () => {
    it('shows empty state in stream view when no events', () => {
      render(<DialoguePanel events={[]} live={false} />);
      fireEvent.click(screen.getByText('日志流'));
      expect(screen.getByText('(暂无对话事件)')).toBeInTheDocument();
    });
  });

  describe('Multiple Tasks', () => {
    it('handles multiple task groups', () => {
      const events: DialogueEvent[] = [
        {
          seq: 1,
          eventId: 'event-1',
          speaker: 'PM',
          content: 'Assigning task TASK-001: First task',
          timestamp: '2024-01-15T10:00:00Z',
          refs: { task_id: 'TASK-001' },
        },
        {
          seq: 2,
          eventId: 'event-2',
          speaker: 'PM',
          content: 'Assigning task TASK-002: Second task',
          timestamp: '2024-01-15T10:01:00Z',
          refs: { task_id: 'TASK-002' },
        },
        {
          seq: 3,
          eventId: 'event-3',
          speaker: 'System',
          content: 'Task TASK-001 completed with SUCCESS',
          timestamp: '2024-01-15T10:02:00Z',
          refs: { task_id: 'TASK-001' },
        },
        {
          seq: 4,
          eventId: 'event-4',
          speaker: 'System',
          content: 'Task TASK-002 completed with FAIL',
          timestamp: '2024-01-15T10:03:00Z',
          refs: { task_id: 'TASK-002' },
        },
      ];

      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText('TASK-001')).toBeInTheDocument();
      expect(screen.getByText('TASK-002')).toBeInTheDocument();
      expect(screen.getByText(/任务数: 2/)).toBeInTheDocument();
    });
  });

  describe('Speaker Styles', () => {
    it('renders PM speaker message', () => {
      const events = [{
        seq: 1,
        speaker: 'PM' as const,
        content: 'PM message',
        timestamp: '2024-01-15T10:00:00Z',
      }];

      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText('PM message')).toBeInTheDocument();
    });

    it('renders Director speaker message', () => {
      const events = [{
        seq: 1,
        speaker: 'Director' as const,
        content: 'Director message',
        timestamp: '2024-01-15T10:00:00Z',
      }];

      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText('Director message')).toBeInTheDocument();
    });

    it('renders QA speaker message', () => {
      const events = [{
        seq: 1,
        speaker: 'QA' as const,
        content: 'QA message',
        timestamp: '2024-01-15T10:00:00Z',
      }];

      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText('QA message')).toBeInTheDocument();
    });

    it('renders System speaker message', () => {
      const events = [{
        seq: 1,
        speaker: 'System' as const,
        content: 'System message',
        timestamp: '2024-01-15T10:00:00Z',
      }];

      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText('System message')).toBeInTheDocument();
    });
  });

  describe('Conflict Detection', () => {
    it('shows conflict badge when success task has reviewer findings', () => {
      const events: DialogueEvent[] = [
        {
          seq: 1,
          speaker: 'PM' as const,
          content: 'Assigning task TASK-001: Feature',
          timestamp: '2024-01-15T10:00:00Z',
          refs: { task_id: 'TASK-001' },
        },
        {
          seq: 2,
          speaker: 'QA' as const,
          content: 'Reviewer: Minor issue found',
          timestamp: '2024-01-15T10:01:00Z',
          refs: { task_id: 'TASK-001' },
        },
        {
          seq: 3,
          speaker: 'System' as const,
          content: 'Task TASK-001 completed with SUCCESS',
          timestamp: '2024-01-15T10:02:00Z',
          refs: { task_id: 'TASK-001' },
        },
      ];

      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText('结论冲突')).toBeInTheDocument();
    });
  });

  describe('Attempt Tracking', () => {
    it('shows attempt information when present', () => {
      const events: DialogueEvent[] = [
        {
          seq: 1,
          speaker: 'System' as const,
          content: 'Task TASK-001: attempt 2 / 3',
          timestamp: '2024-01-15T10:00:00Z',
          refs: { task_id: 'TASK-001' },
        },
      ];

      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText('attempt 2/3')).toBeInTheDocument();
    });
  });

  describe('GLOBAL Tasks', () => {
    it('shows "系统/未归类" for GLOBAL task group', () => {
      const events: DialogueEvent[] = [
        {
          seq: 1,
          speaker: 'System' as const,
          content: 'Global system message',
          timestamp: '2024-01-15T10:00:00Z',
        },
      ];

      render(<DialoguePanel events={events} live={false} />);
      expect(screen.getByText('系统/未归类')).toBeInTheDocument();
    });
  });
});
