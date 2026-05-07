import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import type React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LogsModal } from './LogsModal';

// Mock API fetch
const mockApiFetch = vi.fn();
vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
  connectWebSocket: vi.fn().mockResolvedValue({
    close: vi.fn(),
    send: vi.fn(),
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
  }),
}));

// Mock logs parser modules
vi.mock('./logs/CodexCliStreamParser', () => ({
  CodexCliStreamParser: vi.fn().mockImplementation(() => ({
    feedLine: vi.fn(),
    get events() {
      return [];
    },
  })),
  parseCodexCliLines: vi.fn().mockReturnValue([]),
  stripLlmTags: vi.fn((text: string) => text),
}));

vi.mock('./logs/LlmEventTypes', () => ({
  parseLlmEventLine: vi.fn().mockReturnValue(null),
  parseLlmEventLines: vi.fn().mockReturnValue([]),
}));

vi.mock('./logs/LlmEventCard', () => ({
  LlmEventCard: ({ event }: { event: unknown }) => (
    <div data-testid="llm-event-card">{JSON.stringify(event)}</div>
  ),
}));

vi.mock('./PolarisTerminalRenderer', () => ({
  PolarisTerminalRenderer: () => <div data-testid="hp-terminal">Terminal</div>,
}));

vi.mock('@/app/utils/xssSanitizer', () => ({
  sanitizeHtml: vi.fn((html: string) => html),
}));

const mockLogContent = `2024-01-15 10:00:00 [INFO] Starting process
2024-01-15 10:00:01 [DEBUG] Loading config`;

const defaultProps = {
  isOpen: true,
  onClose: vi.fn(),
  workspace: '/test/workspace',
};

async function renderOpenLogsModal(
  props: Partial<React.ComponentProps<typeof LogsModal>> = {}
) {
  const result = render(<LogsModal {...defaultProps} {...props} />);
  await waitFor(() => {
    expect(mockApiFetch).toHaveBeenCalled();
  });
  await act(async () => { });
  return result;
}

describe('LogsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApiFetch.mockReset();
    mockApiFetch.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          content: mockLogContent,
          mtime: '2024-01-15T10:00:05Z',
        }),
    });
  });

  describe('Basic Rendering', () => {
    it('renders modal when isOpen is true', async () => {
      await renderOpenLogsModal();
      expect(screen.getByText('运行日志')).toBeInTheDocument();
    });

    it('returns null when isOpen is false', () => {
      const { container } = render(<LogsModal {...defaultProps} isOpen={false} />);
      expect(container.firstChild).toBeNull();
    });
  });

  describe('Source Selection', () => {
    it('renders source tabs', async () => {
      await renderOpenLogsModal();
      expect(screen.getByText('PM 子进程')).toBeInTheDocument();
      expect(screen.getByText('PM 禀报')).toBeInTheDocument();
      expect(screen.getByText('Director 子进程')).toBeInTheDocument();
    });

    it('respects initialSourceId prop', async () => {
      await renderOpenLogsModal({ initialSourceId: 'director' });
      expect(screen.getByText('Director 子进程')).toBeInTheDocument();
    });
  });

  describe('Banner Display', () => {
    it('shows banner when provided', async () => {
      await renderOpenLogsModal({ banner: 'Important message' });
      expect(screen.getByText('Important message')).toBeInTheDocument();
    });

    it('shows dismiss button when onDismissBanner provided', async () => {
      const onDismissBanner = vi.fn();
      await renderOpenLogsModal({ banner: 'Important', onDismissBanner });
      expect(screen.getByLabelText('关闭提示')).toBeInTheDocument();
    });

    it('calls onDismissBanner when dismiss button clicked', async () => {
      const onDismissBanner = vi.fn();
      await renderOpenLogsModal({ banner: 'Important', onDismissBanner });
      fireEvent.click(screen.getByLabelText('关闭提示'));
      expect(onDismissBanner).toHaveBeenCalledTimes(1);
    });
  });

  describe('Multiple Sources', () => {
    it('shows all default sources', async () => {
      await renderOpenLogsModal();
      expect(screen.getByText('PM 子进程')).toBeInTheDocument();
      expect(screen.getByText('PM 禀报')).toBeInTheDocument();
      expect(screen.getByText('PM 纪要（jsonl）')).toBeInTheDocument();
      expect(screen.getByText('Director 子进程')).toBeInTheDocument();
      expect(screen.getByText('谋划稿')).toBeInTheDocument();
      expect(screen.getByText('Ollama')).toBeInTheDocument();
      expect(screen.getByText('审校')).toBeInTheDocument();
      expect(screen.getByText('运行纪要')).toBeInTheDocument();
    });
  });
});
