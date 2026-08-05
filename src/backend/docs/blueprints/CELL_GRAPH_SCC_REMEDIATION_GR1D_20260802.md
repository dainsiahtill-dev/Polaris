# GR1D — Cell Graph SCC Remediation

Status: active; GR1D-B1 accepted, subsequent graph waves planned
Owner: platform architecture governance; implementation is one edge-cut wave
per Cell owner.

## Baseline

The catalog currently has a 49-Cell strongly connected component containing
both `runtime.projection` and `factory.pipeline` (334 internal declared edges).
The shortest verified cycle is:

```text
runtime.projection -> director.execution -> factory.pipeline -> runtime.projection
```

It is not acceptable to suppress this with an allowlist or claim graph closure
after removing only one direct import.  At the same time, deleting all SCC
edges in one change would erase real ownership and cross-Cell runtime coupling.

## Edge ledger

| Edge | Classification | Action |
| --- | --- | --- |
| `runtime.projection -> director.execution` | real projection imports/resolves `DirectorService.get_status` | replace with projection-owned observation port, bound by bootstrap |
| `director.execution -> factory.pipeline` | stale metadata; no production Factory call | remove after source assertion; retain `factory.cognitive_runtime` |
| `factory.pipeline -> runtime.projection` | real readiness import of `build_llm_status` | retain until its LLM readiness owner/port is designed and verified |

Other reachability paths are a backlog, not permission to label the shortest
cycle fixed before they are classified.

## Execution model

1. Build a graph snapshot from `cells.yaml`; calculate SCCs and shortest BFS
   predecessor paths for each proposed cut.
2. Classify every selected edge as a real code/effect dependency, a truthful
   non-code runtime coupling, or stale metadata.  Only the final class may be
   deleted without behavior replacement.
3. For a real read dependency, move the consumer to an owner-neutral port and
   bind the concrete owner in bootstrap composition.  Ports must have typed
   unavailable/fail-closed behavior; they may not invent owner facts.
4. Add a regression for the exact cut path and a negative fixture that restores
   the old edge/path.  Do not assert that the whole SCC vanished unless the
   graph query proves it.
5. Regenerate only affected metadata and run catalog governance.  A new edge,
   a new mismatch, or an allowlist is a failed wave.

## First wave: GR1D-B1

`runtime.projection` receives a `DirectorRuntimeStatusObservationPort`.
Bootstrap resolves the concrete Director service.  The Projection Cell no
longer imports `director.execution`; a missing binding is projected as explicit
unavailability, never as a ready Director.  The proven stale
`director.execution -> factory.pipeline` declaration is removed.  Tests prove
the old shortest path cannot be reconstructed from the real catalog and can be
reconstructed from a negative two-edge fixture.

## Seal criteria

GR1D remains active until every path selected in its edge ledger has a result:
cut with behavior-preserving port, re-owned as an explicit public coupling, or
an approved future wave.  A Cell/SCC is sealed only after its catalog is
acyclic for the declared direction and N later integration batches introduce
no new unclassified back edge.

## GR1D-B1 closure evidence

- Projection-owned Director observation port replaces both direct
  `DirectorService` reads; only the bootstrap adapter imports the Director
  owner.
- Bootstrap validates exact DTO and canonical workspace identity. Async and
  sync projections both distinguish unavailable/query-failure/timeout from a
  healthy `IDLE` owner using explicit `projection_error` evidence.
- The stale `director.execution -> factory.pipeline` declaration was removed
  after a production-source assertion; `factory.cognitive_runtime` remains.
- Focused port/bootstrap/graph tests: `15 passed`; status/observability/port
  revalidation: `35 passed`; independent re-review: `CLEAR`.
- Catalog audit-only exited `0` with no new issues or manifest mismatches.

This closes only B1. It is intentionally not a statement that the 49-Cell SCC
is gone.
