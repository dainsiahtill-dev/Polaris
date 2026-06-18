import { describe, expect, it } from 'vitest';
import * as aiDialogue from '../index';

describe('ai-dialogue public exports', () => {
  it('does not expose the legacy SSE chat stream hook', () => {
    expect('useChatStream' in aiDialogue).toBe(false);
  });
});
