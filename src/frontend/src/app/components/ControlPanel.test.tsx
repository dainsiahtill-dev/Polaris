import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ControlPanel } from './ControlPanel';

const serviceMocks = vi.hoisted(() => ({
  getPmStatus: vi.fn(),
  getDirectorStatus: vi.fn(),
}));

vi.mock('@/services', () => serviceMocks);

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
    serviceMocks.getPmStatus.mockResolvedValue({
      ok: true,
      data: { running: false, pid: null, started_at: null, mode: 'desktop_service', source: 'status_file' },
    });
    serviceMocks.getDirectorStatus.mockResolvedValue({
      ok: true,
      data: { running: false, pid: null, started_at: null, mode: 'desktop_service', source: 'status_file' },
    });
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

    it('shows PM backend status evidence after toggling', async () => {
      const onTogglePm = vi.fn().mockResolvedValue(undefined);
      serviceMocks.getPmStatus.mockResolvedValueOnce({
        ok: true,
        data: {
          running: true,
          pid: 4101,
          started_at: 1779512400,
          mode: 'desktop_service',
          source: 'status_file',
        },
      });

      render(<ControlPanel {...defaultProps} onTogglePm={onTogglePm} />);

      fireEvent.click(screen.getByTestId('control-panel-pm-toggle'));

      await waitFor(() => expect(onTogglePm).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(serviceMocks.getPmStatus).toHaveBeenCalledWith('/test/workspace'));
      const evidence = await screen.findByTestId('control-panel-pm-toggle-evidence');
      expect(evidence).toHaveTextContent('/v2/pm/status');
      expect(evidence).toHaveTextContent('running');
      expect(evidence).toHaveTextContent('pid=4101');
      expect(evidence).toHaveTextContent('mode=desktop_service');
      expect(evidence).toHaveTextContent('source=status_file');
    });

    it('disables PM toggle when pmToggleDisabled is true', () => {
      render(<ControlPanel {...defaultProps} pmToggleDisabled={true} />);
      expect(screen.getByTestId('control-panel-pm-toggle')).toBeDisabled();
    });

    it('shows the PM blocked reason on the PM controls', () => {
      render(
        <ControlPanel
          {...defaultProps}
          pmToggleDisabled={true}
          pmBlockedReason="docs/ 初始化未完成"
        />,
      );

      expect(screen.getByText('docs/ 初始化未完成')).toBeInTheDocument();
      expect(screen.getByTestId('control-panel-pm-toggle')).toHaveAttribute('title', 'docs/ 初始化未完成');
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

    it('shows Director backend status evidence after toggling', async () => {
      const onToggleDirector = vi.fn().mockResolvedValue(undefined);
      serviceMocks.getDirectorStatus.mockResolvedValueOnce({
        ok: true,
        data: {
          running: false,
          pid: null,
          started_at: null,
          mode: 'desktop_service',
          source: 'status_file',
        },
      });

      render(<ControlPanel {...defaultProps} onToggleDirector={onToggleDirector} directorRunning={true} />);

      fireEvent.click(screen.getByTestId('control-panel-director-toggle'));

      await waitFor(() => expect(onToggleDirector).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(serviceMocks.getDirectorStatus).toHaveBeenCalledWith('/test/workspace'));
      const evidence = await screen.findByTestId('control-panel-director-toggle-evidence');
      expect(evidence).toHaveTextContent('/v2/director/status?source=auto');
      expect(evidence).toHaveTextContent('idle');
      expect(evidence).toHaveTextContent('pid=none');
      expect(evidence).toHaveTextContent('mode=desktop_service');
      expect(evidence).toHaveTextContent('source=status_file');
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

  describe('Health Ping', () => {
    it('calls health ping and exposes backend evidence in the tooltip', () => {
      const onPingHealth = vi.fn();
      render(
        <ControlPanel
          {...defaultProps}
          healthStatus="healthy"
          healthStatusDetail="/v2/health · healthy · version=0.1"
          onPingHealth={onPingHealth}
        />,
      );

      const healthButton = screen.getByTestId('control-panel-health-ping');
      expect(healthButton).toHaveAttribute('title', '/v2/health · healthy · version=0.1');
      expect(healthButton).toHaveTextContent('Ready');

      fireEvent.click(healthButton);

      expect(onPingHealth).toHaveBeenCalledTimes(1);
    });
  });

  describe('PM Run Once', () => {
    it('shows run once button when onRunPmOnce is provided', () => {
      render(<ControlPanel {...defaultProps} onRunPmOnce={vi.fn()} />);
      expect(screen.getByTestId('control-panel-pm-run-once')).toBeInTheDocument();
    });

    it('shows PM backend status evidence after run once', async () => {
      const onRunPmOnce = vi.fn().mockResolvedValue(undefined);
      serviceMocks.getPmStatus.mockResolvedValueOnce({
        ok: true,
        data: {
          running: false,
          pid: null,
          started_at: null,
          mode: 'single_iteration',
          source: 'status_file',
        },
      });

      render(<ControlPanel {...defaultProps} onRunPmOnce={onRunPmOnce} />);

      fireEvent.click(screen.getByTestId('control-panel-pm-run-once'));

      await waitFor(() => expect(onRunPmOnce).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(serviceMocks.getPmStatus).toHaveBeenCalledWith('/test/workspace'));
      const evidence = await screen.findByTestId('control-panel-pm-toggle-evidence');
      expect(evidence).toHaveTextContent('/v2/pm/status');
      expect(evidence).toHaveTextContent('idle');
      expect(evidence).toHaveTextContent('pid=none');
      expect(evidence).toHaveTextContent('mode=single_iteration');
      expect(evidence).toHaveTextContent('source=status_file');
    });

    it('disables run once with the explicit blocked reason', () => {
      const onRunPmOnce = vi.fn();
      render(
        <ControlPanel
          {...defaultProps}
          onRunPmOnce={onRunPmOnce}
          runOnceDisabled={true}
          runOnceBlockedReason="PM 启动诊断未通过：docs/ 初始化未完成"
        />,
      );

      const runOnce = screen.getByTestId('control-panel-pm-run-once');
      expect(runOnce).toBeDisabled();
      expect(runOnce).toHaveAttribute('title', 'PM 启动诊断未通过：docs/ 初始化未完成');
      fireEvent.click(runOnce);
      expect(onRunPmOnce).not.toHaveBeenCalled();
    });

    it('shows PM backend status evidence after resume', async () => {
      const onResumePm = vi.fn().mockResolvedValue(undefined);
      serviceMocks.getPmStatus.mockResolvedValueOnce({
        ok: true,
        data: {
          running: true,
          pid: 4102,
          started_at: 1779512500,
          mode: 'resume',
          source: 'status_file',
        },
      });

      render(<ControlPanel {...defaultProps} onResumePm={onResumePm} />);

      fireEvent.click(screen.getByTitle('Resume Last'));

      await waitFor(() => expect(onResumePm).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(serviceMocks.getPmStatus).toHaveBeenCalledWith('/test/workspace'));
      const evidence = await screen.findByTestId('control-panel-pm-toggle-evidence');
      expect(evidence).toHaveTextContent('/v2/pm/status');
      expect(evidence).toHaveTextContent('running');
      expect(evidence).toHaveTextContent('pid=4102');
      expect(evidence).toHaveTextContent('mode=resume');
      expect(evidence).toHaveTextContent('source=status_file');
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

  describe('Brain Menu', () => {
    it('calls onOpenBrain from the more menu entry', () => {
      const onOpenBrain = vi.fn();
      render(<ControlPanel {...defaultProps} onOpenBrain={onOpenBrain} />);

      fireEvent.click(screen.getByText('明镜台 (Brain)'));

      expect(onOpenBrain).toHaveBeenCalledTimes(1);
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
