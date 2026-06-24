# CE Architecture Decisions + Director Execution Profile Blueprint

Date: 2026-06-25
Status: Implementation blueprint
Scope: Chief Engineer architecture decisions, Director execution profile, prompt construction, sampling metadata, and audit evidence

## Problem

Director code-generation prompts previously inferred a primary language mostly
from target file extensions and injected one broad language block. That is too
flat for LLM execution. A Go concurrent service, Python bug repair, TypeScript
React component, shell automation script, and SQL migration must receive
different concrete Best Practices.

The deeper architecture problem is classification drift. PM, TaskMarket, Chief
Engineer, Director tasking, prompt guidance, and LLM sampling can each infer
task type from different loose dictionaries. If prompt guidance decides a task
is `bugfix` while the runtime still uses a generic/default temperature, the
final provider request becomes non-auditable and fragile.

The prompt layer therefore needs a maintainable `language x framework x task
type x file role` matrix, backed by one execution profile that also drives
temperature and final-request audit metadata.

Chief Engineer also needs to make explicit architecture choices before Director
implementation. Complex tasks are not just "write code" tasks: they may need a
project structure pattern, dependency-injection boundary, persistence strategy,
realtime transport, queue/stream, cache, auth, observability, or object-storage
decision. These choices must be structured metadata, not prose hidden in a
blueprint summary.

## Brainstormed Options

1. Expand every language block into a very large static prompt.
   - Pros: simple.
   - Cons: prompt bloat, weak task fit, high truncation risk.

2. Add one prompt template per language and per framework.
   - Pros: precise for common stacks.
   - Cons: many templates, hard to maintain, framework coverage drifts.

3. Compose Best Practices from small orthogonal layers.
   - Language profile: expert identity plus idioms, error handling,
     concurrency, design, performance, and test rules.
   - Framework profile: stack-specific rules when metadata/text identifies one.
   - Task-type profile: write code, refactor, code review, bug fix, tests,
     API, frontend, CLI, security, performance, database, DevOps, docs, etc.
   - File-role profile: source, test, config, script, schema, style, docs.
   - Universal production rules and the existing Director output contract.

## Decision

Use option 3, with structured Chief Engineer architecture decisions and a
canonical Director execution profile.

This preserves the existing Director output protocol while making the model see
a sharper expert role and task-specific Best Practices. `PromptBuilder` remains
only an assembler. Classification is resolved once by
`TaskExecutionProfileV1`, and language/framework/task/file-role guidance is
rendered from that resolved profile.

The current implementation places the profile in `director.tasking` as a
transition contract. Long term, the same schema should move upward into a shared
PM/CE/Director metadata contract so upstream phases produce canonical metadata
instead of relying on Director fallback inference.

Chief Engineer blueprints now add `ArchitectureDecisionV1` records. A record
contains `concern`, `decision`, `selected_libraries`, `options_considered`,
`rationale`, `constraints`, `risk_level`, `decision_status`, `source`, and
`evidence`. Explicit CE/LLM records are decisions. Platform-inferred records are
guidance only (`decision_status=guidance`,
`source=platform_signal_guidance`, `evidence.guidance_only=true`): they tell
CE/Director what to evaluate, not what to choose. Guidance records must keep
`selected_libraries=[]`; only explicit CE/LLM/user/project-document decisions
may fill selected architecture patterns or third-party libraries. Director
consumes these records in the PM/CE contract context; it does not maintain a
parallel architecture-selector.

## Architecture

```text
PM task contract
        |
        v
Chief Engineer blueprint/handoff
        |
        +--> optional strong-model architecture advisor
        |    + reads PM contract, project docs, existing dependencies, code map,
        |      target files, runtime constraints, and verification requirements
        |    + emits ArchitectureDecisionV1 records with decision_status
        |      proposed/accepted/rejected, never unstructured prose only
        |    + platform validates schema/evidence/scope before Director sees it
        |
        +--> ArchitectureDecisionV1[]
        |    + application_architecture: Layered/MVC/MVVM/Clean/Hexagonal/DDD/
        |      Repository/Service/Feature-Sliced/Event-Driven/CQRS/
        |      Modular Monolith/Microservices/Micro-Frontend
        |    + realtime: WebSocket/SSE/event streams/message brokers/managed realtime
        |    + database: relational/embedded/document/KV/search/graph/analytics/
        |      time-series/vector/object-storage-backed/managed database families
        |    + async_work/cache/auth/observability/object_storage
        |
        v
Task metadata + subject + description + target/scope paths
        |
        v
execution_profile.resolve_director_execution_profile(...)
        |
        +--> dispatch_type compatibility: bootstrap/file_creation/code_generation/generic
        +--> task_type/project_type/phase
        +--> language/framework/task_foci/file_roles
        +--> temperature_phase/temperature
        +--> output_contract_id
        +--> signal_evidence
        |
        +-----------------------------+
        |                             |
        v                             v
language_guidance.build_language_section(...)    code_generation_engine RoleRuntime context
        |
        +--> language best-practices profile
        +--> framework best-practices profile
        +--> task-type best-practices profiles
        +--> file-role best-practices profiles
        +--> universal production best practices
        |
        v
PromptBuilder.build_code_generation_prompt(...)
        |
        +--> PM/CE contract context
        +--> CE architecture guidance/decisions and selected libraries
        +--> compress variable body
        +--> append protected output-contract suffix
        |
        v
existing patch/file output contract
```

