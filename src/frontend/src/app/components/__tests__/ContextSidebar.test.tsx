/**
 * ContextSidebar Component Tests
 *
 * 测试上下文侧边栏组件的核心功能
 */

import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { ContextSidebar } from '../ContextSidebar';

describe('ContextSidebar', () => {
  const baseProps = {
    dialogueEvents: [],
    live: false,
    dialogueLoading: false,
    onClearDialogueLogs: vi.fn(),
    clearingDialogueLogs: false,
    memoItems: [],
    memoSelected: null,
    memoContent: '',
    memoMtime: '',
    memoLoading: false,
    memoError: null,
    onSelectMemo: vi.fn(),
    memoryContent: '',
    memoryMtime: '',
    memoryLoading: false,
    memoryError: null,
    showCognition: false,
    setShowCognition: vi.fn(),
    settingsShowMemory: true,
    anthroState: null,
    snapshotTimestamp: null,
    snapshotFileStatus: null,
    snapshotFilePaths: null,
    snapshotDirectorState: null,
    resident: null,
  };

  describe('Tab Buttons', () => {
    it('should render all five tabs by default', () => {
      render(<ContextSidebar {...baseProps} />);

      // Check for all tab buttons by title
      expect(screen.getByRole('button', { name: /Discussion/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /备忘/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /忆库/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /快照/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /AGI/i })).toBeInTheDocument();
    });

    it('should hide memory tab when settingsShowMemory is false', () => {
      render(<ContextSidebar {...baseProps} settingsShowMemory={false} />);

      expect(screen.getByRole('button', { name: /Discussion/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /备忘/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /忆库/i })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /快照/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /AGI/i })).toBeInTheDocument();
    });

    it('should render dialogue tab button with correct title attribute', () => {
      render(<ContextSidebar {...baseProps} />);

      const dialogueButton = screen.getByRole('button', { name: /Discussion/i });
      expect(dialogueButton).toHaveAttribute('title', 'Discussion');
    });
  });

  describe('Initial Tab State', () => {
    it('should have dialogue tab as initially active', () => {
      render(<ContextSidebar {...baseProps} />);

      const dialogueButton = screen.getByRole('button', { name: /Discussion/i });
      expect(dialogueButton).toHaveClass('bg-white/10');
    });

    it('should display dialogue tab header text', () => {
      render(<ContextSidebar {...baseProps} />);

      expect(screen.getByRole('heading', { name: /对话流/i })).toBeInTheDocument();
    });

    it('passes dialogue log clear actions to the dialogue panel', () => {
      const onClearDialogueLogs = vi.fn();
      render(<ContextSidebar {...baseProps} onClearDialogueLogs={onClearDialogueLogs} />);

      fireEvent.click(screen.getByText('清空日志'));

      expect(onClearDialogueLogs).toHaveBeenCalledTimes(1);
    });

    it('should display connection status badge', () => {
      const { rerender } = render(<ContextSidebar {...baseProps} live={true} />);

      expect(screen.getByText(/Active/i)).toBeInTheDocument();

      rerender(<ContextSidebar {...baseProps} live={false} />);
      // Use getAllByText since offline appears in multiple places
      expect(screen.getAllByText(/离线/i)).toHaveLength(2);
    });

    it('shows runtime stream events when dialogue events are empty', () => {
      render(
        <ContextSidebar
          {...baseProps}
          live={true}
          runtimeEvents={[
            {
              id: 'bench-session-started',
              timestamp: '2026-06-19T02:02:29.000Z',
              level: 'success',
              source: 'bench',
              title: 'factory_bench.run.started',
              message: 'factory-bench session started',
              meta: { project_id: '-' },
            },
            {
              id: 'bench-gate-1',
              timestamp: '2026-06-19T02:02:30.000Z',
              level: 'error',
              source: 'bench',
              title: 'factory_bench.gate.evaluated',
              message: 'L1-01 gate:integration_qa_passed=FAIL',
              meta: { project_id: 'L1-01', stage: 'quality_gate' },
            },
          ]}
        />,
      );

      expect(screen.getAllByText(/L1-01/).length).toBeGreaterThan(0);
      expect(screen.getByText(/integration_qa_passed=FAIL/)).toBeInTheDocument();
      expect(screen.getByText(/总事件: 2/)).toBeInTheDocument();
      expect(screen.getByText(/任务数: 1/)).toBeInTheDocument();
      expect(screen.getByText(/成功率: 0%/)).toBeInTheDocument();
    });

    it('treats completed bench events with non-zero exit code as failures', () => {
      render(
        <ContextSidebar
          {...baseProps}
          live={true}
          runtimeEvents={[
            {
              id: 'bench-project-completed',
              timestamp: '2026-06-19T02:48:24.000Z',
              level: 'success',
              source: 'bench',
              title: 'factory_bench.project.completed',
              message: 'L2-07 exit=1 dur=329.6s',
              meta: { project_id: 'L2-07', exit_code: 1 },
            },
          ]}
        />,
      );

      expect(screen.getByText(/Event receipt: FAIL - factory_bench.project.completed/)).toBeInTheDocument();
      expect(screen.queryByText(/Result: FAIL - factory_bench.project.completed/)).not.toBeInTheDocument();
      expect(screen.getByText(/成功率: 0%/)).toBeInTheDocument();
    });
  });

  describe('AGI Tab Content', () => {
    it('should show default identity when resident is null', () => {
      render(<ContextSidebar {...baseProps} />);

      // AGI tab button exists
      const agiButton = screen.getByRole('button', { name: /AGI/i });
      expect(agiButton).toBeInTheDocument();
    });

    it('should display AGI tab button with correct title', () => {
      render(<ContextSidebar {...baseProps} />);

      const agiButton = screen.getByRole('button', { name: /AGI/i });
      expect(agiButton).toHaveAttribute('title', 'AGI');
    });
  });

  describe('Tab Icons', () => {
    it('should render dialogue tab button with icon', () => {
      render(<ContextSidebar {...baseProps} />);

      // Dialogue button should have an SVG icon
      const dialogueButton = screen.getByRole('button', { name: /Discussion/i });
      expect(dialogueButton.querySelector('svg')).toBeInTheDocument();
    });

    it('should render tab buttons', () => {
      render(<ContextSidebar {...baseProps} />);

      // Should have at least 5 tab buttons
      const buttons = screen.getAllByRole('button');
      expect(buttons.length).toBeGreaterThanOrEqual(5);
    });
  });

  describe('Snapshot Tab', () => {
    it('should render snapshot tab button with correct title', () => {
      render(<ContextSidebar {...baseProps} />);

      const snapshotButton = screen.getByRole('button', { name: /快照/i });
      expect(snapshotButton).toHaveAttribute('title', '快照');
    });

    it('keeps long snapshot metadata contained', () => {
      render(
        <ContextSidebar
          {...baseProps}
          activeTab="snapshot"
          snapshotTimestamp="2026-05-30T12:00:00.000Z-long-runtime-token-without-natural-breaks"
          snapshotFileStatus={[
            'C:/Users/dains/Documents/GitLab/polaris/runtime/very-long-file-status-without-natural-breaks.md',
          ]}
          snapshotFilePaths={['a.md']}
          snapshotDirectorState={{
            phase: 'chief-engineer-blueprint-phase-with-a-very-long-token-that-must-wrap',
            iteration: 7,
          }}
        />,
      );

      expect(screen.getByTestId('snapshot-panel')).toHaveClass('overflow-hidden');
      expect(screen.getByText(/时刻:/)).toHaveClass('break-all');
      expect(screen.getByTestId('snapshot-panel-file-line')).toHaveClass('break-all');
    });
  });

  describe('Memo Tab', () => {
    it('should render memo tab button with correct title', () => {
      render(<ContextSidebar {...baseProps} />);

      const memoButton = screen.getByRole('button', { name: /备忘/i });
      expect(memoButton).toHaveAttribute('title', '备忘');
    });

    it('keeps long memo content inside the right-side panel', () => {
      render(
        <ContextSidebar
          {...baseProps}
          activeTab="memos"
          memoItems={[
            {
              name: 'runtime-contract-with-long-name.md',
              path: 'runtime/contracts/runtime-contract-with-long-name.md',
              mtime: '2026-05-30T12:00:00Z',
            },
          ]}
          memoSelected={{
            name: 'runtime-contract-with-long-name.md',
            path: 'runtime/contracts/runtime-contract-with-long-name.md',
            mtime: '2026-05-30T12:00:00Z',
          }}
          memoContent={'C:/Users/dains/Documents/GitLab/polaris/'.repeat(20)}
        />,
      );

      expect(screen.getByTestId('memo-panel')).toHaveClass('overflow-hidden');
      expect(screen.getByTestId('memo-panel-body')).toHaveClass('min-w-0');
      expect(screen.getByTestId('memo-panel-content')).toHaveClass('break-words');
    });
  });

  describe('Memory Tab', () => {
    it('should render memory tab button with correct title when settingsShowMemory is true', () => {
      render(<ContextSidebar {...baseProps} settingsShowMemory={true} />);

      const memoryButton = screen.getByRole('button', { name: /忆库/i });
      expect(memoryButton).toHaveAttribute('title', '忆库');
    });

    it('can be controlled by the parent to open the memory tab', () => {
      const onActiveTabChange = vi.fn();
      render(
        <ContextSidebar
          {...baseProps}
          activeTab="memory"
          onActiveTabChange={onActiveTabChange}
          settingsShowMemory={true}
        />,
      );

      expect(screen.getByText('记忆')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /快照/i }));

      expect(onActiveTabChange).toHaveBeenCalledWith('snapshot');
    });

    it('routes cognition mode changes through the provided state setter', () => {
      const setShowCognition = vi.fn();
      render(
        <ContextSidebar
          {...baseProps}
          activeTab="memory"
          showCognition={false}
          setShowCognition={setShowCognition}
          settingsShowMemory={true}
        />,
      );

      fireEvent.click(screen.getByText('认知'));

      expect(setShowCognition).toHaveBeenCalledWith(true);
    });
  });
});
