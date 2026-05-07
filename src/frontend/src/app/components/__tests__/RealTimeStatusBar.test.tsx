/**
 * RealTimeStatusBar Component Tests
 *
 * 测试实时状态栏组件的核心功能：
 * - PM/Director 状态显示
 * - 运行时长格式化
 * - 轮次显示
 * - LLM/数据库状态指示器
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';

// Mock framer-motion to avoid IntersectionObserver issues in tests
vi.mock('framer-motion', () => ({
  motion: {
    div: ({ children, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
      <div {...props}>{children}</div>
    ),
  },
  AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// Mock AnimateCountUp component
vi.mock('@/app/components/ui/animate-count-up', () => ({
  AnimateCountUp: ({ to, prefix = '', padStart = 0 }: { to: number; prefix?: string; padStart?: number }) => (
    <span data-testid="count-up" data-value={to}>
      {prefix}{String(to).padStart(padStart, '0')}
    </span>
  ),
}));

// Mock AnimateBorder component
vi.mock('@/app/components/ui/animate-border', () => ({
  AnimateBorder: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { RealTimeStatusBar } from '../RealTimeStatusBar';

describe('RealTimeStatusBar', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-16T10:00:00.000Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('Initial Render', () => {
    it('should render with idle states when nothing is running', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      // Should have idle states for both PM and Director
      expect(screen.getAllByText(/Idle/i)).toHaveLength(4);
      // Should have idle states for both PM and Director
      expect(screen.getAllByText(/PM/i)).toHaveLength(2);
      expect(screen.getAllByText(/Director/i)).toHaveLength(2);
    });

    it('should show active PM status with duration when running', () => {
      const startedAt = Math.floor(Date.now() / 1000) - 120; // 2 minutes ago
      render(
        <RealTimeStatusBar
          pmRunning={true}
          directorRunning={false}
          pmStartedAt={startedAt}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      // PM label with active status
      expect(screen.getByText(/PM/i)).toBeInTheDocument();
      expect(screen.getByText(/Active/i)).toBeInTheDocument();
    });

    it('should show active Director status when running', () => {
      const startedAt = Math.floor(Date.now() / 1000) - 60; // 1 minute ago
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={true}
          pmStartedAt={null}
          directorStartedAt={startedAt}
          pmIteration={null}
        />
      );

      // Director label with active status
      expect(screen.getByText(/Director/i)).toBeInTheDocument();
      expect(screen.getByText(/Active/i)).toBeInTheDocument();
    });

    it('should display iteration count when provided', () => {
      render(
        <RealTimeStatusBar
          pmRunning={true}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={42}
        />
      );

      expect(screen.getByText('#042')).toBeInTheDocument();
    });

    it('should not display iteration when null', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      // Iteration badge should not be visible
      expect(screen.queryByText(/^#\d+$/)).not.toBeInTheDocument();
    });
  });

  describe('Duration Formatting', () => {
    it('should format seconds correctly', () => {
      const startedAt = Math.floor(Date.now() / 1000) - 45; // 45 seconds ago
      render(
        <RealTimeStatusBar
          pmRunning={true}
          directorRunning={false}
          pmStartedAt={startedAt}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      expect(screen.getByText(/45s/i)).toBeInTheDocument();
    });

    it('should format minutes correctly', () => {
      const startedAt = Math.floor(Date.now() / 1000) - 300; // 5 minutes ago
      render(
        <RealTimeStatusBar
          pmRunning={true}
          directorRunning={false}
          pmStartedAt={startedAt}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      expect(screen.getByText(/5m/i)).toBeInTheDocument();
    });

    it('should format hours correctly', () => {
      const startedAt = Math.floor(Date.now() / 1000) - 7200; // 2 hours ago
      render(
        <RealTimeStatusBar
          pmRunning={true}
          directorRunning={false}
          pmStartedAt={startedAt}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      expect(screen.getByText(/2h/i)).toBeInTheDocument();
    });

    it('should handle null startedAt', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      // Should render without errors
      expect(screen.getAllByText(/PM/i)).toHaveLength(2);
    });

    it('should handle future timestamps gracefully', () => {
      const futureStartedAt = Math.floor(Date.now() / 1000) + 100;
      render(
        <RealTimeStatusBar
          pmRunning={true}
          directorRunning={false}
          pmStartedAt={futureStartedAt}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      // Should display 0s for negative duration
      expect(screen.getByText(/0s/i)).toBeInTheDocument();
    });

    it('should not show huge durations for accidental 1970 timestamps', () => {
      render(
        <RealTimeStatusBar
          pmRunning={true}
          directorRunning={false}
          pmStartedAt={1771594}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      expect(screen.getByText('Active')).toBeInTheDocument();
      expect(screen.queryByText(/493/)).not.toBeInTheDocument();
    });
  });

  describe('LLM Status Display', () => {
    it('should display LLM ready status', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
          llmStatus="ready"
        />
      );

      expect(screen.getByText(/LLM/i)).toBeInTheDocument();
      expect(screen.getByText(/就绪/i)).toBeInTheDocument();
    });

    it('should display LLM blocked status', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
          llmStatus="blocked"
        />
      );

      expect(screen.getByText(/LLM/i)).toBeInTheDocument();
      expect(screen.getByText(/阻塞/i)).toBeInTheDocument();
    });

    it('should display LLM unknown status', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
          llmStatus="unknown"
        />
      );

      expect(screen.getByText(/LLM/i)).toBeInTheDocument();
      expect(screen.getByText(/未判/i)).toBeInTheDocument();
    });

    it('should not display LLM status when not provided', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      // LLM indicator should not be visible
      expect(screen.queryByText(/LLM/i)).not.toBeInTheDocument();
    });
  });

  describe('Database Status Display', () => {
    it('should display database ready status', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
          lancedbOk={true}
        />
      );

      expect(screen.getByText(/经籍库/i)).toBeInTheDocument();
      expect(screen.getByText(/就绪/i)).toBeInTheDocument();
    });

    it('should display database offline status', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
          lancedbOk={false}
        />
      );

      expect(screen.getByText(/经籍库/i)).toBeInTheDocument();
      expect(screen.getByText(/离线/i)).toBeInTheDocument();
    });

    it('should not display database status when undefined', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      expect(screen.queryByText(/经籍库/i)).not.toBeInTheDocument();
    });
  });

  describe('File Edit Status Display', () => {
    it('should display latest runtime file edit evidence', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={true}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
          fileEditEvents={[
            {
              id: 'older',
              filePath: 'src/old.ts',
              operation: 'create',
              contentSize: 12,
              timestamp: '2026-04-16T09:59:00.000Z',
            },
            {
              id: 'newer',
              filePath: 'src/new.ts',
              operation: 'modify',
              contentSize: 42,
              timestamp: '2026-04-16T10:00:00.000Z',
            },
          ]}
        />
      );

      expect(screen.getByTestId('runtime-file-edit-status')).toHaveTextContent('文件变更');
      expect(screen.getByTestId('runtime-file-edit-status')).toHaveTextContent('modify src/new.ts');
      expect(screen.queryByText(/src\/old.ts/)).not.toBeInTheDocument();
    });
  });

  describe('Time Display', () => {
    it('should display current time', () => {
      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      expect(screen.getByText(/漏刻时辰/i)).toBeInTheDocument();
    });

    it('should update time every second', () => {
      const { rerender } = render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      act(() => {
        vi.advanceTimersByTime(1000);
      });

      // Rerender to reflect timer update
      rerender(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      // Component should still render without errors
      expect(screen.getAllByText(/PM/i)).toHaveLength(2);
    });
  });

  describe('Combined Status Display', () => {
    it('should display all statuses when all props provided', () => {
      const pmStartedAt = Math.floor(Date.now() / 1000) - 180;
      const directorStartedAt = Math.floor(Date.now() / 1000) - 60;

      render(
        <RealTimeStatusBar
          pmRunning={true}
          directorRunning={true}
          pmStartedAt={pmStartedAt}
          directorStartedAt={directorStartedAt}
          pmIteration={5}
          llmStatus="ready"
          lancedbOk={true}
        />
      );

      // PM active
      expect(screen.getAllByText(/PM/i)).toHaveLength(1);
      // Director active (Director when running)
      expect(screen.getByText(/Director/i)).toBeInTheDocument();
      // Iteration
      expect(screen.getByText('#005')).toBeInTheDocument();
      // LLM ready
      expect(screen.getByText(/LLM/i)).toBeInTheDocument();
      // Database ready
      expect(screen.getByText(/经籍库/i)).toBeInTheDocument();
    });

    it('should handle mixed running states', () => {
      const directorStartedAt = Math.floor(Date.now() / 1000) - 30;

      render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={true}
          pmStartedAt={null}
          directorStartedAt={directorStartedAt}
          pmIteration={null}
          llmStatus="ready"
        />
      );

      // PM should have at least one idle state
      expect(screen.getAllByText(/PM/i)).toHaveLength(2);
      // Director active
      expect(screen.getByText(/Director/i)).toBeInTheDocument();
      expect(screen.getByText(/Active/i)).toBeInTheDocument();
    });
  });

  describe('Cleanup', () => {
    it('should clean up timer on unmount', () => {
      const clearIntervalSpy = vi.spyOn(global, 'clearInterval');
      const { unmount } = render(
        <RealTimeStatusBar
          pmRunning={false}
          directorRunning={false}
          pmStartedAt={null}
          directorStartedAt={null}
          pmIteration={null}
        />
      );

      unmount();

      expect(clearIntervalSpy).toHaveBeenCalled();
    });
  });
});
