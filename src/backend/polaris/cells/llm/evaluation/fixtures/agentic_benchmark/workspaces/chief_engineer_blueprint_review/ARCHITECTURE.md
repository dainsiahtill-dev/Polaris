# Architecture Brief

Current state:
- evaluation logic is split between readiness checks and ad-hoc scripts
- report persistence is inconsistent

Target state:
- one deterministic benchmark runner
- one judge implementation
- one rollback plan for benchmark fixture migrations

Required blueprint elements:
- phased rollout
- key risks
- rollback plan
