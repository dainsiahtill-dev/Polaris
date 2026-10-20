# Current-manifest package preparation proof

R05 exposed a policy denial before the declared test recipe could execute:
`npm run build && node --test dist/tests/domain.test.js`, where the same
manifest declares `build: tsc -p tsconfig.json`.

Data flow: current manifest → literal preparation reference → existing
build/lint proof grammar → existing test grammar → current policy hash →
ExecutionBroker revalidation → actual execution and proof.

The verifier-policy Cell continues to own grammar decisions; it gains no
execution authority or state. Resolve only literal package-manager references
used for build/lint preparation within test recipes. Other modalities retain
their prior grammar and file-content checks. Reject missing/cyclic references, options,
shell substitutions and unknown/non-proving leaves. The existing whole-manifest
digest binds nested bodies without a new cache, ledger or capability.

Private public-policy fixtures substitute executable identity and policy
storage only. They prove admission and binding behavior, not execution or
delivery. Current Node grammar and missing-entrypoint feedback are regression
checks. Final acceptance still requires a fresh frozen native delivery.
