import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AIDialogueHeader } from '../AIDialogueHeader';
import { AIInputArea } from '../AIInputArea';

const theme = {
  primary: 'amber',
  secondary: 'amber-400',
  gradient: 'from-amber-500 to-amber-700',
};

describe('AI dialogue loading status copy', () => {
  it('does not describe a loading role status as a failure', () => {
    render(
      <AIDialogueHeader
        theme={theme}
        roleName="PM"
        statusDisplay={<span>检查中...</span>}
        configuredProviderLabel="codex_cli"
        configuredModelLabel="gpt-5.3-codex"
        hasConversation={false}
        showHistory={false}
        isChatReady={false}
        statusKind="loading"
        onLoadHistory={vi.fn()}
        onClear={vi.fn()}
        onToggleHistory={vi.fn()}
      />,
    );

    expect(screen.getByText('PM 状态检查中')).toBeInTheDocument();
    expect(screen.queryByText('PM 状态获取失败')).not.toBeInTheDocument();
  });

  it('uses a loading placeholder before the chat status is known', () => {
    render(
      <AIInputArea
        value=""
        onChange={vi.fn()}
        onKeyDown={vi.fn()}
        onSend={vi.fn()}
        isLoading={false}
        isChatReady={false}
        isExplicitlyUnconfigured={false}
        statusKind="loading"
        roleName="PM"
        theme={theme}
      />,
    );

    expect(screen.getByPlaceholderText('PM 状态检查中...')).toBeDisabled();
    expect(screen.getByText('正在检查角色状态')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('PM 状态异常，请先重试')).not.toBeInTheDocument();
  });
});
