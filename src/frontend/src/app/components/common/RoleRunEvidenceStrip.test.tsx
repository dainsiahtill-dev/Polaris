import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RoleRunEvidenceStrip } from './RoleRunEvidenceStrip';

describe('RoleRunEvidenceStrip', () => {
  it('renders a run endpoint, normalized status details, backend message, and cancel result', () => {
    const onCancel = vi.fn();
    const onRefresh = vi.fn();

    render(
      <RoleRunEvidenceStrip
        tone="emerald"
        testId="run-evidence"
        endpoint="/v2/director/runs/run-1"
        workspace="C:/Temp/Product"
        loading={false}
        status="RUNNING"
        details={['queued=3']}
        message="Status: RUNNING"
        refreshTestId="run-refresh"
        refreshDisabled={false}
        refreshLoading={false}
        realtimePushActive
        onRefresh={onRefresh}
        cancelTestId="run-cancel"
        cancelDisabled={false}
        cancelLoading={false}
        onCancel={onCancel}
        cancelResultTestId="run-cancel-result"
        cancelResultEndpoint="/v2/director/runs/run-1/cancel"
        cancelResultVisible
        cancelResultLoading={false}
        cancelResultMessage="取消运行已提交: CANCELLED"
      />,
    );

    const evidence = screen.getByTestId('run-evidence');
    expect(evidence).not.toHaveTextContent('/v2/director/runs/run-1');
    expect(screen.getByTestId('run-evidence-endpoint')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/runs/run-1?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(evidence).toHaveTextContent('RUNNING · queued=3');
    expect(evidence).toHaveTextContent('Status: RUNNING');
    expect(screen.getByTestId('run-evidence-realtime-push')).toHaveTextContent('实时推送');
    expect(screen.getByTestId('run-cancel-result')).toHaveAttribute(
      'data-endpoint',
      '/v2/director/runs/run-1/cancel?workspace=C%3A%2FTemp%2FProduct',
    );
    expect(screen.getByTestId('run-cancel-result')).not.toHaveTextContent('/v2/director/runs/run-1/cancel');

    fireEvent.click(screen.getByTestId('run-refresh'));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('run-cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('shows loading and error states without hiding the cancel endpoint evidence', () => {
    render(
      <RoleRunEvidenceStrip
        tone="amber"
        testId="run-evidence"
        endpoint="/v2/pm/runs/run-2"
        loading={false}
        error="run detail unavailable"
        cancelTestId="run-cancel"
        cancelDisabled
        cancelLoading
        onCancel={vi.fn()}
        cancelResultTestId="run-cancel-result"
        cancelResultEndpoint="/v2/pm/runs/run-2/cancel"
        cancelResultVisible
        cancelResultLoading
      />,
    );

    expect(screen.getByTestId('run-evidence')).toHaveTextContent('run detail unavailable');
    expect(screen.getByTestId('run-cancel-result')).toHaveAttribute('data-endpoint', '/v2/pm/runs/run-2/cancel');
    expect(screen.getByTestId('run-cancel-result')).toHaveTextContent('cancelling');
    expect(screen.getByTestId('run-cancel')).toBeDisabled();
  });
});
