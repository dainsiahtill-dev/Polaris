import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ContextProjectionManifest } from './contextProjectionManifest';
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
    expect(onEvidenceSelect).toHaveBeenCalledWith('old-call', undefined);
    fireEvent.click(screen.getByTestId('contextos-node-filter-FOLDED'));
    expect(screen.queryByTestId('contextos-node-event-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('contextos-node-event-2')).toBeInTheDocument();
  });

  it('uses the backend manifest for hierarchy, distribution and precise evidence navigation', () => {
    const manifest: ContextProjectionManifest = {
      schema_version: 'context.projection_manifest.v1',
      context_snapshot_ref: 'abc',
      workspace: '/ws',
      run_id: 'run',
      call_id: 'run:qa:9',
      run_cards: [{ id: 'turn-1', seq: 1, role: 'qa', phase: 'qa_probe', verification: 'passed', workspace_revision: 7, child_ids: ['event-3'] }],
      nodes: [{
        id: 'event-3', parent_id: 'turn-1', seq: 3, turn: 1, kind: 'tool_log', title: 'execute_command', summary: 'done', role: 'qa',
        fold_state: 'ACTIVE', fold_reason: 'current', evidence_ref: 'current-call', projection_ref: 'request:0', request_message_index: 0,
        phase: null, ok: true, original_tokens: 10, projected_tokens: 10,
      }],
      summary: { original_tokens: 10, projected_tokens: 10, compression_ratio: 1, fold_counts: { ACTIVE: 1 }, kind_counts: { tool_log: 1 }, run_card_count: 1, scope: 'test' },
    };
    const onEvidenceSelect = vi.fn();
    render(<ContextOSNodeMap content={content} finalRequest={finalRequest} manifest={manifest} onEvidenceSelect={onEvidenceSelect} />);
    expect(screen.getByTestId('contextos-compression-bar')).toHaveTextContent('100.0%');
    expect(screen.getByTestId('contextos-runcard-turn-1')).toHaveTextContent('phase=qa_probe');
    fireEvent.click(screen.getByTestId('contextos-node-event-3'));
    fireEvent.click(screen.getByTestId('contextos-node-evidence-link'));
    expect(onEvidenceSelect).toHaveBeenCalledWith('current-call', 0);
  });
});
