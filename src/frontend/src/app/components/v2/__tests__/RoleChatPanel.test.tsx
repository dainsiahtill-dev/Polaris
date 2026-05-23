import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RoleChatPanel } from '../RoleChatPanel';

const apiFetchMock = vi.fn();

vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

vi.mock('@/app/utils/devLogger', () => ({
  devLogger: {
    warn: vi.fn(),
  },
}));

describe('RoleChatPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads backend registered role-chat roles without Scout', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          roles: ['pm', 'chief_engineer', 'director', 'scout'],
          count: 4,
        }),
        { status: 200 },
      ),
    );

    render(<RoleChatPanel defaultRole="chief_engineer" />);

    const selector = screen.getByRole('combobox', { name: 'Select role' });
    await waitFor(() => expect(screen.getByTestId('role-chat-registry-source')).toHaveTextContent('roles: backend'));
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/role/chat/roles');
    expect(selector).toHaveValue('chief_engineer');
    expect(screen.getByRole('option', { name: 'Chief Engineer (工部尚书)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Director (工部侍郎)' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Scout (探子)' })).not.toBeInTheDocument();
  });

  it('sends Chief Engineer messages through the generic role-chat endpoint', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          roles: ['pm', 'architect', 'chief_engineer', 'director', 'qa'],
          count: 5,
        }),
        { status: 200 },
      ),
    );
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          response: 'Ready for Director handoff',
          role: 'chief_engineer',
        }),
        { status: 200 },
      ),
    );

    render(<RoleChatPanel defaultRole="chief_engineer" />);

    await waitFor(() => expect(screen.getByTestId('role-chat-registry-source')).toHaveTextContent('roles: backend'));
    fireEvent.change(screen.getByLabelText('Message input'), {
      target: { value: 'Review the implementation plan' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith('/v2/role/chief_engineer/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: 'Review the implementation plan',
          context: undefined,
        }),
      });
    });
    expect(await screen.findByText('Ready for Director handoff')).toBeInTheDocument();
  });
});
