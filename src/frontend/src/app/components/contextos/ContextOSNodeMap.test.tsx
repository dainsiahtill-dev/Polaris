import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ContextOSNodeMap } from './ContextOSNodeMap';
import type { ContextPayload } from './ContextViewerModal';
import type { EngineeringEvent } from '@/services/api.types';
import type { FinalProviderRequestPayload } from './finalProviderRequestProtocol';

const content: ContextPayload = {
  schema_version: 1,
  hash: 'abc',
  trace_id: null,
  call_id: 'run:qa:9',
  stored_at: null,
  message_count: 1,
  total_chars: 24,
  messages: [{ role: 'user', content: '{"workspace_revision":7}' }],
};

const finalRequest: FinalProviderRequestPayload = {
  call_id: 'run:qa:9',
  provider_request: { body: { messages: [{ role: 'tool', tool_call_id: 'current-call' }] } },
};

const events: EngineeringEvent[] = [
  { session_id: 'run', workspace: '/ws', seq: 1, role: 'qa', kind: 'tool_observed', payload: { call_id: 'old-call', tool: 'execute_command', ok: true, content: 'done' } },
  { session_id: 'run', workspace: '/ws', seq: 2, role: 'qa', kind: 'run_updated', payload: { workspace_revision: 7, verification: 'passed' } },
];

describe('ContextOSNodeMap', () => {
  it('renders fold states and links evidence back to the final request', () => {
    const onEvidenceSelect = vi.fn();
    render(<ContextOSNodeMap content={content} finalRequest={finalRequest} events={events} onEvidenceSelect={onEvidenceSelect} />);
    expect(screen.getByTestId('contextos-node-map')).toBeInTheDocument();
    expect(screen.getByTestId('contextos-node-event-1')).toHaveAttribute('data-fold-state', 'REFERENCE');
    expect(screen.getByTestId('contextos-node-event-2')).toHaveAttribute('data-fold-state', 'FOLDED');
    fireEvent.click(screen.getByTestId('contextos-node-event-1'));
    fireEvent.click(screen.getByTestId('contextos-node-evidence-link'));
    expect(onEvidenceSelect).toHaveBeenCalledWith('old-call');
    fireEvent.click(screen.getByTestId('contextos-node-filter-FOLDED'));
    expect(screen.queryByTestId('contextos-node-event-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('contextos-node-event-2')).toBeInTheDocument();
  });
});
