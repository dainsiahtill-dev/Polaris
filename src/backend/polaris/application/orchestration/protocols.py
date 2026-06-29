"""Protocol interfaces for application-layer orchestration.

This module defines structural Protocols that describe the public contract
of orchestration services and value objects.  Using ``typing.Protocol`` with
``@runtime_checkable`` allows for structural subtyping checks at runtime,
which is useful for testing and dependency injection.

Call chain::

    polaris/application/orchestration/protocols.py  (this module)
        ├── IArchitectDesignDoc    – Design document value object
        ├── IArchitectService      – Architect service contract
        ├── IQaReviewResult        – QA review value object
        ├── IQAService             – QA audit service contract
        └── IAuditVerdictService   – Audit verdict service contract

Architecture constraints (AGENTS.md):
    - Protocols define contracts, never implementations.
    - All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Architect domain protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class IArchitectDesignDoc(Protocol):
    """Protocol for an architecture design document.

    Represents the minimal set of attributes that any design document
    produced by the Architect domain must expose.  Concrete implementations
    (e.g. ``DesignResult`` in ``architect_orchestrator``) satisfy this
    protocol structurally.

    Example::

        def process_design_doc(doc: IArchitectDesignDoc) -> None:
            print(f"{doc.title} ({doc.doc_type}): {doc.content_length} chars")
    """

    @property
    def design_id(self) -> str:
        """Unique identifier for this design document."""
        ...

    @property
    def doc_type(self) -> str:
        """Document type classification.

        Typical values: ``"requirements"``, ``"adr"``,
        ``"interface_contract"``, ``"plan"``.
        """
        ...

    @property
    def title(self) -> str:
        """Human-readable title of the design document."""
        ...

    @property
    def status(self) -> str:
        """Processing status of the document.

        Typical values: ``"completed"``, ``"failed"``, ``"in_progress"``.
        """
        ...

    @property
    def content_length(self) -> int:
        """Byte length of the document content."""
        ...

    @property
    def output_path(self) -> str:
        """File system path where the document was written, or ``""``."""
        ...

    @property
    def error(self) -> str:
        """Error message if the document failed to produce, or ``""``."""
        ...

    @property
    def metadata(self) -> dict[str, Any]:
        """Arbitrary key-value metadata attached to the document."""
        ...


@runtime_checkable
class IArchitectService(Protocol):
    """Protocol for the Architect design service.

    Abstracts ``polaris.cells.architect.design.public.ArchitectService`` so
    that the ``ArchitectOrchestrator`` can remain decoupled from the Cell
    implementation.  All methods are async to reflect the LLM-backed nature
    of the Cell.

    Example::

        def create_orchestrator(service: IArchitectService) -> ArchitectOrchestrator:
            ...
    """

    async def create_requirements_doc(
        self,
        *,
        goal: str,
        in_scope: list[str],
        out_of_scope: list[str],
        constraints: list[str],
        definition_of_done: list[str],
        backlog: list[str],
    ) -> Any:
        """Create a requirements specification document.

        Args:
            goal: Design goal / objective.
            in_scope: Items in scope.
            out_of_scope: Items out of scope.
            constraints: Design constraints.
            definition_of_done: Acceptance criteria.
            backlog: Backlog items.

        Returns:
            A design document object with at least ``doc_id``, ``doc_type``,
            ``title``, ``content``, and ``version`` attributes.
        """
        ...

    async def create_adr(
        self,
        *,
        title: str,
        context: str,
        decision: str,
        consequences: list[str],
    ) -> Any:
        """Create an Architecture Decision Record.

        Args:
            title: ADR title.
            context: Decision context.
            decision: The decision made.
            consequences: List of consequences.

        Returns:
            A design document object with at least ``doc_id``, ``doc_type``,
            ``title``, ``content``, and ``version`` attributes.
        """
        ...

    async def create_interface_contract(
        self,
        *,
        api_name: str,
        endpoints: list[dict[str, Any]],
    ) -> Any:
        """Create an interface contract document.

        Args:
            api_name: Name of the API / interface.
            endpoints: List of endpoint definition dicts.

        Returns:
            A design document object with at least ``doc_id``, ``doc_type``,
            ``title``, ``content``, and ``version`` attributes.
        """
        ...

    async def create_implementation_plan(
        self,
        *,
        milestones: list[str],
        verification_commands: list[str],
        risks: list[dict[str, Any]],
    ) -> Any:
        """Create an implementation plan document.

        Args:
            milestones: Delivery milestone descriptions.
            verification_commands: Commands to verify each milestone.
            risks: List of risk dicts with ``risk`` and ``mitigation`` keys.

        Returns:
            A design document object with at least ``doc_id``, ``doc_type``,
            ``title``, ``content``, and ``version`` attributes.
        """
        ...


# ---------------------------------------------------------------------------
# QA domain protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class IQaReviewResult(Protocol):
    """Protocol for a QA review outcome.

    Represents the minimal set of attributes that any QA review result
    must expose.  Concrete implementations (e.g. ``QaReviewResult`` in
    ``qa_orchestrator``) satisfy this protocol structurally.

    Example::

        def summarize_review(result: IQaReviewResult) -> str:
            return f"Review {result.review_id}: {result.issue_count} issues found"
    """

    @property
    def review_id(self) -> str:
        """Unique identifier for this review."""
        ...

    @property
    def target(self) -> str:
        """Path or identifier of the review target."""
        ...

    @property
    def status(self) -> str:
        """Review processing status.

        Typical values: ``"completed"``, ``"failed"``, ``"skipped"``.
        """
        ...

    @property
    def issue_count(self) -> int:
        """Number of issues found during the review."""
        ...

    @property
    def findings(self) -> tuple[str, ...]:
        """Tuple of human-readable issue descriptions."""
        ...

    @property
    def error(self) -> str:
        """Error message if the review failed, or ``""``."""
        ...

    @property
    def metadata(self) -> dict[str, Any]:
        """Arbitrary key-value metadata attached to the review."""
        ...


@runtime_checkable
class IQAService(Protocol):
    """Protocol for the QA audit service.

    Abstracts ``polaris.cells.qa.audit_verdict.public.QAService`` so that
    the ``QaOrchestrator`` can remain decoupled from the Cell implementation.
    The ``audit_task`` method is async to reflect the LLM-backed nature of
    the Cell.

    Example::

        def create_qa_orchestrator(service: IQAService) -> QaOrchestrator:
            ...
    """

    async def audit_task(
        self,
        *,
        task_id: str,
        task_subject: str,
        changed_files: list[str],
    ) -> Any:
        """Execute a QA review for the given task.

        Args:
            task_id: The task identifier to audit.
            task_subject: Human-readable task subject.
            changed_files: List of changed file paths to review.

        Returns:
            An audit result object with at least ``audit_id``, ``target``,
            ``verdict``, ``issues``, ``metrics``, and ``timestamp`` attributes.
        """
        ...


@runtime_checkable
class IAuditVerdictService(Protocol):
    """Protocol for the audit verdict service.

    Abstracts ``polaris.cells.audit.verdict.public.IndependentAuditService``
    so that the ``QaOrchestrator`` can remain decoupled from the Cell
    implementation.  All methods are async to reflect the LLM-backed nature
    of the Cell.

    Example::

        def run_verdict_flow(service: IAuditVerdictService) -> None:
            ...
    """

    async def run_verdict(self, command: Any) -> Any:
        """Run an audit verdict for a completed review.

        Args:
            command: A verdict command object (e.g. ``RunAuditVerdictCommandV1``)
                with at least ``workspace``, ``run_id``, ``task_id``, and
                ``metadata`` attributes.

        Returns:
            A verdict result object with at least ``verdict``, ``details``,
            and ``status`` attributes.
        """
        ...

    async def query_verdict(self, query: Any) -> Any:
        """Query the current verdict state for a task or run.

        Args:
            query: A verdict query object (e.g. ``QueryAuditVerdictV1``) with
                at least ``workspace``, ``run_id``, ``task_id``, and
                ``include_artifacts`` attributes.

        Returns:
            A query result object with at least ``ok``, ``status``,
            ``verdict``, ``details``, ``error_code``, and ``error_message``
            attributes.
        """
        ...
