import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ControlPanel } from './ControlPanel';

// Mock the DropdownMenu components
vi.mock('./ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dropdown-menu">{children}</div>
  ),
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dropdown-menu-content">{children}</div>
  ),
  DropdownMenuItem: ({
    children,
    onClick,
    disabled
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
  }) => (
    <button onClick={onClick} disabled={disabled} data-testid="dropdown-menu-item">
      {children}
    </button>
  ),
  DropdownMenuSeparator: () => <div data-testid="dropdown-menu-separator" />,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dropdown-menu-trigger">{children}</div>
  ),
}));

// Mock UsageHUD component
vi.mock('./UsageHUD', () => ({
  UsageHUD: () => <div data-testid="usage-hud">UsageHUD</div>,
}));

const defaultProps = {
  workspace: '/test/workspace',
  pmRunning: false,
  directorRunning: false,
  onOpenSettings: vi.fn(),
  onTogglePm: vi.fn(),
  onToggleDirector: vi.fn(),
  onRefresh: vi.fn(),
  isArtifactsOpen: false,
  onToggleArtifacts: vi.fn(),
};

describe('ControlPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Basic Rendering', () => {
    it('renders the control panel with title', () => {
      render(<ControlPanel {...defaultProps} />);
      expect(screen.getByText('Polaris')).toBeInTheDocument();
    });

    it('displays the workspace path', () => {
      render(<ControlPanel {...defaultProps} />);
      expect(screen.getByDisplayValue('/test/workspace')).toBeInTheDocument();
    });
  });

  describe('PM Toggle', () => {
    it('renders PM toggle button', () => {
      render(<ControlPanel {...defaultProps} />);
      expect(screen.getByTestId('control-panel-pm-toggle')).toBeInTheDocument();
    });

    it('calls onTogglePm when PM button clicked', () => {
      render(<ControlPanel {...defaultProps} />);
      fireEvent.click(screen.getByTestId('control-panel-pm-toggle'));
      expect(defaultProps.onTogglePm).toHaveBeenCalledTimes(1);
    });

    it('disables PM toggle when pmToggleDisabled is true', () => {
      render(<ControlPanel {...defaultProps} pmToggleDisabled={true} />);
      expect(screen.getByTestId('control-panel-pm-toggle')).toBeDisabled();
    });

    it('disables PM toggle when isStartingPM is true', () => {
      render(<ControlPanel {...defaultProps} isStartingPM={true} />);
      expect(screen.getByTestId('control-panel-pm-toggle')).toBeDisabled();
    });

    it('disables PM toggle when isStoppingPM is true', () => {
      render(<ControlPanel {...defaultProps} isStoppingPM={true} />);
      expect(screen.getByTestId('control-panel-pm-toggle')).toBeDisabled();
    });
  });

  describe('Director Toggle', () => {
    it('renders Director toggle button', () => {
      render(<ControlPanel {...defaultProps} />);
      expect(screen.getByTestId('control-panel-director-toggle')).toBeInTheDocument();
    });

    it('calls onToggleDirector when Director button clicked', () => {
      render(<ControlPanel {...defaultProps} />);
      fireEvent.click(screen.getByTestId('control-panel-director-toggle'));
      expect(defaultProps.onToggleDirector).toHaveBeenCalledTimes(1);
    });

    it('disables Director toggle when directorToggleDisabled is true', () => {
      render(<ControlPanel {...defaultProps} directorToggleDisabled={true} />);
      expect(screen.getByTestId('control-panel-director-toggle')).toBeDisabled();
    });

    it('disables Director toggle when isStartingDirector is true', () => {
      render(<ControlPanel {...defaultProps} isStartingDirector={true} />);
      expect(screen.getByTestId('control-panel-director-toggle')).toBeDisabled();
    });

    it('disables Director toggle when isStoppingDirector is true', () => {
      render(<ControlPanel {...defaultProps} isStoppingDirector={true} />);
      expect(screen.getByTestId('control-panel-director-toggle')).toBeDisabled();
    });

    it('shows blocked reason when directorBlockedReason is provided', () => {
      render(<ControlPanel {...defaultProps} directorBlockedReason="缺少配置" />);
      expect(screen.getByText('缺少配置')).toBeInTheDocument();
    });
  });

  describe('Workspace Error', () => {
    it('shows workspace error when provided', () => {
      render(<ControlPanel {...defaultProps} workspaceError="Invalid workspace path" />);
      expect(screen.getByText('Invalid workspace path')).toBeInTheDocument();
    });
  });

  describe('Logs Button', () => {
    it('calls onOpenLogs when clicked', () => {
      const onOpenLogs = vi.fn();
      render(<ControlPanel {...defaultProps} onOpenLogs={onOpenLogs} />);
      // Find the logs button and click it
      const logsButton = document.querySelector('[title="查看子进程与回执日志"]');
      if (logsButton) {
        fireEvent.click(logsButton);
        expect(onOpenLogs).toHaveBeenCalledTimes(1);
      }
    });
  });

  describe('PM Run Once', () => {
    it('shows run once button when onRunPmOnce is provided', () => {
      render(<ControlPanel {...defaultProps} onRunPmOnce={vi.fn()} />);
      expect(screen.getByTestId('control-panel-pm-run-once')).toBeInTheDocument();
    });

    it('disables run once when runOnceDisabled is true', () => {
      render(<ControlPanel {...defaultProps} onRunPmOnce={vi.fn()} runOnceDisabled={true} />);
      expect(screen.getByTestId('control-panel-pm-run-once')).toBeDisabled();
    });
  });

  describe('Factory Mode', () => {
    it('shows factory mode button when onEnterFactoryMode is provided', () => {
      render(<ControlPanel {...defaultProps} onEnterFactoryMode={vi.fn()} />);
      expect(screen.getByTitle('Factory 模式 - 无人值守开发工厂')).toBeInTheDocument();
    });

    it('calls onEnterFactoryMode when clicked', () => {
      const onEnterFactoryMode = vi.fn();
      render(<ControlPanel {...defaultProps} onEnterFactoryMode={onEnterFactoryMode} />);
      fireEvent.click(screen.getByTitle('Factory 模式 - 无人值守开发工厂'));
      expect(onEnterFactoryMode).toHaveBeenCalledTimes(1);
    });
  });

  describe('Chief Engineer Workspace', () => {
    it('shows chief engineer workspace entry when provided', () => {
      render(<ControlPanel {...defaultProps} onEnterChiefEngineerWorkspace={vi.fn()} />);
      expect(screen.getByText('Chief Engineer 工作区')).toBeInTheDocument();
    });

    it('calls onEnterChiefEngineerWorkspace from the more menu entry', () => {
      const onEnterChiefEngineerWorkspace = vi.fn();
      render(<ControlPanel {...defaultProps} onEnterChiefEngineerWorkspace={onEnterChiefEngineerWorkspace} />);
      fireEvent.click(screen.getByText('Chief Engineer 工作区'));
      expect(onEnterChiefEngineerWorkspace).toHaveBeenCalledTimes(1);
    });
  });

  describe('Ollama Stop', () => {
    it('shows Ollama section when onStopOllama is provided', () => {
      render(<ControlPanel {...defaultProps} onStopOllama={vi.fn()} />);
      expect(screen.getByText('Ollama')).toBeInTheDocument();
    });
  });

  describe('Terminal Toggle', () => {
    it('shows terminal toggle button', () => {
      render(<ControlPanel {...defaultProps} />);
      expect(screen.getByTitle('Terminal (Ctrl + `)')).toBeInTheDocument();
    });

    it('calls onToggleTerminal when clicked', () => {
      const onToggleTerminal = vi.fn();
      render(<ControlPanel {...defaultProps} onToggleTerminal={onToggleTerminal} />);
      fireEvent.click(screen.getByTitle('Terminal (Ctrl + `)'));
      expect(onToggleTerminal).toHaveBeenCalledTimes(1);
    });
  });

  describe('IO/Memory Modes', () => {
    it('shows IO mode badge when ioFsyncMode is provided', () => {
      render(<ControlPanel {...defaultProps} ioFsyncMode="relaxed" />);
      expect(screen.getByText('IO:RELAXED')).toBeInTheDocument();
    });

    it('shows MEM mode badge when memoryRefsMode is provided', () => {
      render(<ControlPanel {...defaultProps} memoryRefsMode="soft" />);
      expect(screen.getByText('MEM:SOFT')).toBeInTheDocument();
    });

    it('shows STRICT IO mode badge by default', () => {
      render(<ControlPanel {...defaultProps} ioFsyncMode="strict" />);
      expect(screen.getByText('IO:STRICT')).toBeInTheDocument();
    });

    it('shows OFF MEM mode badge', () => {
      render(<ControlPanel {...defaultProps} memoryRefsMode="off" />);
      expect(screen.getByText('MEM:OFF')).toBeInTheDocument();
    });
  });

  describe('Current Task Display', () => {
    it('shows current task when PM is running', () => {
      render(
        <ControlPanel
          {...defaultProps}
          pmRunning={true}
          currentTask="Implementing login"
        />
      );
      expect(screen.getByText('Implementing login')).toBeInTheDocument();
    });

    it('filters structured runtime fragments from compact task labels', () => {
      render(
        <ControlPanel
          {...defaultProps}
          pmRunning={true}
          currentTask="}"
          isExecutingTool={true}
          currentToolName='"summary": {}'
        />
      );

      expect(screen.queryByText('}')).not.toBeInTheDocument();
      expect(screen.queryByText(/summary/)).not.toBeInTheDocument();
      expect(screen.queryByText(/正在执行:/)).not.toBeInTheDocument();
    });
  });

  describe('Loading States', () => {
    it('shows spinner when isStartingPM is true', () => {
      render(<ControlPanel {...defaultProps} isStartingPM={true} />);
      expect(document.querySelector('[class*="animate-spin"]')).toBeInTheDocument();
    });

    it('shows spinner when isStartingDirector is true', () => {
      render(<ControlPanel {...defaultProps} isStartingDirector={true} />);
      expect(document.querySelector('[class*="animate-spin"]')).toBeInTheDocument();
    });
  });
});
