# GR5B — CE Leaf Construction Authority Closure

Status: accepted
Owner: `chief_engineer.blueprint`

## Problem

CE construction steps already carry target, signature, verification,
dependency, public-symbol and consumption concepts.  Two gaps prevent them
from being safe preventative contracts:

1. A leaf task copies the parent JobToken/capability token.  The parent's
   `allowed_write_paths` contains all parent targets, and Role Kernel merges
   nested scopes.  A Director leaf therefore can write a sibling despite
   declaring only one target.
2. A `consumes_symbols` provider target that does not exist is not rejected;
   a provider with no public symbols can also leave a dangling cross-file
   contract.  Later final-request evidence requirements are conditional on
   metadata that this gap can omit.

DEO physical path enforcement and `director.runtime` repair policy are not
the defect.  GR5B must narrow authority before execution, not add a late
repair rule.

## Target contract

```text
CE parent construction plan
  -> validated construction graph
  -> one leaf step / one target
  -> derived leaf JobToken (write scope = exactly target)
  -> Director read access to declared siblings only
  -> DEO path policy -> effect receipt
```

For each consumed symbol, the provider target must exist, be a code provider,
declare non-empty exact public symbols, and appear in the consumer's
`depends_on`.  Missing provider, empty provider symbols, or absent dependency
is a CE fail-closed validation error, not a Director prompt hint.

The leaf token is newly derived and content-addressed after construction
hardening.  Its lineage is bound to the parent token, leaf step identity,
target file, and construction contract hash.  It grants one write path only.
Read capability can retain
the declared dependency/sibling scope but must never be folded into write
scope by nested-token union.

## Scope

Allowed production ownership is `chief_engineer.blueprint` plus its public
contracts/tests and the `roles.adapters` deferred-repair composition bridge
that forwards the already-bound leaf authority to DEO.  `roles.kernel`/DEO
receive no semantic policy change; they are validation consumers.
`director.runtime` M10 remains a bounded late diagnostic path.  No Factory
Bench, target project, Provider or execution-broker change belongs here.

## Completion evidence

1. A nonexistent provider target, empty code-provider exports, and missing
   `depends_on` each fail before task publication.
2. Every published leaf token exposes exactly its `target_file` as write
   scope; parent write scope cannot be observed from the leaf.
3. A sibling write is rejected by the existing DEO path policy with
   `deo_path_scope_denied`; a declared sibling read remains valid.
4. Leaf lineage/hash changes when the construction contract or target changes.
5. Existing construction-step, CE consumer, Role Kernel scope, and DEO
   directed-effect lifecycle regressions pass; graph/dependency metadata
   remains unchanged except within the owning Cell.

## 2026-08-02 independent-review closure

Initial implementation passed focused tests but independent review found
authority-widening defects.  The following conditions are now closed:

1. A leaf must prove that its target write scope and dependency read scope are
   subsets of a valid, auditable parent JobToken, while binding parent content,
   contract, and blueprint hashes.  All leaves are validated before any leaf
   publication.
2. Deferred repair must take physical mutation scope only from the bound leaf
   token `allowed_write_paths`; `allowed_read_paths`, `scope_paths`, and
   arbitrary context fields are never write authority.

The deferred-repair bridge additionally requires one canonical root
`capability_token_hash`, exact matching nested copies, three byte-equal token
aliases, strict schema/audit/scope/envelope bindings, and Mapping values for
every reserved authority/envelope key at the root, `metadata`, and
`context_override` layers.  Malformed or stale projections fail before any
policy, fence, or mutation port is constructed.

Acceptance evidence: independent R6 review
`/tmp/polaris-subagent-gr5b-r6.json` is `CLEAR`; the focused CE, adapter, and
Role Kernel consumer chain reports `180 passed`, with mypy, Ruff, formatting,
and scoped diff checks green.  The actual commit path has six adversarial
negative cases with zero receipts and unchanged target bytes.  No Provider,
Factory Bench, or target-project code was used or changed for this closure.

Canonical machine-readable residual:
`docs/defects/GR5B_LEAF_AUTHORITY_WIDENING_20260802.json`.