Runtime code-generation calls pass the same execution profile into
`RoleRuntimeService` context and metadata. Director sampling uses the existing
transaction-kernel override channel (`_transaction_kernel_temperature_override`)
so the final provider request receives a precise temperature even when lower
KernelOne role/phase fallback logic is bypassed.

## Implementation Plan

1. Extend `language_guidance.py` with structured prompt context detection and
   typed profile registries.
2. Add `TaskExecutionProfileV1` as the canonical task execution profile
   contract, with `DirectorExecutionProfileV1` kept only as a compatibility
   alias for existing Director imports.
3. Add `execution_profile.resolve_director_execution_profile(...)` and make
   legacy `task_classifier.py` delegate to it.
4. Keep `build_language_section(...) -> tuple[str, str]` backward compatible,
   but feed it profile-resolved metadata from `PromptBuilder`.
5. Protect the patch/file output contract from prompt truncation by compressing
   only the variable prompt body and appending a fixed suffix.
6. Pass `director_execution_profile`, `task_type`, `phase`,
   `temperature_phase`, and `_transaction_kernel_temperature_override` through
   the Director runtime code-generation bridge.
7. Add final-request audit sampling metadata: final temperature,
   temperature source, phase, task type, and execution profile schema.
8. Add Chief Engineer `ArchitectureDecisionV1` and guidance inference for
   application architecture, realtime, database, queue/async work, cache, auth,
   observability, and object storage. Guidance inference produces evaluation
   dimensions and candidate families; it must not make final architecture or
   library choices.
9. Persist architecture decisions in CE blueprints, include them in TaskMarket
   handoff metadata, preserve them when CE fissions a parent task into leaf
   Director tasks, and render them in Director prompts.
10. When a stronger platform model is available, use it inside the CE blueprint
    phase as an architecture advisor/reviewer. It should convert the evidence
    package into structured `ArchitectureDecisionV1` proposals or accepted
    decisions, not bypass the contract or write directly to Director prompts.

## Maintenance Rules

- Language profiles must contain concrete Best Practices, not broad quality
  adjectives. Each profile should cover idioms/style, error handling,
  concurrency/async where applicable, design, performance, and testing.
- Task-type profiles must describe how the model should act for write-code,
  refactor, review, bugfix, and test tasks. Domain profiles such as API,
  frontend, database, DevOps, and security are additive.
- Prompt output must keep the existing patch/file contract intact. Best
  Practices must never ask Director to output markdown reports, shell commands,
  or status narration in code-generation responses. The output contract is a
  protected suffix and must not be included in any prompt body truncation.
- Profile detection must use metadata first, then task text and paths.
  Ambiguous signals should compose multiple profiles rather than silently
  dropping the base write-code profile.
- No new task-type classifier may be added in PM, CE, Director, prompt guidance,
  or KernelOne sampling without consuming or extending the canonical execution
  profile schema.
- Temperature must be a profile-derived sampling decision. Director codegen
  uses the existing transaction-kernel override channel as the enforcement path;
  KernelOne role/phase fallback is only a last-mile fallback, not a second source
  of truth.
- Final request audit must expose sampling metadata so ContextOS can verify
  which temperature and profile drove the actual provider request.
- Explicit CE/LLM architecture decisions are the upstream source of truth for
  architecture patterns and third-party library direction. Inferred guidance is
  not a final decision and must keep `selected_libraries` empty. Director prompt
  code may render these records, but must not reimplement a second architecture
  selector.
- Stronger platform LLM/AGI assistance belongs in CE, not Director. It may
  propose or accept architecture choices after reading the task contract,
  project documentation, dependency manifests, target files, code map, runtime
  constraints, quality gates, and current ecosystem context. The platform must
  still validate schema, evidence, scope, risk, and selected libraries before
  handoff. If evidence is insufficient, the correct output is a guidance record
  or an explicit uncertainty/risk, not a fabricated decision.
