import { LLM_OBSERVATIONS_LOGICAL_PATH } from '../useUsageStats';
import { normalizeArtifactPath } from '@/services/fileService';

describe('useUsageStats logical path', () => {
  it('reads usage stats from the logical runtime path', () => {
    expect(LLM_OBSERVATIONS_LOGICAL_PATH).toBe('runtime/events/llm.observations.jsonl');
    expect(normalizeArtifactPath(LLM_OBSERVATIONS_LOGICAL_PATH)).toBe('runtime/events/llm.observations.jsonl');
  });

  it('does not depend on a workspace-prefixed runtime path', () => {
    const workspacePrefixed = 'X:/Git/polaris/runtime/events/llm.observations.jsonl';
    expect(LLM_OBSERVATIONS_LOGICAL_PATH).not.toBe(workspacePrefixed);
  });
});
