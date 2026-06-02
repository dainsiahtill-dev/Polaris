import { describe, expect, it } from 'vitest';
import { parseRealtimePatchForDiff } from './realtimeDiffParsing';

describe('parseRealtimePatchForDiff', () => {
  it('treats raw create patches as new file content', () => {
    expect(
      parseRealtimePatchForDiff({
        operation: 'create',
        patch: 'export const value = 1;\n',
      }),
    ).toEqual({
      oldValue: '',
      newValue: 'export const value = 1;\n',
    });
  });

  it('preserves removed and added lines from unified modify patches', () => {
    const patch = [
      '--- a',
      '+++ b',
      '@@ -1,3 +1,3 @@',
      ' const keep = true;',
      '-const value = 1;',
      '+const value = 2;',
      ' export const done = true;',
    ].join('\n');

    expect(
      parseRealtimePatchForDiff({
        operation: 'modify',
        patch,
      }),
    ).toEqual({
      oldValue: 'const keep = true;\nconst value = 1;\nexport const done = true;',
      newValue: 'const keep = true;\nconst value = 2;\nexport const done = true;',
    });
  });

  it('preserves deleted content from unified delete patches', () => {
    const patch = ['--- a', '+++ b', '@@ -1,2 +0,0 @@', '-line one', '-line two'].join('\n');

    expect(
      parseRealtimePatchForDiff({
        operation: 'delete',
        patch,
      }),
    ).toEqual({
      oldValue: 'line one\nline two',
      newValue: '',
    });
  });
});
