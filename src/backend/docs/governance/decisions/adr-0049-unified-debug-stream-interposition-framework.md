# ADR-0049: Unified Debug Stream Interposition Framework

Date: 2026-03-25

## Status

Accepted

## Context

The role console has suffered repeated visibility regressions:

1. `thinking` was swallowed by the unified terminal console.
2. Visible protocol wrappers such as `<output>` leaked into user-visible output.
3. Tool-call visibility drifted because delivery only recognized a narrow event set.
4. There was no canonical way to surface real LLM request payloads, strategy choices,
   session continuity projection, or compression/budget decisions.

The underlying problem was structural: observability was not modeled as its own
stream layer. Each feature leaked ad-hoc data through unrelated event types,
which made delivery rendering brittle and made lower-layer diagnostics invisible.

## Decision

Introduce a dedicated, context-local debug stream interposition framework:

1. `kernelone.telemetry.debug_stream` becomes the canonical opt-in debug event bus.
2. Lower layers emit structured debug events instead of printing.
3. `RoleConsoleHost` owns the debug session and interposes the debug stream between
   runtime execution and terminal rendering.
4. `terminal_console.py` renders debug events with dedicated visual treatment and
   never mixes them into normal assistant output.
5. `--debug` is the explicit user opt-in switch for full runtime diagnostics.

## Consequences

Positive:

1. Debug/observability becomes a first-class stream, not a side effect.
2. Real LLM request payloads can be surfaced without coupling the LLM caller to CLI code.
3. Strategy, continuity, and compression/budget decisions become observable in a stable way.
4. Delivery rendering remains isolated from kernel/runtime internals.

Trade-offs:

1. More event traffic is generated when debug mode is enabled.
2. Delivery hosts must decide how to render debug events; terminal console now owns a
   distinct renderer path for them.
3. The terminal can style debug output as dim grey, but standard terminals cannot
   reliably force a smaller font size.

## Verification

1. `polaris/kernelone/context/tests/test_strategy_run_context.py`
2. `polaris/delivery/cli/tests/test_terminal_console.py`
3. `polaris/cells/roles/kernel/tests/test_stream_visible_output_contract.py`
4. Real CLI smoke with `python -m polaris.delivery.cli console --debug ...`

## Follow-up

1. Reuse the same debug stream contract for non-CLI hosts.
2. Add scorecard/replay-oriented debug sinks if debug traces need structured persistence.
