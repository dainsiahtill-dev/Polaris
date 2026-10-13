# Project verifier source and runtime input closure

Purpose: allow an exact CE build recipe to create its declared runtime output while retaining owner-bound input, path, policy and receipt checks. This is a bounded correction in `factory.verification_guard`; it adds no grant, engine or public contract.

Flow: immutable CE completion contract; VerificationGuard physical intent; bootstrap owner adapter; execution_broker public authority; KernelOne sandbox/process/effect receipt; later exact entrypoint probe.

`_command_inputs` owns input selection. Covering an entrypoint always binds its source. Coverage alone does not make the entrypoint runtime output an input to build, lint, test or environment preparation. An entrypoint probe binds runtime bytes at its own admission. A non-entrypoint command that explicitly names the declared runtime path in canonical argv also retains that input, with lexical cwd normalization only. Explicit artifact dependencies and the whole existing source/manifest/config/test/docs/assets closure remain bound. No path is removed based on its current existence.

Execution Broker remains unchanged: missing true inputs, foreign paths and parent symlinks fail closed; exact executable, command authority, capability and hash checks remain authoritative. No output directories or target files are created by this correction. Actual build failure, entrypoint mismatch and test failure remain real failed evidence.

Native18 evidence: contract `202aa465c90badd8ff9d14b03b35b891bab339325e04f5077bfc422e7ca93d5a`; `VER-BUILD` covers `ENT-CLI-RUN`, whose source is `src/cli/run.ts` and runtime is `dist/cli/run.js`. The broker's strict snapshot rejects missing `dist/cli` before reservation/spawn. ENV has an authenticated exit-0 receipt; the separate native quality gate previously recorded build exit 0, 23/24 tests passing and start MODULE_NOT_FOUND. These are separate evidence claims.

Verification: RED reproduces missing dist parent for four source modalities. GREEN retains source closure, actual runtime references, entrypoint missing-file/hash checks and foreign/symlink rejection. Run owner/bootstrap/broker regression collections, Ruff and Mypy. Private real broker build followed by runtime entrypoint provides component evidence only. Fresh isolated L1-01 and terminal namespace release remain coordinator-owned.

Limits: exact argv references are conservative input pins; a declared runtime path used as an explicit output operand can still fail closed. Indirect script dependencies require explicit contract artifact inputs. Descriptor semantics, graph edges, public contracts, state owners and effect capabilities are unchanged.
