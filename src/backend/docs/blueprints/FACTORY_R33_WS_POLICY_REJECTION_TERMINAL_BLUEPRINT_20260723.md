# Factory R33: WebSocket Policy Rejection Is Terminal

Status: implementation active; Bench remains `not_schedulable`.

## Observed fact

R32 received 802 runtime WebSocket attempts from an old R24 browser binding.
The isolated backend had a unique token and rejected the stale
`polaris-local-dev`/foreign-workspace connection with close code `1008`, but
the frontend transport treated every non-1000/1001 close as retryable. Port
reuse therefore turned one stale browser tab into an unbounded reconnect and
audit-write storm against the new instance.

## Contract

- WebSocket close code `1008` is a terminal policy/binding rejection.
- Runtime transport must not schedule automatic reconnect after `1008`.
- The connection state must retain a visible policy-rejection error.
- A later explicit `start()` (normally after page reload or corrected binding)
  may establish a new connection.
- Transient non-policy closes keep the existing bounded retry behavior.
- No Bench or Provider request is authorized by this change.

## Proof ladder

1. Focused frontend regression: `1008` schedules no reconnect.
2. Existing runtime socket transport suite remains green.
3. Frontend type check and lint remain green.
4. Only after CE terminalization is also closed may pre-bench scheduling be
   reconsidered.
