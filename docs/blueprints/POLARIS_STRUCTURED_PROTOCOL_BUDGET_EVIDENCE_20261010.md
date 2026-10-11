# Structured protocol preservation and budget refusal diagnostics

POL-91; mode=implementation; owner=PM/CE contract and blueprint engineer.
Scope is the nine paths in the frozen MANIFEST, authorized by the board comment
47b7c732-1cbc-4b99-ba89-e54dd669405b. This is an external engineering repair,
not a Polaris execution role or a project delivery verdict.

## Root cause and repair

`OutputParser.parse_thinking` and visible-wrapper cleanup previously interpreted
tags inside JSON values as protocol. The canonical transcript sanitizer could
match `<html>` in one task field through `</html>` in another. The frozen known
prefix loses 4,652 characters at [1014,5666), splicing task identity and routing.
This is independent of PM fission rejection, which preserves the original graph.

The generic protocol boundary now lexically protects JSON string tokens,
including escaped quotes/backslashes, Unicode and an unterminated final string.
It does not require complete valid JSON and never repairs or completes JSON.
Envelope closing tags inside those strings cannot terminate an actual external
tool envelope. XML calls accept complete JSON argument objects before interpreting
XML argument elements. Native execution, aliases and allowlists retain their
existing owner and restrictions; text extraction remains audit/sanitization only.

Actual thinking/output/result wrappers outside data still clean normally. Result
wrappers with JSON containing a literal closing tag now remove the real envelope.
The transaction completion/result and PM admission regressions retain the real
parser, history construction, result projection and PM normalization; only external
persisting ports are substituted. Synthetic complete fixtures are labeled as such.
Independent complete historical task objects are forensic fixtures, never a
recovered plan or a revision base.

## Observation boundary

`GatewayTelemetry.emit_budget_exceeded_observation` uses the existing
`resolve_run_events_path` and gateway-bound `emit_event` sink. It observes actual
system reservation, current instruction and final assembly rejection origins,
then the gateway raises the original `BudgetExceededError` unchanged.

The event is `kind=observation`, `name=context.budget_exceeded`, `ok=false`.
Its output allowlist is: preparation stage, exception code/limit/requested/current,
enforcement total, role policy limit, the already-resolved model window when
available, reserved system tokens, remaining/current tokens when already computed,
and SHA-256 plus character counts for raw current, sanitized current when already
computed, and actual system text. Refs contain existing run/role/task identity.
No prompts, arbitrary metadata, connection configuration, credentials, output
allowance guesses, provider lifecycle, snapshot, receipt or authority claims enter
this output. A resolved fallback window is not a physical model qualification.

Missing run identity/destination produces no observation file. The canonical
existing-run guard is retained; an explicit events path remains trusted under the
existing contract. No alternate root, phantom run directory or new storage owner
is added. Resolver, emitter and I/O failures are isolated from the typed refusal.
The observer is best effort and cannot guarantee recording for unbound requests.

## Assumptions and verification card

1. A tag start inside a JSON string is data, including partial forensic prefixes.
   Captured-prefix and cross-field tag regressions test this assumption.
2. Closing tags inside JSON arguments must not terminate external envelopes.
   XML, XML args, canonical bracket/angle and native argument controls test this.
3. Preservation cannot confer plan validity. Incomplete prefix and synthetic
   truncated transaction/PM controls remain rejected.
4. Budget operands belong to their actual throw. Real isolated existing-run
   persistence tests compare events to the real gateway exception and exact
   allowlist, including sanitized input fingerprints and privacy decoys.
5. Diagnostic effects cannot change admission. Missing binding, failed resolution,
   failed emitter, system reservation, policy cap and small-window tests enforce it.

Pre-mortem: blanket JSON bypass would hide real external protocol; regular
expressions could close envelopes at literal argument markup; observation errors
could replace the original exception; successful event defaults could mislabel
refusal. Each is covered by regressions. The authorized blueprint holds this
verification card; no out-of-scope governance files are created.

Validation uses the existing `.venv/bin/python`, isolated HOME/cache/CWD and
source PYTHONPATH, with no dependency install. Relevant parser and ContextGateway
suites, changed-production ruff and mypy are recorded in the engineering report.
Original existing test assertions are compared by AST and retained. The native
runtime trace test is explicitly excluded because this assignment has no native
API/Provider/Bench authorization. No UI surface changes require browser verification.

## Preserved limits and next acceptance

No cap, model/provider config, sanitizer, estimator, prompt/history contract,
PM task ID/dependency/fission, permission or quality gate is changed. Existing
one-token empty-projection accounting means a current instruction fitting its
individual limit can still fail final assembly; boundary controls preserve it.

The original full 10,105-character reply is unavailable; only the known 8,000
characters are preserved. The missing 2,105 characters are never invented.
Historical correction request switches and actual gateway configuration remain
unknown. Reconstructed 4894/5690/3106 operands under 8000 are not historical native
evidence. Initial PM 60000/750s qualification does not establish CE/full capacity.

Independent board/Root review must inspect reports, diff, frozen hashes and fresh
verification after genuine employee namespace release. This repair does not grant
Provider dispatch, settle a run, qualify CE output or demonstrate fresh isolated
L1-L12 completion. No new native grant is issued by this document.
