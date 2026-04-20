"""Runtime orchestrator for unified PM/Director lifecycle management.

This module provides RuntimeOrchestrator, the core service for managing
PM and Director subprocesses with unified semantics.

Example:
    >>> from polaris.cells.orchestration.workflow_runtime.public.service import RuntimeOrchestrator, ServiceDefinition
    >>>
    >>> orchestrator = RuntimeOrchestrator()
    >>>
    >>> # Define PM service
    >>> pm_def = ServiceDefinition(
    ...     name="pm",
    ...     command=["python", "-m", "pm", "--workspace", "."],
    ...     workspace=Path("."),
    ...     run_mode=RunMode.LOOP,
    ... )
    >>>
    >>> # Launch and manage
    >>> handle = await orchestrator.submit(pm_def)
    >>> status = await orchestrator.status(handle)
    >>> await orchestrator.terminate(handle)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from polaris.cells.orchestration.workflow_runtime.public.process_launch import ProcessLaunchRequest, RunMode

from .event_stream import EventLevel, EventStream, EventType, OrchestrationEvent
from .process_launcher import ProcessLauncher

_logger = logging.getLogger(__name__)


class ServiceState(Enum):
    """Service lifecycle states."""

    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


@dataclass(frozen=True)
class ServiceDefinition:
    """Service specification for orchestration.

    Attributes:
        name: Service name (pm, director, etc.)
        command: Command and arguments
        working_dir: Working directory
        env_vars: Environment variable overrides
        resource_limits: Resource constraints
        run_mode: Execution mode (single, loop, daemon, etc.)
        restart_policy: Restart behavior
        health_check: Health check configuration
        dependencies: List of service names to wait for
    """

    name: str
    command: list[str]
    working_dir: Path
    env_vars: dict[str, str] = field(default_factory=dict)
    resource_limits: dict[str, Any] = field(default_factory=dict)
    run_mode: RunMode = RunMode.SINGLE
    restart_policy: str = "never"  # never, on-failure, always
    health_check: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)

    def to_launch_request(self) -> ProcessLaunchRequest:
        """Convert to ProcessLaunchRequest."""
        return ProcessLaunchRequest(
            mode=self.run_mode,
            command=self.command,
            workspace=self.working_dir,
            env_vars=self.env_vars,
            name=self.name,
            role=self.name,
        )


@dataclass
class ServiceHandle:
    """Handle to a managed service.

    Attributes:
        id: Unique service ID
        definition: Service definition
        state: Current service state
        process_handle: Underlying process handle
        start_time: When service was started
        end_time: When service ended (if completed)
        restart_count: Number of restarts
        last_error: Last error message
    """

    id: str
    definition: ServiceDefinition
    state: ServiceState = ServiceState.PENDING
    process_handle: dict[str, Any] | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    restart_count: int = 0
    last_error: str | None = None

    @property
    def is_running(self) -> bool:
        """Check if service is running."""
        return self.state in (ServiceState.STARTING, ServiceState.RUNNING)

    @property
    def is_completed(self) -> bool:
        """Check if service has completed."""
        return self.state in (ServiceState.COMPLETED, ServiceState.FAILED, ServiceState.TERMINATED)


class RuntimeOrchestrator:
    """Unified runtime orchestrator for PM and Director.

    This class provides a unified interface for managing subprocess
    lifecycle with consistent semantics across PM and Director.

    Attributes:
        _launcher: Process launcher instance
        _event_stream: Event stream for audit logging
        _services: Dictionary of managed services
        _service_order: Set of service names for dependency tracking
    """

    def __init__(
        self,
        event_stream: EventStream | None = None,
    ) -> None:
        """Initialize runtime orchestrator.

        Args:
            event_stream: Optional event stream for logging
        """
        self._launcher = ProcessLauncher()
        self._event_stream = event_stream or EventStream()
        self._services: dict[str, ServiceHandle] = {}
        self._service_order: set[str] = set()

    async def shutdown(self) -> None:
        """Shutdown the orchestrator and cleanup all resources.

        This method terminates all running services and releases resources.
        Should be called when the orchestrator is no longer needed.
        """
        # Terminate all running services
        for service_id in list(self._services.keys()):
            service = self._services.get(service_id)
            if service and service.is_running:
                try:
                    await self.terminate(service, timeout=5.0)
                except (RuntimeError, ValueError) as exc:
                    _logger.debug("service terminate failed (best-effort): service_id=%s: %s", service_id, exc)

        # Clear services dictionary
        self._services.clear()
        self._service_order.clear()

    async def submit(self, definition: ServiceDefinition) -> ServiceHandle:
        """Submit a service for execution.

        Args:
            definition: Service definition

        Returns:
            Service handle
        """
        # Generate service ID
        import uuid

        service_id = f"{definition.name}_{uuid.uuid4().hex[:8]}"

        # Create handle
        handle = ServiceHandle(
            id=service_id,
            definition=definition,
            state=ServiceState.PENDING,
        )

        # Store service
        self._services[service_id] = handle
        self._service_order.add(definition.name)

        # Wait for dependencies
        await self._wait_for_dependencies(definition)

        # Launch service
        await self._launch_service(handle)

        return handle

    async def status(self, handle: ServiceHandle) -> dict[str, Any]:
        """Get service status.

        Args:
            handle: Service handle

        Returns:
            Status dictionary
        """
        service = self._services.get(handle.id)
        if not service:
            return {"error": "Service not found"}

        return {
            "id": service.id,
            "name": service.definition.name,
            "state": service.state.value,
            "is_running": service.is_running,
            "is_completed": service.is_completed,
            "start_time": service.start_time.isoformat() if service.start_time else None,
            "end_time": service.end_time.isoformat() if service.end_time else None,
            "restart_count": service.restart_count,
            "last_error": service.last_error,
        }

    async def terminate(self, handle: ServiceHandle, timeout: float = 10.0) -> bool:
        """Terminate a service gracefully.

        Args:
            handle: Service handle
            timeout: Time to wait for graceful termination

        Returns:
            True if terminated successfully
        """
        service = self._services.get(handle.id)
        if not service:
            return False

        if not service.is_running:
            return True

        service.state = ServiceState.STOPPING

        # Terminate process
        if service.process_handle:
            success = await self._launcher.terminate(service.process_handle, timeout)
            if success:
                service.state = ServiceState.TERMINATED
                service.end_time = datetime.now()

                # Emit event
                self._emit_event(
                    EventType.TERMINATED,
                    service.definition.name,
                    service_id=service.id,
                )

            return success

        return False

    async def wait_for_completion(
        self,
        handle: ServiceHandle,
        timeout: float | None = None,
    ) -> ServiceHandle:
        """Wait for service to complete.

        Args:
            handle: Service handle
            timeout: Maximum time to wait

        Returns:
            Updated service handle
        """
        service = self._services.get(handle.id)
        if not service:
            return handle

        if service.is_completed:
            return service

        # Wait for process to complete
        if service.process_handle:
            result = await self._launcher.wait_for(service.process_handle, timeout)

            # Update service state
            if result.exit_code == 0:
                service.state = ServiceState.COMPLETED
            else:
                service.state = ServiceState.FAILED
                service.last_error = result.error_message

            service.end_time = datetime.now()

            # Emit event
            self._emit_event(
                EventType.COMPLETED if result.exit_code == 0 else EventType.FAILED,
                service.definition.name,
                service_id=service.id,
                exit_code=result.exit_code,
            )

        return service

    def list_active(self) -> list[ServiceHandle]:
        """List all active services.

        Returns:
            List of active service handles
        """
        return [s for s in self._services.values() if s.is_running]

    def list_all(self) -> list[ServiceHandle]:
        """List all services.

        Returns:
            List of all service handles
        """
        return list(self._services.values())

    async def _wait_for_dependencies(self, definition: ServiceDefinition) -> None:
        """Wait for dependencies to be ready.

        Args:
            definition: Service definition with dependencies
        """
        for dep_name in definition.dependencies:
            # Find running service with this name
            dep_services = [s for s in self._services.values() if s.definition.name == dep_name and s.is_running]

            if dep_services:
                # Wait for any instance to complete
                await self.wait_for_completion(dep_services[0])

    async def _launch_service(self, handle: ServiceHandle) -> None:
        """Launch a service.

        Args:
            handle: Service handle to launch
        """
        handle.state = ServiceState.STARTING
        handle.start_time = datetime.now()

        try:
            # Convert to launch request
            request = handle.definition.to_launch_request()

            # Launch process
            result = await self._launcher.launch(request)

            if result.is_success():
                handle.state = ServiceState.RUNNING
                handle.process_handle = result.process_handle

                # Emit event
                self._emit_event(
                    EventType.SPAWNED,
                    handle.definition.name,
                    service_id=handle.id,
                    pid=result.pid,
                )

                # Handle restart policy for non-persistent modes
                if handle.definition.run_mode not in (RunMode.LOOP, RunMode.DAEMON, RunMode.CONTINUOUS):
                    # For one-shot modes, wait for completion
                    asyncio.create_task(self._monitor_service(handle))

            else:
                handle.state = ServiceState.FAILED
                handle.last_error = result.error_message
                handle.end_time = datetime.now()

                # Emit event
                self._emit_event(
                    EventType.FAILED,
                    handle.definition.name,
                    service_id=handle.id,
                    error=result.error_message,
                )

                # Retry if policy allows
                await self._maybe_restart(handle)

        except (RuntimeError, ValueError) as e:
            handle.state = ServiceState.FAILED
            handle.last_error = str(e)
            handle.end_time = datetime.now()

            self._emit_event(
                EventType.FAILED,
                handle.definition.name,
                service_id=handle.id,
                error=str(e),
            )

    async def _monitor_service(self, handle: ServiceHandle) -> None:
        """Monitor a service and update state.

        Args:
            handle: Service handle to monitor
        """
        await self.wait_for_completion(handle)

        # Check if restart needed
        await self._maybe_restart(handle)

    async def _maybe_restart(self, handle: ServiceHandle) -> None:
        """Check if service should be restarted.

        Args:
            handle: Service handle
        """
        policy = handle.definition.restart_policy

        if policy == "never":
            return

        if policy == "on-failure" and handle.state != ServiceState.FAILED:
            return

        # Check restart limit
        if handle.restart_count >= 3:  # Max 3 restarts
            self._emit_event(
                EventType.RETRY_EXHAUSTED,
                handle.definition.name,
                service_id=handle.id,
            )
            return

        # Schedule restart
        handle.restart_count += 1
        handle.state = ServiceState.PENDING
        handle.last_error = None
        handle.end_time = None

        self._emit_event(
            EventType.RETRY_SCHEDULED,
            handle.definition.name,
            service_id=handle.id,
            attempt=handle.restart_count,
        )

        # Wait before restart
        await asyncio.sleep(1.0 * handle.restart_count)

        # Relaunch
        await self._launch_service(handle)

    def _emit_event(
        self,
        event_type: EventType,
        source: str,
        level: EventLevel = EventLevel.INFO,
        **payload: Any,
    ) -> None:
        """Emit an orchestration event.

        Args:
            event_type: Event type
            source: Source component
            level: Event level
            **payload: Event payload
        """
        event = OrchestrationEvent(
            event_type=event_type,
            source=source,
            level=level,
            payload=payload,
        )
        self._event_stream.publish(event)

    # Convenience methods for PM and Director

    async def launch_pm(
        self,
        workspace: Path,
        mode: RunMode = RunMode.SINGLE,
        **kwargs: Any,
    ) -> ServiceHandle:
        """Convenience method to launch PM.

        Args:
            workspace: Workspace path
            mode: Execution mode
            **kwargs: Additional parameters

        Returns:
            Service handle
        """
        request = self._launcher.launch_pm(workspace, mode, **kwargs)

        definition = ServiceDefinition(
            name="pm",
            command=request.command,
            working_dir=workspace,
            run_mode=mode,
            env_vars=request.env_vars,
        )

        return await self.submit(definition)

    async def launch_director(
        self,
        workspace: Path,
        mode: RunMode = RunMode.ONE_SHOT,
        **kwargs: Any,
    ) -> ServiceHandle:
        """Convenience method to launch Director.

        Args:
            workspace: Workspace path
            mode: Execution mode
            **kwargs: Additional parameters

        Returns:
            Service handle
        """
        request = self._launcher.launch_director(workspace, mode, **kwargs)

        definition = ServiceDefinition(
            name="director",
            command=request.command,
            working_dir=workspace,
            run_mode=mode,
            env_vars=request.env_vars,
        )

        return await self.submit(definition)
