"""Architecture-decision guidance inference for Chief Engineer blueprints.

The CE LLM owns final architecture and dependency choices. This module only
detects contract signals and turns them into structured review prompts:
which concerns to evaluate, which options to compare, and which constraints to
respect. Explicit CE/LLM decisions are preserved ahead of inferred guidance.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ArchitectureDecisionV1,
)

_MAX_TEXT_CHARS = 16000

APPLICATION_ARCHITECTURE_OPTIONS = (
    "Layered architecture or a lighter module-boundary alternative when the project needs separation without heavy domain modeling",
    "Clean / Hexagonal / Ports-and-Adapters style boundaries when long-lived core logic must stay independent from frameworks and I/O",
    "DDD or bounded-context modeling only when domain complexity, invariants, and ubiquitous-language needs justify it",
    "Repository / service / application-service boundaries when persistence or external I/O must remain swappable and testable",
    "Framework-native component architecture for modern frontend work, with feature/module boundaries that fit the existing stack",
    "Feature-sliced or similar frontend organization when product areas are large enough to need explicit ownership boundaries",
    "Unidirectional data flow, state machines, or framework-native state patterns when UI state coordination is the main risk",
    "Framework-native MVC only when the existing server-side framework and project shape are actually MVC",
    "Mobile MVVM / ViewModel-style separation only when the UI stack and platform conventions make it a natural fit",
    "Event-driven architecture or a simpler callback/job model depending on coupling, durability, replay, and operational needs",
    "CQRS / event sourcing only when read/write models, audit trails, or replay requirements clearly justify the complexity",
    "Modular monolith before microservices unless independent deployment, ownership, scaling, and data boundaries are explicit",
    "Micro-frontend or a simpler shared-shell/module approach only when frontend teams need independent deployment governance",
    "Dependency Injection / Inversion of Control or a framework-native equivalent when dependencies must be explicit and testable",
    "A newer, simpler, or more ecosystem-current alternative when it better fits the project documents and current stack",
)

REALTIME_OPTIONS = (
    "Bidirectional realtime transport for collaborative or interactive clients when both directions matter",
    "One-way server-to-client streaming when the client only observes progress or events",
    "Durable event streaming when replay, fan-out, ordered consumption, or worker coordination is required",
    "Message-broker fan-out when delivery guarantees and decoupled consumers matter more than browser transport",
    "Managed realtime infrastructure when the target project already standardizes on it and operational ownership is acceptable",
    "Explicit refresh or polling only when the product is not truly realtime or compatibility constraints require it",
    "A newer or more mature transport/library that better fits current ecosystem and project constraints",
)

DATABASE_OPTIONS = (
    "Relational OLTP family when transactions, relational integrity, migrations, and ad hoc querying dominate",
    "Embedded/local family when the project is local-first, single-node, test-heavy, or operationally lightweight",
    "Document-oriented family when document shape, flexible schema, and aggregate reads are real requirements",
    "Key-value or wide-column family when access is mostly lookup/write-heavy and relational joins are not central",
    "Search-index family when ranking, full-text search, faceting, or relevance tuning is a primary requirement",
    "Graph family when relationship traversal is the core query pattern rather than an incidental join",
    "Analytical/columnar family when reporting, aggregation, or OLAP-style workloads dominate",
    "Time-series family when retention windows, downsampling, and time-window queries dominate",
    "Vector-search family when semantic retrieval or nearest-neighbor search is a first-class requirement",
    "Object/blob storage plus metadata database when large artifacts dominate and transactional metadata is separate",
    "Managed cloud database family when operations, scaling, backups, and compliance favor managed ownership",
    "The existing project database or a newer ecosystem-current alternative when it better fits the documented constraints",
)

ASYNC_WORK_OPTIONS = (
    "Durable event streaming when replay, fan-out, stream processing, or ordered consumption matters",
    "Message broker queues when routing, acknowledgements, retries, and dead-letter handling are central",
    "Cloud task queues when the deployment platform already provides a mature managed primitive",
    "Application-level job framework when it is the ecosystem-standard and fits the current runtime",
    "In-process queues only for disposable work that may be lost on restart",
    "A simpler or more mature queue/stream alternative when it better matches operational constraints",
)

CACHE_OPTIONS = (
    "Bounded in-process cache when data is local, disposable, and memory limits are clear",
    "Shared cache family when multiple workers need common cached state, sessions, or rate-limit counters",
    "CDN or edge cache family when public read-heavy assets or responses dominate",
    "Database/materialized-view cache when transactional freshness and query planning matter",
    "No cache when invalidation complexity outweighs measured performance benefit",
)

AUTH_OPTIONS = (
    "Session-based auth when server-side session control and browser security are a natural fit",
    "Token-based auth only with explicit rotation, revocation, audience, and expiry strategy",
    "OAuth2 / OpenID Connect family when external identity providers or delegated login are required",
    "Enterprise SSO family when organization identity integration is a documented requirement",
    "Framework-native auth when the existing stack already standardizes on it",
    "External identity-provider service when operational ownership and compliance requirements favor it",
    "A newer or more mature auth approach when it better matches the current ecosystem and threat model",
)

OBSERVABILITY_OPTIONS = (
    "Structured logging with correlation IDs when debugging and auditability are primary needs",
    "Distributed tracing when requests cross services, queues, workers, or external providers",
    "Metrics when SLOs, capacity, latency, throughput, or error-budget tracking are needed",
    "Audit logs when security, compliance, or irreversible actions must be explainable",
    "Error tracking when user-facing failures need triage and release correlation",
    "Provider-native telemetry when the deployment platform already has mature observability",
    "A newer or more mature observability stack when it better fits the runtime and organization",
)

OBJECT_STORAGE_OPTIONS = (
    "Local filesystem only for development, ephemeral artifacts, or explicitly single-node tools",
    "Object-storage family when durable user uploads, large artifacts, retention, or CDN access matter",
    "Cloud blob-storage family when the deployment platform already owns storage and access controls",
    "Database blobs only for small transactional payloads with clear size and retention limits",
    "Artifact/package registry family for build outputs, releases, or dependency artifacts",
    "A newer or more operationally suitable storage service when it better fits compliance and deployment constraints",
)


def normalize_architecture_decisions(value: Any) -> tuple[ArchitectureDecisionV1, ...]:
    """Normalize persisted/user-provided decision payloads."""

    if isinstance(value, ArchitectureDecisionV1):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()

    decisions: list[ArchitectureDecisionV1] = []
    for item in value:
        try:
            if isinstance(item, ArchitectureDecisionV1):
                decisions.append(item)
            elif isinstance(item, Mapping):
                decisions.append(ArchitectureDecisionV1.from_mapping(item))
        except ValueError:
            continue
    return tuple(decisions)


def merge_architecture_decisions(
    explicit: tuple[ArchitectureDecisionV1, ...],
    inferred: tuple[ArchitectureDecisionV1, ...],
) -> tuple[ArchitectureDecisionV1, ...]:
    """Merge decisions by concern, preserving explicit CE/model choices first."""

    merged: list[ArchitectureDecisionV1] = []
    seen: set[str] = set()
    for decision in (*explicit, *inferred):
        concern = decision.concern.strip().lower()
        if not concern or concern in seen:
            continue
        seen.add(concern)
        merged.append(decision)
    return tuple(merged)


def selected_libraries_from_decisions(
    decisions: tuple[ArchitectureDecisionV1, ...],
) -> tuple[str, ...]:
    """Flatten selected libraries/technologies while preserving order."""

    rows: list[str] = []
    seen: set[str] = set()
    for decision in decisions:
        for library in decision.selected_libraries:
            key = library.strip().lower()
            if key and key not in seen:
                seen.add(key)
                rows.append(library)
    return tuple(rows)


def infer_architecture_decisions(
    *,
    objective: str,
    context: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
    target_files: list[str] | tuple[str, ...] = (),
    scope_paths: list[str] | tuple[str, ...] = (),
    dependencies: list[str] | tuple[str, ...] = (),
) -> tuple[ArchitectureDecisionV1, ...]:
    """Infer CE architecture/dependency decision guidance from task signals."""

    text = _collect_signal_text(
        objective=objective,
        context=context,
        constraints=constraints,
        target_files=target_files,
        scope_paths=scope_paths,
        dependencies=dependencies,
    )
    decisions: list[ArchitectureDecisionV1] = []

    complexity = _match_any(
        text,
        (
            "large project",
            "medium project",
            "enterprise",
            "complex",
            "multi module",
            "microservice",
            "service layer",
            "frontend",
            "clean architecture",
            "hexagonal",
            "ports and adapters",
            "ddd",
            "domain driven",
            "dependency injection",
            "repository",
            "cqrs",
            "event driven",
            "event-driven",
            "modular monolith",
            "micro frontend",
            "中大型",
            "复杂项目",
            "大型项目",
            "分层",
            "依赖注入",
            "领域驱动",
            "仓储",
            "事件驱动",
            "模块化单体",
            "微服务",
        ),
    )
    ui_arch_evidence = (
        _match_any(
            text,
            (
                "mvc",
                "mvvm",
                "view",
                "controller",
                "component",
                "frontend",
                "mobile",
                "ui",
                "screen",
                "page",
                "react",
                "vue",
                "angular",
                "swiftui",
                "compose",
                "界面",
                "前端",
                "页面",
                "组件",
                "移动端",
            ),
        )
        if _has_ui_signal(text)
        else ()
    )
    frontend_evidence = ("modern_frontend",) if _has_modern_frontend_signal(text) else ()
    mobile_evidence = ("mvvm",) if _has_mobile_mvvm_signal(text) else ()
    server_mvc_evidence = ("mvc",) if _has_server_mvc_signal(text) else ()
    architecture_evidence = (
        complexity or ui_arch_evidence or frontend_evidence or mobile_evidence or server_mvc_evidence
    )
    if architecture_evidence:
        decisions.append(_application_architecture_decision(text, architecture_evidence))

    realtime = _match_any(
        text,
        (
            "realtime",
            "real-time",
            "real time",
            "live update",
            "live progress",
            "websocket",
            "web socket",
            "sse",
            "server-sent",
            "eventsource",
            "nats",
            "jetstream",
            "push notification",
            "streaming",
            "pubsub",
            "pub/sub",
            "实时",
            "推送",
            "长连接",
            "事件流",
            "进度流",
        ),
    )
    if realtime:
        decisions.append(_realtime_decision(text, realtime))

    database = _match_any(
        text,
        (
            "database",
            "db",
            "sql",
            "sqlite",
            "mysql",
            "postgres",
            "postgresql",
            "mongodb",
            "mongo",
            "persistence",
            "persist",
            "transaction",
            "acid",
            "orm",
            "migration",
            "数据库",
            "持久化",
            "事务",
            "数据表",
        ),
    )
    if database:
        decisions.append(_database_decision(text, database))

    queue = _match_any(
        text,
        (
            "queue",
            "job queue",
            "worker",
            "background job",
            "task queue",
            "rabbitmq",
            "kafka",
            "redis streams",
            "celery",
            "异步任务",
            "后台任务",
            "队列",
            "消息队列",
        ),
    )
    if queue and not _has_concern(decisions, "realtime"):
        decisions.append(_queue_decision(queue))

    cache = _match_any(
        text,
        (
            "cache",
            "redis",
            "memoize",
            "rate limit",
            "session store",
            "缓存",
            "限流",
            "会话存储",
        ),
    )
    if cache:
        decisions.append(_cache_decision(cache))

    auth = _match_any(
        text,
        (
            "auth",
            "authentication",
            "authorization",
            "oauth",
            "oidc",
            "jwt",
            "login",
            "permission",
            "rbac",
            "鉴权",
            "认证",
            "授权",
            "登录",
            "权限",
        ),
    )
    if auth:
        decisions.append(_auth_decision(auth))

    observability = _match_any(
        text,
        (
            "observability",
            "opentelemetry",
            "metrics",
            "tracing",
            "trace",
            "structured logging",
            "audit log",
            "telemetry",
            "可观测",
            "指标",
            "链路追踪",
            "审计日志",
        ),
    )
    if observability:
        decisions.append(_observability_decision(observability))

    storage = _match_any(
        text,
        (
            "object storage",
            "s3",
            "minio",
            "upload",
            "file storage",
            "blob",
            "artifact",
            "对象存储",
            "文件上传",
            "附件",
        ),
    )
    if storage:
        decisions.append(_storage_decision(storage))

    return tuple(decisions)


def _collect_signal_text(
    *,
    objective: str,
    context: Mapping[str, Any] | None,
    constraints: Mapping[str, Any] | None,
    target_files: list[str] | tuple[str, ...],
    scope_paths: list[str] | tuple[str, ...],
    dependencies: list[str] | tuple[str, ...],
) -> str:
    chunks = [
        str(objective or ""),
        _flatten_mapping(context or {}),
        _flatten_mapping(constraints or {}),
        " ".join(str(item) for item in target_files),
        " ".join(str(item) for item in scope_paths),
        " ".join(str(item) for item in dependencies),
    ]
    return " ".join(chunks).lower()[:_MAX_TEXT_CHARS]


def _flatten_mapping(value: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    for key, item in value.items():
        chunks.append(str(key))
        if isinstance(item, Mapping):
            chunks.append(_flatten_mapping(item))
        elif isinstance(item, (list, tuple, set)):
            chunks.extend(_flatten_value(child) for child in item)
        else:
            chunks.append(str(item))
    return " ".join(chunk for chunk in chunks if chunk)


def _flatten_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return _flatten_mapping(value)
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_value(item) for item in value)
    return str(value)


def _match_any(text: str, keywords: tuple[str, ...]) -> tuple[str, ...]:
    matched: list[str] = []
    for keyword in keywords:
        pattern = re.escape(keyword.lower())
        if re.search(rf"(?<![a-z0-9_]){pattern}(?![a-z0-9_])", text) or keyword in text:
            matched.append(keyword)
    return tuple(matched[:8])


def _contains(text: str, *keywords: str) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _has_concern(
    decisions: list[ArchitectureDecisionV1],
    concern: str,
) -> bool:
    expected = concern.strip().lower()
    return any(decision.concern.strip().lower() == expected for decision in decisions)


def _has_ui_signal(text: str) -> bool:
    if _contains(
        text,
        "no ui",
        "without ui",
        "headless",
        "non-ui",
        "no frontend",
        "without frontend",
        "无界面",
        "没有界面",
        "无需界面",
        "无前端",
    ):
        return False
    return _contains(
        text,
        "frontend",
        "ui",
        "view",
        "controller",
        "screen",
        "page",
        "component",
        "react",
        "vue",
        "angular",
        "swiftui",
        "compose",
        "ios",
        "android",
        "mobile",
        "界面",
        "前端",
        "页面",
        "组件",
        "移动端",
    )


def _has_modern_frontend_signal(text: str) -> bool:
    return _contains(
        text,
        "react",
        "vue",
        "angular",
        "svelte",
        "next.js",
        "nextjs",
        "nuxt",
        "component",
        "composition api",
        "hooks",
        "state management",
        "frontend",
        "前端",
        "组件",
    )


def _has_mobile_mvvm_signal(text: str) -> bool:
    if _has_modern_frontend_signal(text):
        return False
    return _contains(
        text,
        "mvvm",
        "viewmodel",
        "view model",
        "mobile",
        "ios",
        "android",
        "swiftui",
        "compose",
        "移动端",
    )


def _has_server_mvc_signal(text: str) -> bool:
    if _has_modern_frontend_signal(text):
        return False
    return _contains(
        text,
        "mvc",
        "rails",
        "django",
        "spring mvc",
        "asp.net mvc",
        "laravel",
        "server-rendered",
        "服务端渲染",
    )


def _application_architecture_decision(
    text: str,
    evidence: tuple[str, ...],
) -> ArchitectureDecisionV1:
    options: tuple[str, ...]
    if _contains(text, "micro frontend", "micro-frontend", "微前端"):
        decision = "Evaluate whether the task truly needs micro-frontends; prefer the existing frontend framework's component/module boundaries unless independent deployment and shared-shell governance are explicit."
        options = APPLICATION_ARCHITECTURE_OPTIONS
    elif _has_modern_frontend_signal(text):
        decision = "Evaluate the existing frontend framework conventions and choose component/module boundaries, state-management placement, and service/client injection that fit the project."
        options = (
            "Framework-native component architecture or a newer ecosystem-current equivalent",
            "Feature/module slicing only when product areas are large enough to justify it",
            "Framework-native state management, state machines, or simpler local state depending on coordination risk",
            "Dependency injection or explicit client/service boundaries for I/O",
        )
    elif _has_mobile_mvvm_signal(text):
        decision = "Evaluate whether MVVM/ViewModel separation fits the mobile or explicitly ViewModel-oriented UI task; do not apply it to unrelated frontend or backend work."
        options = (
            "Mobile MVVM / ViewModel separation when platform conventions make it a natural fit",
            "Unidirectional data flow or state-machine alternatives when they model UI state more clearly",
            "Clean architecture boundaries only when the mobile feature is large enough to justify them",
            "Dependency injection or platform-native service injection for testable dependencies",
        )
    elif _has_server_mvc_signal(text):
        decision = "Evaluate whether the existing server-side web framework is actually MVC-style; keep business logic out of controllers/views if MVC is selected."
        options = (
            "Framework-native MVC only when the existing server-side stack is MVC",
            "Service layer or application-service boundary for business orchestration",
            "Clean / Hexagonal alternative when framework-independent core logic matters more",
            "Dependency injection or framework-native container for external dependencies",
        )
    elif _contains(text, "ddd", "domain driven", "领域驱动"):
        decision = (
            "Evaluate DDD-style bounded contexts only if the domain model and invariants justify the extra structure."
        )
        options = (
            "DDD / bounded contexts when domain language and invariants justify it",
            "Repository or persistence-port boundary when storage must be swappable/testable",
            "Application service boundary for use-case orchestration",
            "Simpler layered modules when DDD would overfit the problem",
        )
    elif _contains(text, "hexagonal", "ports and adapters", "clean architecture"):
        decision = "Evaluate Clean/Hexagonal boundaries for I/O-heavy or long-lived modules, preserving framework-independent core logic where useful."
        options = (
            "Clean / Hexagonal / Ports-and-Adapters boundary when I/O isolation is valuable",
            "Simpler layered boundary when ports/adapters would be unnecessary ceremony",
            "Framework-native dependency injection or explicit constructor injection",
            "Adapter contracts only where tests or future replacement need them",
        )
    elif _contains(text, "cqrs", "event driven", "event-driven", "事件驱动"):
        decision = "Evaluate event-driven architecture and CQRS only when asynchronous boundaries, audit trails, or materially different read/write models exist."
        options = (
            "Event-driven architecture when asynchronous boundaries or replay are genuine requirements",
            "CQRS only when read/write models materially diverge",
            "Outbox / inbox / idempotency patterns when durability across storage and events matters",
            "Synchronous request/response when it is simpler and sufficient",
        )
    elif _contains(text, "microservice", "微服务"):
        decision = "Evaluate modular monolith versus microservices from deployment, ownership, data-boundary, and observability requirements; do not split services by default."
        options = (
            "Modular monolith when one deployable with explicit module boundaries is enough",
            "Microservices only with explicit ownership, deployment, scaling, and data boundaries",
            "Service contracts and compatibility testing when boundaries cross processes",
            "Shared-library/module extraction when independent deployment is not required",
        )
    else:
        decision = "Evaluate the lightest architecture that keeps the project maintainable: module boundaries, dependency injection points, and separation of I/O from core logic."
        options = (
            "Lightweight layered architecture or module boundaries",
            "Clean / Hexagonal style only when I/O independence is worth the extra structure",
            "Repository/service boundary only when persistence or external I/O requires it",
            "Dependency injection or explicit parameter passing for testable dependencies",
        )
    return ArchitectureDecisionV1(
        concern="application_architecture",
        decision=decision,
        selected_libraries=(),
        options_considered=options,
        rationale=(
            "Complex or medium-to-large tasks may need stable module boundaries, but CE must choose the pattern from actual requirements and existing project conventions."
        ),
        constraints=(
            "Select architecture from actual task contract, project documents, target files, existing framework, and product requirements; do not force MVC/MVVM/microservices when the project has no matching UI, controller, deployment, or domain boundary.",
            "For React/Vue/Angular/Svelte/Next/Nuxt pages, prefer the framework's component/state-management architecture instead of imposing MVC or MVVM.",
            "Inject dependencies through constructors/functions instead of hidden globals.",
            "Keep controllers/routes/views thin; place orchestration in application services.",
            "Keep core/domain logic independent from frameworks, persistence, network, and UI concerns when the project size justifies it.",
            "Do not introduce heavyweight patterns for one-file or trivial scripts.",
        ),
        risk_level="medium",
        evidence={"guidance_only": True, "matched_keywords": list(evidence)},
        decision_status="guidance",
        source="platform_signal_guidance",
    )


def _realtime_decision(
    text: str,
    evidence: tuple[str, ...],
) -> ArchitectureDecisionV1:
    if _contains(text, "nats", "jetstream", "polaris", "runtime.v2", "/v2/ws/runtime"):
        decision = "Evaluate realtime transport needs; for Polaris runtime/UI product paths, preserve the existing NATS JetStream + WebSocket gateway policy."
        risk = "high"
    elif _contains(text, "chat", "collaboration", "bidirectional", "双向", "协作"):
        decision = "Evaluate bidirectional client interaction, durability, fan-out, replay, and backpressure before selecting realtime transport."
        risk = "medium"
    else:
        decision = "Evaluate whether the target actually needs live delivery, durable event replay, or simple server-to-client streaming before choosing a realtime mechanism."
        risk = "medium"
    return ArchitectureDecisionV1(
        concern="realtime",
        decision=decision,
        selected_libraries=(),
        options_considered=REALTIME_OPTIONS,
        rationale=(
            "Realtime delivery affects lifecycle, backpressure, replay, and client subscription semantics; "
            "the choice must be explicit before Director implementation."
        ),
        constraints=(
            "For Polaris product/runtime UI, keep the existing NATS JetStream + /v2/ws/runtime path and do not introduce SSE or polling.",
            "Use SSE only for target-project server-to-client streams when platform policy allows it and bidirectional messaging is unnecessary.",
            "Define goroutine/task lifecycle, cancellation, retry, and backpressure behavior before adding producer/consumer code.",
        ),
        risk_level=risk,
        evidence={"guidance_only": True, "matched_keywords": list(evidence)},
        decision_status="guidance",
        source="platform_signal_guidance",
    )


def _database_decision(
    text: str,
    evidence: tuple[str, ...],
) -> ArchitectureDecisionV1:
    if _contains(text, "sqlite", "embedded", "local", "desktop", "single user", "单机", "本地"):
        decision = "Evaluate whether local/embedded persistence is enough, and only then choose a lightweight store behind a data-access boundary."
        risk = "medium"
    elif _contains(text, "mongodb", "mongo", "document", "json document", "文档"):
        decision = "Evaluate document-shaped data, schema flexibility, query needs, and consistency before selecting a document database."
        risk = "medium"
    elif _contains(text, "mysql"):
        decision = "Validate whether the named database technology is an explicit project standard or only an example in the task text before selecting it."
        risk = "high"
    else:
        decision = "Evaluate relational, embedded, and document persistence options from data shape, transactions, migrations, deployment, and existing stack."
        risk = "high"
    return ArchitectureDecisionV1(
        concern="database",
        decision=decision,
        selected_libraries=(),
        options_considered=DATABASE_OPTIONS,
        rationale=(
            "Persistence choices define transaction boundaries, migrations, operational burden, and long-term data-model constraints."
        ),
        constraints=(
            "Keep data access behind an explicit repository/service boundary.",
            "Define migrations, indexes, transaction handling, connection pooling, and backup/restore expectations.",
            "Do not hard-code credentials or environment-specific DSNs in source code.",
        ),
        risk_level=risk,
        evidence={"guidance_only": True, "matched_keywords": list(evidence)},
        decision_status="guidance",
        source="platform_signal_guidance",
    )


def _queue_decision(evidence: tuple[str, ...]) -> ArchitectureDecisionV1:
    return ArchitectureDecisionV1(
        concern="async_work",
        decision="Evaluate whether background work needs durability, retry, ordering, dead-lettering, and restart survival before selecting a queue or stream.",
        selected_libraries=(),
        options_considered=ASYNC_WORK_OPTIONS,
        rationale="Background work needs explicit retry, idempotency, visibility timeout, and dead-letter semantics.",
        constraints=(
            "Every job must have idempotency keys, retry limits, timeout, and dead-letter handling.",
            "Do not use in-process queues for work that must survive process restart.",
        ),
        risk_level="high",
        evidence={"guidance_only": True, "matched_keywords": list(evidence)},
        decision_status="guidance",
        source="platform_signal_guidance",
    )


def _cache_decision(evidence: tuple[str, ...]) -> ArchitectureDecisionV1:
    return ArchitectureDecisionV1(
        concern="cache",
        decision="Evaluate whether the task needs shared cache, rate limiting, session storage, or only a bounded in-process cache.",
        selected_libraries=(),
        options_considered=CACHE_OPTIONS,
        rationale="Cache correctness depends on TTL, invalidation, and whether state must be shared across workers.",
        constraints=(
            "Define TTL, invalidation, serialization format, and cache-miss behavior.",
            "Do not make cache state the only source of truth.",
        ),
        risk_level="medium",
        evidence={"guidance_only": True, "matched_keywords": list(evidence)},
        decision_status="guidance",
        source="platform_signal_guidance",
    )


def _auth_decision(evidence: tuple[str, ...]) -> ArchitectureDecisionV1:
    return ArchitectureDecisionV1(
        concern="auth",
        decision="Evaluate authentication and authorization requirements from product scope, existing identity provider, token/session model, and authorization policy.",
        selected_libraries=(),
        options_considered=AUTH_OPTIONS,
        rationale="Authentication and authorization affect security posture, testability, and future identity-provider integration.",
        constraints=(
            "Store secrets outside source code.",
            "Separate authentication from authorization decisions.",
            "Add tests for expired, malformed, missing, and unauthorized credentials.",
        ),
        risk_level="high",
        evidence={"guidance_only": True, "matched_keywords": list(evidence)},
        decision_status="guidance",
        source="platform_signal_guidance",
    )


def _observability_decision(evidence: tuple[str, ...]) -> ArchitectureDecisionV1:
    return ArchitectureDecisionV1(
        concern="observability",
        decision="Evaluate what runtime evidence is needed for the architecture: structured logs, traces, metrics, audit logs, and correlation IDs.",
        selected_libraries=(),
        options_considered=OBSERVABILITY_OPTIONS,
        rationale="Architecture changes that add I/O, async work, or external dependencies need diagnosable runtime evidence.",
        constraints=(
            "Propagate request/run correlation identifiers.",
            "Avoid logging secrets or high-volume payload bodies.",
            "Expose failures as actionable error codes rather than silent fallbacks.",
        ),
        risk_level="medium",
        evidence={"guidance_only": True, "matched_keywords": list(evidence)},
        decision_status="guidance",
        source="platform_signal_guidance",
    )


def _storage_decision(evidence: tuple[str, ...]) -> ArchitectureDecisionV1:
    return ArchitectureDecisionV1(
        concern="object_storage",
        decision="Evaluate file/blob storage from artifact size, retention, access control, deployment topology, and backup/restore needs.",
        selected_libraries=(),
        options_considered=OBJECT_STORAGE_OPTIONS,
        rationale="File and artifact storage choices affect retention, access control, backup, and deployment topology.",
        constraints=(
            "Validate file type, size, paths, and access permissions at the boundary.",
            "Store metadata separately from blob content when search or lifecycle policies are needed.",
        ),
        risk_level="medium",
        evidence={"guidance_only": True, "matched_keywords": list(evidence)},
        decision_status="guidance",
        source="platform_signal_guidance",
    )
