import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RuntimeErrorDialog } from './RuntimeErrorDialog';

describe('RuntimeErrorDialog', () => {
  it('bounds long error details so actions remain visible', () => {
    render(
      <RuntimeErrorDialog
        open
        issue={{
          code: 'LLM_RATE_LIMIT',
          title: '运行异常'.repeat(20),
          detail: [
            'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions?provider=extremely-long-provider-name-without-natural-breaks',
            ...Array.from({ length: 80 }, (_, index) => `line ${index + 1}: provider returned 429`),
          ].join('\n'),
        }}
        onOpenChange={vi.fn()}
        onOpenLogs={vi.fn()}
      />,
    );

    const dialog = screen.getByTestId('runtime-error-dialog');
    expect(dialog.className).toContain('max-h-[88vh]');
    expect(dialog.className).toContain('grid-rows-[auto_auto_auto]');
    expect(dialog.className).toContain('overflow-hidden');
    expect(screen.getByTestId('runtime-error-footer')).toHaveClass('flex-wrap');
    expect(screen.getByText(/https:\/\/token-plan/)).toHaveClass('break-words');
    expect(screen.getByText(/错误码:/)).toHaveClass('break-all');
    expect(screen.getByText('查看日志')).toBeInTheDocument();
    expect(screen.getByText('关闭')).toBeInTheDocument();
  });
});
