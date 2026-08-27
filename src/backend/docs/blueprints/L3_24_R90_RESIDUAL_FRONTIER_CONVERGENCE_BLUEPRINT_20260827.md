# L3-24 R90 residual-frontier convergence blueprint

## Classification

Pattern change. No new Cell, public API, state owner, or cross-Cell dependency is introduced.
The change remains inside Factory workspace-quality scheduling plus KernelOne residual attribution.

## Exact-run evidence

- Run: `factory_28b010ca7fe2`
- Project: `L3-24`, attempt `r90`
- Workspace (read-only evidence): `/tmp/factory-bench-l3-24-r90/workspaces/9e40b46ba518-35df0743f8d7fede/L3-24-023d7314133040b6/6dcc48f118bf3a0bb04a0f03`
- Verifier truth: C++ build and real-run passed; named behavior checks remained red; production depth was `421 < 650`.
- Candidate truth: two cipher transactions were rejected and rolled back; moon/keyword/depth residual owners never received a later repair wave.
- Dynamic selector probe: the same residual payload returns four production targets and rotates away from claimed cipher targets. The targets are lost after selection, before the global non-progress stop.
- Attribution truth: the explicit product-depth failure was reported as `M06_director_multi_task` only because the downstream failed TaskRuntime row contained `task_runtime_not_completed`.

## Invariants

1. Generated Bench workspaces remain read-only to the main Agent.
2. Candidate compile/verifier rejection always rolls back before another repair wave.
3. Three non-progress rounds remain the default global fuse; arbitrary owner rotation cannot buy unbounded retries.
4. One rejected transaction may yield at most one bounded residual-frontier handoff when current verifier evidence names a different, unclaimed, existing target.
5. The handoff stays in Director repair; PM and Chief Engineer are never restarted.
6. An explicit product/verifier failure remains primary over a downstream TaskRuntime consequence. M06 remains primary only for delivery-green, control-plane-only boundary failures.

## Data flow

```text
verifier residuals
  -> causal target extraction
  -> immutable TaskRuntime/JobToken owner claim
  -> candidate transaction
  -> verifier/candidate guard
     -> accepted: reset non-progress budget
     -> rejected: rollback + record rejected targets
        -> if distinct unclaimed residual frontier exists and handoff unused
           -> one bounded same-Director frontier wave
        -> else global fuse stops

factory audit residuals
  -> explicit product/verifier failure detection
  -> module ladder attribution
     -> product quality/depth first
     -> downstream M06 only when no explicit product failure exists
```

## Verification

- RED/GREEN characterization for rejected transaction plus distinct residual frontier.
- Existing global no-op/equal-count rotation caps remain unchanged.
- Residual-attribution regression: depth failure plus downstream TaskRuntime failure must not become M06.
- Targeted Ruff, Mypy, Pytest.
- Fresh isolated L3-24 Bench only after local gates are green.

