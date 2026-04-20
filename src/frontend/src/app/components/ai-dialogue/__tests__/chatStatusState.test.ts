import { resolveDialogueStatusKind } from '../chatStatusState';

describe('resolveDialogueStatusKind', () => {
  it('returns loading while status fetch is in flight', () => {
    expect(resolveDialogueStatusKind(null, true)).toBe('loading');
  });

  it('returns ready for configured and reachable roles', () => {
    expect(resolveDialogueStatusKind({ ready: true, configured: true }, false)).toBe('ready');
  });

  it('returns unconfigured only for explicit configuration failures', () => {
    expect(resolveDialogueStatusKind({ ready: false, configured: false }, false)).toBe('unconfigured');
  });

  it('returns error when the status request failed and configuration is unknown', () => {
    expect(resolveDialogueStatusKind({ ready: false }, false)).toBe('error');
    expect(resolveDialogueStatusKind(null, false)).toBe('error');
  });
});
