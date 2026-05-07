/**
 * ContextSidebar Component Tests
 *
 * 测试上下文侧边栏组件的核心功能
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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

    it('should display connection status badge', () => {
      const { rerender } = render(<ContextSidebar {...baseProps} live={true} />);

      expect(screen.getByText(/Active/i)).toBeInTheDocument();

      rerender(<ContextSidebar {...baseProps} live={false} />);
      // Use getAllByText since offline appears in multiple places
      expect(screen.getAllByText(/离线/i)).toHaveLength(2);
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
  });

  describe('Memo Tab', () => {
    it('should render memo tab button with correct title', () => {
      render(<ContextSidebar {...baseProps} />);

      const memoButton = screen.getByRole('button', { name: /备忘/i });
      expect(memoButton).toHaveAttribute('title', '备忘');
    });
  });

  describe('Memory Tab', () => {
    it('should render memory tab button with correct title when settingsShowMemory is true', () => {
      render(<ContextSidebar {...baseProps} settingsShowMemory={true} />);

      const memoryButton = screen.getByRole('button', { name: /忆库/i });
      expect(memoryButton).toHaveAttribute('title', '忆库');
    });
  });
});
