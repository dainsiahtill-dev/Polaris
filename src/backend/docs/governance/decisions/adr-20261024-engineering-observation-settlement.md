# ADR: separate current artifact observation from resource settlement

Status: accepted for bounded implementation under the standing user authorization. Design and runtime baseline are in POLARIS_ENGINEERING_OBSERVATION_SETTLEMENT_20261024.md and vc-20261024-engineering-observation-settlement.yaml.

Current artifact applicability and terminal/drained resource eligibility are distinct facts owned by the existing engineering journal. A large freshness-backed HTTP snapshot must not be a prerequisite to releasing an exactly bound terminal/quiescent instance. Resource closure cannot upgrade UNKNOWN acceptance. Current verification, QA and visual applicability share one real input observation within one read, and reobserve across reads. Complete synchronous IO/encoding must not starve the ASGI/NATS loop.

Reuse roles.runtime, KFS, SQLite and InstanceSupervisor. No new controller, ledger or model-authored contract. Keep host permissions, source/output freshness, cancellation/UNKNOWN, cold-version and process identity protections. A frontend stop confirmation error is retained while independently authorized residual cleanup can progress.

The owner receives a generic platform resource-settlement expectation. Internal acceptance/Bench bindings and their mode/kind/nonce rules stay in the adapter; they are not Supervisor business semantics or prerequisites for ordinary project settlement.

L213 stays native-completed but readside-timeout/ROOT-assisted, even after a later successful version. QA prompt changes and blanket zombie success are not decisions made by this ADR.
