# Browser failure evidence closure

Fresh r04 runner exited1 at real Chromium canvas screenshot TimeoutError before
normal report persistence. Factory had already failed QA; screenshot failure cannot
make it pass. Playwright's TimeoutError inherits its own Error, not builtin
TimeoutError/RuntimeError, so both current exception tuples miss it.

Bounded change in factory.pipeline internal test harness: explicitly catch
Playwright Error at the browser probe and outer browser boundary, record failure,
retain timeout/console/resource evidence and permit normal failed-report handling.
No timeouts, pixel thresholds, acceptance assertions or target source are changed.
Only an unavailable optional Playwright import keeps the existing HTTP fallback;
an available browser's runtime failure must never fall back to HTTP success.

TDD: real moving blank canvas reproduces escaping exception; after repair it returns
ok=false with original timeout. Stable blank remains false, painted remains true;
injected browser runtime error remains false. Existing bench gates still apply.

TaskRuntime disappearance is a separate normal terminal-drain action: r04 froze
five authoritative rows before reset/tombstones and released its workspace lease.
Do not blame screenshot failure for that reset or fabricate replacement task state.

## Headless environment isolation

One-variable dynamic browser tests independently established inherited
DISPLAY=127.0.0.1:0 stalls native canvas fillRect; removing only DISPLAY restores
painting, while adding it to the previously healthy environment reproduces the
stall. HTTP served fully; pytest/PWD/import/loopback explanations were disproved.
Remove DISPLAY only from the copied environment of Chromium launched headless=True.
Do not mutate the parent/global environment or alter headed browser behavior.
Regression deliberately injects the stale DISPLAY and retains actual painted/blank
pixel criteria. This is execution environment isolation, not an acceptance waiver.
