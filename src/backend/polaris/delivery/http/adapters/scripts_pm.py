"""Adapter for PM runtime access without sys.path manipulation."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ScriptsPMAdapter:
    """Clean adapter for PM runtime without sys.path hacks.

    This adapter loads PM modules from ``polaris.delivery.cli.pm``
    without modifying sys.path, ensuring clean imports and proper isolation.

    Example:
        >>> adapter = ScriptsPMAdapter("/path/to/workspace")
        >>> pm = adapter.get_pm()
        >>> pm.is_initialized()
    """

    def __init__(self, workspace: str | Path) -> None:
        """Initialize the PM adapter.

        Args:
            workspace: Path to the workspace
        """
        self.workspace = Path(workspace).resolve()
        self._pm_instance: Any | None = None
        self._pm_module: Any | None = None

    def _load_pm_module(self) -> Any:
        """Load the PM integration module using proper package imports.

        This method requires the standard backend package import path and
        fails fast when runtime bootstrap is incomplete.

        Returns:
            The loaded pm_integration module
        """
        if self._pm_module is not None:
            return self._pm_module

        try:
            from polaris.delivery.cli.pm import pm_integration as module

            self._pm_module = module
            return module
        except ImportError as exc:
            raise ImportError(
                "Unable to import 'polaris.delivery.cli.pm.pm_integration'. "
                "Ensure backend bootstrap adds src/backend to PYTHONPATH before "
                "constructing ScriptsPMAdapter."
            ) from exc

    def get_pm(self) -> Any:
        """Get the PM instance for the workspace.

        Returns:
            PM instance
        """
        if self._pm_instance is not None:
            return self._pm_instance

        module = self._load_pm_module()
        get_pm_func = getattr(module, "get_pm", None)
        if get_pm_func is None:
            raise ImportError("get_pm function not found in pm_integration module")

        self._pm_instance = get_pm_func(str(self.workspace))
        return self._pm_instance

    def is_initialized(self) -> bool:
        """Check if PM is initialized for this workspace.

        Returns:
            True if PM is initialized
        """
        try:
            pm = self.get_pm()
            return pm.is_initialized()
        except (RuntimeError, ValueError):
            return False

    def initialize(self, project_name: str = "", description: str = "") -> dict[str, Any]:
        """Initialize PM for the workspace.

        Args:
            project_name: Project name
            description: Project description

        Returns:
            Initialization result
        """
        pm = self.get_pm()
        return pm.initialize(project_name=project_name, description=description)

    def get_status(self) -> dict[str, Any]:
        """Get PM status.

        Returns:
            Status dictionary
        """
        pm = self.get_pm()
        if not pm.is_initialized():
            return {"initialized": False, "workspace": str(self.workspace)}
        return pm.get_status()

    def list_documents(
        self,
        doc_type: str | None = None,
        pattern: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List documents.

        Args:
            doc_type: Filter by document type
            pattern: Glob pattern to filter paths
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with documents list
        """
        pm = self.get_pm()
        return pm.list_documents(doc_type=doc_type, pattern=pattern, limit=limit, offset=offset)

    def get_document(self, doc_path: str) -> dict[str, Any] | None:
        """Get document information.

        Args:
            doc_path: Document path

        Returns:
            Document info or None
        """
        pm = self.get_pm()
        return pm.get_document(doc_path)

    def get_document_content(self, doc_path: str, version: str | None = None) -> str | None:
        """Get document content.

        Args:
            doc_path: Document path
            version: Specific version

        Returns:
            Document content or None
        """
        pm = self.get_pm()
        return pm.get_document_content(doc_path, version)

    def create_or_update_document(
        self,
        doc_path: str,
        content: str,
        updated_by: str = "api",
        change_summary: str = "",
    ) -> Any | None:
        """Create or update a document.

        Args:
            doc_path: Document path
            content: Document content
            updated_by: Who made the update
            change_summary: Summary of changes

        Returns:
            Version info or None
        """
        pm = self.get_pm()
        return pm.create_or_update_document(doc_path, content, updated_by, change_summary)

    def delete_document(self, doc_path: str, delete_file: bool = True) -> bool:
        """Delete a document.

        Args:
            doc_path: Document path
            delete_file: Whether to delete the actual file

        Returns:
            True if deleted successfully
        """
        pm = self.get_pm()
        return pm.delete_document(doc_path, delete_file)

    def get_document_versions(self, doc_path: str) -> list[Any]:
        """Get all versions of a document.

        Args:
            doc_path: Document path

        Returns:
            List of versions
        """
        pm = self.get_pm()
        return pm.get_document_versions(doc_path)

    def compare_document_versions(self, doc_path: str, old_version: str, new_version: str) -> Any:
        """Compare two document versions.

        Args:
            doc_path: Document path
            old_version: Old version number
            new_version: New version number

        Returns:
            Diff result
        """
        pm = self.get_pm()
        return pm.compare_document_versions(doc_path, old_version, new_version)

    def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search documents.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching documents
        """
        pm = self.get_pm()
        return pm.search_documents(query, limit)

    def list_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List tasks.

        Args:
            status: Filter by status
            assignee: Filter by assignee
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with tasks list
        """
        pm = self.get_pm()
        return pm.list_tasks(status=status, assignee=assignee, limit=limit, offset=offset)

    def get_task_history(
        self,
        task_id: str | None = None,
        assignee: str | None = None,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get task history.

        Args:
            task_id: Filter by task ID
            assignee: Filter by assignee
            status: Filter by status
            start_date: Start date filter
            end_date: End date filter
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with task history
        """
        pm = self.get_pm()
        return pm.get_task_history(
            task_id=task_id,
            assignee=assignee,
            status=status,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )

    def get_director_task_history(
        self,
        iteration: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get tasks dispatched to Director.

        Args:
            iteration: Filter by PM iteration number
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with director tasks
        """
        pm = self.get_pm()
        return pm.get_director_task_history(iteration=iteration, limit=limit, offset=offset)

    def get_task(self, task_id: str) -> Any | None:
        """Get a specific task.

        Args:
            task_id: Task ID

        Returns:
            Task object or None
        """
        pm = self.get_pm()
        return pm.get_task(task_id)

    def get_task_assignments(
        self,
        task_id: str | None = None,
        assignee: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get task assignment history.

        Args:
            task_id: Filter by task ID
            assignee: Filter by assignee
            limit: Maximum results

        Returns:
            List of assignment records
        """
        pm = self.get_pm()
        return pm.get_task_assignments(task_id, assignee, limit)

    def search_tasks(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search tasks.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching tasks
        """
        pm = self.get_pm()
        return pm.search_tasks(query, limit)

    def list_requirements(
        self,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List requirements.

        Args:
            status: Filter by status
            priority: Filter by priority
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with requirements list
        """
        pm = self.get_pm()
        return pm.list_requirements(status=status, priority=priority, limit=limit, offset=offset)

    def get_requirement(self, req_id: str) -> dict[str, Any] | None:
        """Get a specific requirement.

        Args:
            req_id: Requirement ID

        Returns:
            Requirement dict or None
        """
        pm = self.get_pm()
        return pm.get_requirement(req_id)

    def analyze_project_health(self) -> dict[str, Any]:
        """Analyze project health.

        Returns:
            Health analysis report
        """
        pm = self.get_pm()
        return pm.analyze_project_health()
