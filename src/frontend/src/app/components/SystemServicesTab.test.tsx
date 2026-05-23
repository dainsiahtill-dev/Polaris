import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SystemServicesTab, normalizeCapabilityLabels } from './SystemServicesTab';

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

function jsonResponse(payload: unknown) {
  return {
    json: vi.fn().mockResolvedValue(payload),
  };
}

describe('SystemServicesTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiFetchMock.mockImplementation((path: string) => {
      if (path === '/arsenal/mcp/status') {
        return Promise.resolve(jsonResponse({ available: true, tools: ['read_file'] }));
      }

      if (path === '/arsenal/director/capabilities') {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            role: 'director',
            capabilities: {
              electron_workbench: ['read_files', 'write_files'],
              workflow: ['execute_tests'],
            },
          }),
        );
      }

      if (path === '/arsenal/vision/status') {
        return Promise.resolve(jsonResponse({ pil_available: true, model_loaded: false }));
      }

      return Promise.resolve(jsonResponse({ ok: true }));
    });
  });

  it('normalizes array and host-scoped capability payloads', () => {
    expect(normalizeCapabilityLabels(['write_files', 'read_files', 'read_files'])).toEqual([
      'read_files',
      'write_files',
    ]);
    expect(
      normalizeCapabilityLabels({
        electron_workbench: ['write_files', 'read_files'],
        policy: { delete_files: false, execute_tests: true },
      }),
    ).toEqual([
      'electron_workbench: read_files',
      'electron_workbench: write_files',
      'policy: execute_tests',
    ]);
  });

  it('renders the legacy Director capability map as online desktop evidence', async () => {
    render(<SystemServicesTab />);

    await screen.findByText('Director Capabilities Overview');

    expect(await screen.findByText('3 项权限已启用')).toBeInTheDocument();
    expect(screen.getByText('electron_workbench: read_files')).toBeInTheDocument();
    expect(screen.getByText('electron_workbench: write_files')).toBeInTheDocument();
    expect(screen.getByText('workflow: execute_tests')).toBeInTheDocument();
    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith('/arsenal/director/capabilities');
    });
  });
});
