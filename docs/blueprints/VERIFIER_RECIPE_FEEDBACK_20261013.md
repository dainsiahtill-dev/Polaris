# Verifier recipe feedback

The verifier-policy owner now explains rejected recipe syntax through its
existing read-only `get_verifier_command_profile_feedback` entrypoint. Previously
unknown `install`, `npm run test:check`, and `npx http-server` proposals received
an error without supported alternatives.

Rejected proposals retain their original `profile_id` and `error_code`.
`candidate_profiles` contains `candidate_profile_id`, `expected_modality`,
`accepted_shape`, and `argv_examples`. Every example is a complete argv including
the executable. Primary candidate fields also appear at the top level to support
existing feedback consumers. Existing Node test argument diagnostics remain.

Node package-manager install actions and script aliases use the same grammar
data as classification. Current native Node tests, direct Node file entrypoints,
and npx TypeScript commands provide additional alternatives. A known command
under the wrong modality identifies its actual built-in profile and modality;
this also works for non-Node built-ins. This change is a bounded Node recipe
directory, not a complete inventory of every built-in profile.

The owner resolver checks generated examples; the public feedback service then
reverse-classifies every example using the current workspace and cwd before
advertising it. Examples are advisory: `guidance_only=true` and
`execution_authority=false`. They do not create scripts, profiles, command grants,
configuration, receipts, proof, or launch leases. Runtime validation of files,
package scripts, executable identity, authorization and physical proof continues
through existing owners.

Regression coverage rejects shell execution, foreign paths, arbitrary script
names, unsupported npx wrappers and mutating lint commands after requesting
guidance. It checks actual runtime policy rejection as well as planning
classification, verifies every advertised example, and preserves successful
custom-script declarations and existing Node diagnostics.

Ownership and dependencies remain within `control_plane.verifier_policy`; no
Cell boundary, public signature, graph dependency or descriptor semantics change.
Full architecture/release and descriptor gates remain coordinator-owned after
all writer buckets stop. Cell tests and static checks establish component
evidence only; fresh isolated L1 verifier execution and independent QA are still
required for delivery.
