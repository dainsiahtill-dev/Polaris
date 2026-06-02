import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProjectProgressPanel, extractLatestQaEvidence } from './ProjectProgressPanel';
import type { DialogueEvent } from './DialoguePanel';
import type { LogEntry } from './pm';

const qaLog: LogEntry = {
  id: 'qa-1',
  timestamp: '2026-06-02T00:00:00.000Z',
  level: 'success',
  source: 'QA',
  message: 'Project integration QA passed',
  meta: {
    reason: 'integration_qa_passed',
    evidence_grade: 'real_command_passed',
    summary: 'Integration verification passed: npm run test',
    passed: true,
  },
};

const qaDialogueEvent: DialogueEvent = {
  eventId: 'qa-dialogue-1',
  speaker: 'QA',
  type: 'review',
  content: 'Project integration QA: PASS; Integration verification passed: npm run test -- --watch=false',
  refs: { phase: 'integration_qa' },
  meta: {
    reason: 'integration_qa_passed',
    evidence_grade: 'real_command_passed',
    summary: 'Integration verification passed: npm run test -- --watch=false',
    passed: true,
  },
};

describe('ProjectProgressPanel QA evidence', () => {
  it('extracts the latest integration QA evidence grade from runtime logs', () => {
    expect(extractLatestQaEvidence([qaLog])).toEqual({
      grade: 'real_command_passed',
      reason: 'integration_qa_passed',
      summary: 'Integration verification passed: npm run test',
      passed: true,
    });
  });

  it('renders QA evidence grade instead of a vague PASS label', () => {
    render(
      <ProjectProgressPanel
        tasks={[]}
        pmRunning={false}
        executionLogs={[qaLog]}
        currentPhase="qa"
      />,
    );

    const evidence = screen.getByTestId('qa-evidence-grade');
    expect(evidence).toHaveTextContent('QA evidence');
    expect(evidence).toHaveTextContent('real command passed');
    expect(evidence).toHaveTextContent('integration_qa_passed');
  });

  it('recovers QA evidence from dialogue transcript metadata after reload', () => {
    expect(extractLatestQaEvidence([], [qaDialogueEvent])).toEqual({
      grade: 'real_command_passed',
      reason: 'integration_qa_passed',
      summary: 'Integration verification passed: npm run test -- --watch=false',
      passed: true,
    });

    render(
      <ProjectProgressPanel
        tasks={[]}
        pmRunning={false}
        executionLogs={[]}
        dialogueEvents={[qaDialogueEvent]}
        currentPhase="qa"
      />,
    );

    const evidence = screen.getByTestId('qa-evidence-grade');
    expect(evidence).toHaveTextContent('QA evidence');
    expect(evidence).toHaveTextContent('real command passed');
    expect(evidence).toHaveTextContent('integration_qa_passed');
  });
});