- Complex/medium-large projects should receive an explicit
  `application_architecture` decision when task signals warrant it. This is a
  candidate pool, not a mandatory template. CE must select from actual task
  contract, project documents, target files, existing stack, and product
  requirements. Candidate options include Layered Architecture, MVC, MVVM, Clean Architecture,
  Hexagonal Architecture, DDD, Repository Pattern, Service Layer,
  Feature-Sliced Design, Event-Driven Architecture, CQRS, Modular Monolith,
  Microservices, and Micro-Frontend.
- Database guidance should be a broad evaluation family, not four hard-coded
  products. Consider relational OLTP, embedded/local, document, key-value or
  wide-column, search, graph, analytical/columnar, time-series, vector search,
  object-storage-backed designs, and managed cloud database options according
  to actual data shape, query patterns, transactions, operations, and existing
  stack.
- Do not force architecture patterns by name. MVC/MVVM require UI,
  controller/view, screen, or mobile-app signals. Microservices require clear
  independent deployment/team ownership/service-boundary signals. CQRS requires
  materially different read/write models or audit/event sourcing needs. For a
  small CLI/script/library change, prefer minimal module boundaries and
  dependency injection seams over heavyweight architecture.
- Dependency Injection is a first-class architecture constraint for complex
  projects: dependencies should be passed through constructors/functions or
  framework-native containers, not hidden in global state.
- Realtime decisions must keep Polaris platform policy separate from generic
  target-project design. Polaris runtime/UI product paths remain NATS JetStream
  + `/v2/ws/runtime`; SSE/polling are only options considered for target
  projects when policy allows them and must not be introduced as Polaris
  product realtime fallbacks.

## Long-Term Migration

The next architectural step is to introduce a shared `TaskCanonicalMetadataV1`
contract above Director:

```text
PM normalized task
    -> TaskMarket pending_design payload
    -> Chief Engineer blueprint/handoff
    -> TaskExecutionProfileV1
```

PM should generate `task_type`, `project_type`, `phase`, `tech_stack`,
`scope_paths`, `target_files`, `acceptance_criteria`, `quality_gates`, and
`verification_commands` in a canonical metadata object. CE should preserve and
refine that object, including `architecture_decisions` and
`selected_libraries`, instead of flattening selected fields into another
payload.
Director should eventually treat fallback inference as compatibility mode only,
with warnings in `signal_evidence`.

## Verification Plan

- `ruff check src/backend/polaris/cells/director/tasking/internal/language_guidance.py src/backend/polaris/cells/director/tasking/internal/execution_profile.py src/backend/polaris/cells/director/tasking/internal/task_classifier.py src/backend/polaris/cells/director/tasking/internal/prompt_builder.py src/backend/polaris/cells/director/tasking/internal/code_generation_engine.py src/backend/polaris/cells/director/tasking/public/contracts.py src/backend/polaris/cells/roles/kernel/internal/llm_caller/caller.py src/backend/polaris/cells/roles/kernel/internal/llm_caller/context_audit.py --fix`
- `ruff format src/backend/polaris/cells/director/tasking/internal/language_guidance.py src/backend/polaris/cells/director/tasking/internal/execution_profile.py src/backend/polaris/cells/director/tasking/internal/task_classifier.py src/backend/polaris/cells/director/tasking/internal/prompt_builder.py src/backend/polaris/cells/director/tasking/internal/code_generation_engine.py src/backend/polaris/cells/director/tasking/public/contracts.py src/backend/polaris/cells/director/tasking/tests/test_prompt_builder.py src/backend/polaris/cells/director/tasking/tests/test_language_guidance.py src/backend/polaris/cells/director/tasking/tests/test_code_generation_engine_profile.py src/backend/polaris/cells/roles/kernel/tests/test_final_request_sampling_audit.py`
- `mypy src/backend/polaris/cells/director/tasking/internal/language_guidance.py src/backend/polaris/cells/director/tasking/internal/execution_profile.py src/backend/polaris/cells/director/tasking/internal/task_classifier.py src/backend/polaris/cells/director/tasking/internal/prompt_builder.py src/backend/polaris/cells/director/tasking/public/contracts.py`
- `pytest src/backend/polaris/cells/director/tasking/tests/test_prompt_builder.py src/backend/polaris/cells/director/tasking/tests/test_language_guidance.py src/backend/polaris/cells/director/tasking/tests/test_code_generation_engine_profile.py src/backend/polaris/cells/roles/kernel/tests/test_final_request_sampling_audit.py -q`
