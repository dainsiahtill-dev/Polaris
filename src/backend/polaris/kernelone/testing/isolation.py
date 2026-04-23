"""Test isolation utilities for polaris.

This module provides:
    - GlobalStateIsolationManager: Safe snapshot/restore for sys.modules, os.environ, and module globals
    - ModuleGuard: Context manager for blocking module imports during tests
    - SingletonResetGuard: Context manager for resetting module-level singletons

These utilities replace direct sys.modules/os.environ manipulation in tests,
providing a safe, reversible way to isolate test state.

Usage:
    # Snapshot and restore sys.modules
    manager = GlobalStateIsolationManager()
    snapshot = manager.snapshot_modules(["polaris.kernelone.events"])
    # ... run test ...
    manager.restore_modules(snapshot)

    # Block module imports
    with ModuleBlocker("polaris.delivery"):
        import polaris.cells.some_cell  # Raises ImportError
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator


# ─────────────────────────────────────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ModuleSnapshot:
    """Snapshot of sys.modules entries for a set of modules.

    Attributes:
        modules: Dict mapping module name to module object (None = was not loaded)
        prefixes: List of module prefixes that were snapshotted
    """

    modules: dict[str, types.ModuleType | None] = field(default_factory=dict)
    prefixes: list[str] = field(default_factory=list)

    def __getitem__(self, key: str) -> types.ModuleType | None:
        return self.modules[key]

    def __contains__(self, key: str) -> bool:
        return key in self.modules


@dataclass
class EnvSnapshot:
    """Snapshot of os.environ entries.

    Attributes:
        env: Dict mapping key to value (None = was not set)
        keys: List of environment variable keys that were snapshotted
    """

    env: dict[str, str | None] = field(default_factory=dict)
    keys: list[str] = field(default_factory=list)


@dataclass
class ModuleGlobalsSnapshot:
    """Snapshot of module global variables.

    Attributes:
        module_name: The fully-qualified module name
        globals: Dict mapping global name to value (None = was not set)
    """

    module_name: str
    globals: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# GlobalStateIsolationManager
# ─────────────────────────────────────────────────────────────────────────────


class GlobalStateIsolationManager:
    """Manages global state isolation for tests.

    This class provides a safe, reversible way to:
        - Snapshot and restore sys.modules entries
        - Snapshot and restore os.environ variables
        - Snapshot and restore module-level global variables
        - Block specific module imports during tests

    Unlike direct sys.modules manipulation, this class:
        - Tracks all changes for proper cleanup
        - Supports nested snapshots
        - Provides context managers for automatic cleanup
        - Validates module existence before manipulation

    Example:
        manager = GlobalStateIsolationManager()

        # Snapshot and restore sys.modules
        snapshot = manager.snapshot_modules(["polaris.delivery"])
        try:
            import polaris.cells.some_cell
            # ... test code ...
        finally:
            manager.restore_modules(snapshot)

        # Or use the context manager
        with manager.module_isolation(["polaris.delivery"]):
            import polaris.cells.some_cell  # Will fail
    """

    def __init__(self) -> None:
        self._module_snapshots: list[ModuleSnapshot] = []
        self._env_snapshots: list[EnvSnapshot] = []
        self._globals_snapshots: list[ModuleGlobalsSnapshot] = []

    # ─────────────────────────────────────────────────────────────────────────
    # sys.modules Operations
    # ─────────────────────────────────────────────────────────────────────────

    def snapshot_modules(
        self,
        prefixes: list[str],
    ) -> ModuleSnapshot:
        """Take a snapshot of sys.modules entries matching prefixes.

        Args:
            prefixes: List of module names or prefixes to snapshot.
                     A prefix matches if module_name == prefix or
                     module_name.startswith(prefix + ".").

        Returns:
            ModuleSnapshot containing all matching entries (key: module name,
            value: module object or None if not loaded)

        Example:
            snapshot = manager.snapshot_modules(["polaris.kernelone.events"])
            # ... test code ...
            manager.restore_modules(snapshot)
        """
        modules: dict[str, types.ModuleType | None] = {}
        for name in list(sys.modules):
            for prefix in prefixes:
                if name == prefix or name.startswith(prefix + "."):
                    modules[name] = sys.modules.get(name)
                    break

        snapshot = ModuleSnapshot(modules=modules, prefixes=prefixes[:])
        self._module_snapshots.append(snapshot)
        return snapshot

    def restore_modules(self, snapshot: ModuleSnapshot) -> None:
        """Restore sys.modules to snapshot state.

        This method:
            1. Removes any modules that were added after the snapshot
            2. Restores modules that were modified after the snapshot
            3. Does NOT restore modules that were removed before the snapshot
               (to preserve imports made by other tests)

        Args:
            snapshot: ModuleSnapshot returned by snapshot_modules()

        Raises:
            ValueError: If snapshot was not created by this manager
        """
        if snapshot not in self._module_snapshots:
            raise ValueError(
                "Snapshot was not created by this manager. Use snapshot_modules() to create valid snapshots."
            )

        # Get current modules
        current_modules = set(sys.modules.keys())
        snapshotted_modules = set(snapshot.modules.keys())

        # Remove modules added after snapshot (keep ones that were already there)
        for name in current_modules - snapshotted_modules:
            for prefix in snapshot.prefixes:
                if name == prefix or name.startswith(prefix + "."):
                    sys.modules.pop(name, None)
                    break

        # Restore modules to their snapshot state
        for name, module in snapshot.modules.items():
            if module is None:
                # Module was not loaded at snapshot time, ensure it's not loaded now
                sys.modules.pop(name, None)
            else:
                # Restore to snapshot state
                sys.modules[name] = module

        self._module_snapshots.remove(snapshot)

    def evict_modules(self, prefixes: list[str]) -> ModuleSnapshot:
        """Evict (remove) modules matching prefixes from sys.modules.

        Unlike snapshot_modules(), this removes the modules immediately
        rather than just recording their state.

        Args:
            prefixes: List of module names or prefixes to evict

        Returns:
            ModuleSnapshot for later restoration

        Example:
            snapshot = manager.evict_modules(["polaris.delivery"])
            try:
                # delivery modules will raise ImportError
                import polaris.delivery.cli  # Fails
            finally:
                manager.restore_modules(snapshot)
        """
        snapshot = self.snapshot_modules(prefixes)
        for name in list(sys.modules):
            for prefix in prefixes:
                if name == prefix or name.startswith(prefix + "."):
                    sys.modules.pop(name, None)
                    break
        return snapshot

    @contextmanager
    def module_isolation(
        self,
        prefixes: list[str],
    ) -> Generator[None, None, None]:
        """Context manager for isolating module imports.

        All modules matching prefixes will be temporarily removed from
        sys.modules during the context. On exit, they are restored to
        their original state.

        Args:
            prefixes: List of module names or prefixes to isolate

        Yields:
            None

        Example:
            with manager.module_isolation(["polaris.delivery"]):
                # polaris.delivery.* imports will fail
                import polaris.delivery.cli  # ImportError
        """
        snapshot = self.evict_modules(prefixes)
        try:
            yield
        finally:
            self.restore_modules(snapshot)

    # ─────────────────────────────────────────────────────────────────────────
    # os.environ Operations
    # ─────────────────────────────────────────────────────────────────────────

    def snapshot_env(self, keys: list[str]) -> EnvSnapshot:
        """Take a snapshot of os.environ entries.

        Args:
            keys: List of environment variable names to snapshot

        Returns:
            EnvSnapshot containing all matching entries (key: var name,
            value: value or None if not set)
        """
        env: dict[str, str | None] = {k: os.environ.get(k) for k in keys}
        snapshot = EnvSnapshot(env=env, keys=keys[:])
        self._env_snapshots.append(snapshot)
        return snapshot

    def restore_env(self, snapshot: EnvSnapshot) -> None:
        """Restore os.environ to snapshot state.

        Args:
            snapshot: EnvSnapshot returned by snapshot_env()

        Raises:
            ValueError: If snapshot was not created by this manager
        """
        if snapshot not in self._env_snapshots:
            raise ValueError("Snapshot was not created by this manager. Use snapshot_env() to create valid snapshots.")

        for key, value in snapshot.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        self._env_snapshots.remove(snapshot)

    @contextmanager
    def env_isolation(
        self,
        keys: list[str],
        values: dict[str, str] | None = None,
    ) -> Generator[None, None, None]:
        """Context manager for isolating environment variables.

        Args:
            keys: List of environment variable names to isolate
            values: Optional dict of values to set during the context

        Yields:
            None

        Example:
            with manager.env_isolation(["KERNELONE_ROOT"], {"KERNELONE_ROOT": "/tmp/test"}):
                os.environ["KERNELONE_ROOT"] = "/tmp/test"  # Test code
        """
        snapshot = self.snapshot_env(keys)
        # Clear the keys
        for key in keys:
            os.environ.pop(key, None)
        # Set new values if provided
        if values:
            for key, value in values.items():
                os.environ[key] = value
        try:
            yield
        finally:
            self.restore_env(snapshot)

    # ─────────────────────────────────────────────────────────────────────────
    # Module Globals Operations
    # ─────────────────────────────────────────────────────────────────────────

    def snapshot_module_globals(
        self,
        module_name: str,
        globals_to_watch: list[str],
    ) -> ModuleGlobalsSnapshot:
        """Take a snapshot of specific global variables in a module.

        Args:
            module_name: Fully-qualified module name (e.g., "polaris.kernelone.events")
            globals_to_watch: List of global variable names to track

        Returns:
            ModuleGlobalsSnapshot

        Raises:
            ImportError: If module is not loaded
        """
        if module_name not in sys.modules:
            raise ImportError(f"Module {module_name!r} is not loaded")

        module = sys.modules[module_name]
        globals_dict: dict[str, Any] = {}
        for name in globals_to_watch:
            globals_dict[name] = getattr(module, name, None)

        snapshot = ModuleGlobalsSnapshot(module_name=module_name, globals=globals_dict)
        self._globals_snapshots.append(snapshot)
        return snapshot

    def restore_module_globals(self, snapshot: ModuleGlobalsSnapshot) -> None:
        """Restore module globals to snapshot state.

        Args:
            snapshot: ModuleGlobalsSnapshot returned by snapshot_module_globals()

        Raises:
            ValueError: If snapshot was not created by this manager
            ImportError: If module is no longer loaded
        """
        if snapshot not in self._globals_snapshots:
            raise ValueError(
                "Snapshot was not created by this manager. Use snapshot_module_globals() to create valid snapshots."
            )

        if snapshot.module_name not in sys.modules:
            raise ImportError(f"Module {snapshot.module_name!r} is no longer loaded")

        module = sys.modules[snapshot.module_name]
        for name, value in snapshot.globals.items():
            if value is None:
                # Was not set at snapshot time
                if hasattr(module, name):
                    delattr(module, name)
            else:
                setattr(module, name, value)

        self._globals_snapshots.remove(snapshot)

    @contextmanager
    def module_globals_isolation(
        self,
        module_name: str,
        globals_to_watch: list[str],
    ) -> Generator[None, None, None]:
        """Context manager for isolating module global variables.

        Args:
            module_name: Fully-qualified module name
            globals_to_watch: List of global variable names to track

        Yields:
            None

        Example:
            with manager.module_globals_isolation(
                "polaris.kernelone.events.bus_adapter",
                ["_default_adapter"]
            ):
                # _default_adapter will be None during context
                import polaris.kernelone.events.bus_adapter
                bus_adapter._default_adapter = None
        """
        snapshot = self.snapshot_module_globals(module_name, globals_to_watch)
        module = sys.modules[module_name]
        # Set all to None
        for name in snapshot.globals:
            setattr(module, name, None)
        try:
            yield
        finally:
            self.restore_module_globals(snapshot)


# ─────────────────────────────────────────────────────────────────────────────
# ModuleBlocker (replaces _DeliveryBlocker pattern)
# ─────────────────────────────────────────────────────────────────────────────


class ModuleBlocker(types.ModuleType):
    """A fake module that raises ImportError for any non-dunder attribute access.

    This is useful for testing that a package does not import certain modules
    at load time. Place into sys.modules under the target module name, and
    any attempt to import that module will raise ImportError with a clear message.

    Usage:
        blocker = ModuleBlocker("polaris.delivery")
        sys.modules["polaris.delivery"] = blocker
        try:
            import polaris.delivery.cli  # Raises ImportError
        finally:
            sys.modules.pop("polaris.delivery", None)

    Dunder attributes (__file__, __spec__, __loader__, etc.) are passed through
    to avoid breaking the import machinery.
    """

    _ACCEPTED_DUNDERS = frozenset(
        {
            "__name__",
            "__doc__",
            "__package__",
            "__loader__",
            "__spec__",
            "__file__",
            "__cached__",
            "__builtins__",
            "__path__",
            "__abstractmethods__",
            "__dict__",
            "__weakref__",
        }
    )

    def __init__(self, full_name: str) -> None:
        super().__init__(full_name)
        self.__name__ = full_name

    def __getattr__(self, name: str) -> Any:
        if name in self._ACCEPTED_DUNDERS:
            return super().__getattribute__(name)
        raise ImportError(f"Module {self.__name__!r} is blocked by test isolation. Attempted to access: {name!r}")

    def __dir__(self) -> list[str]:
        return list(self._ACCEPTED_DUNDERS)


@contextmanager
def block_modules(*module_names: str) -> Generator[None, None, None]:
    """Context manager to block specific modules during test execution.

    All sub-modules of the given modules will also be blocked.

    Args:
        *module_names: Module names or prefixes to block (e.g., "polaris.delivery")

    Yields:
        None

    Example:
        with block_modules("polaris.delivery", "polaris.orchestration"):
            import polaris.delivery.cli  # Raises ImportError
    """
    blockers: dict[str, types.ModuleType | None] = {}

    # Create blockers for all module names
    for name in module_names:
        if name in sys.modules:
            blockers[name] = sys.modules[name]
        else:
            blockers[name] = None
        sys.modules[name] = ModuleBlocker(name)

    try:
        yield
    finally:
        # Restore original state
        for name, original in blockers.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


# ─────────────────────────────────────────────────────────────────────────────
# Singleton Reset Guard
# ─────────────────────────────────────────────────────────────────────────────


class SingletonResetGuard:
    """Context manager for resetting module-level singletons.

    This is a class-based alternative to the reset_singletons() function,
    providing a cleaner interface for test isolation.

    Attributes:
        singletons: Dict mapping module name to list of singleton variable names.

    Example:
        with SingletonResetGuard({
            "polaris.kernelone.events": ["_global_bus"],
        }):
            # All listed singletons are reset to None
            from polaris.kernelone.events import registry
            assert registry._global_bus is None
    """

    def __init__(self, singletons: dict[str, list[str]]) -> None:
        self._singletons = singletons

    def __enter__(self) -> None:
        import sys

        self._snapshots: list[tuple[str, str, object]] = []
        for module_name, var_names in self._singletons.items():
            if module_name not in sys.modules:
                continue
            module = sys.modules[module_name]
            for var_name in var_names:
                if hasattr(module, var_name):
                    self._snapshots.append((module_name, var_name, getattr(module, var_name)))
                    setattr(module, var_name, None)
                else:
                    self._snapshots.append((module_name, var_name, None))

    def __exit__(self, *args: object) -> None:
        import sys

        for module_name, var_name, original_value in self._snapshots:
            if module_name in sys.modules:
                module = sys.modules[module_name]
                if original_value is None:
                    if hasattr(module, var_name):
                        delattr(module, var_name)
                else:
                    setattr(module, var_name, original_value)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton Reset Utilities
# ─────────────────────────────────────────────────────────────────────────────


@contextmanager
def reset_singletons(
    singletons: dict[str, list[str]],
) -> Generator[None, None, None]:
    """Context manager to reset module-level singleton variables.

    Args:
        singletons: Dict mapping module name to list of singleton variable names.
                   Example: {"polaris.kernelone.events": ["_global_bus", "_default_adapter"]}

    Yields:
        None

    Example:
        with reset_singletons({
            "polaris.kernelone.events": ["_global_bus"],
            "polaris.kernelone.events.bus_adapter": ["_default_adapter"]
        }):
            # All listed singletons are reset to None
            from polaris.kernelone.events import registry
            assert registry._global_bus is None
    """
    # Snapshot and reset
    snapshots: list[tuple[str, str, Any]] = []
    for module_name, var_names in singletons.items():
        if module_name not in sys.modules:
            continue
        module = sys.modules[module_name]
        for var_name in var_names:
            if hasattr(module, var_name):
                snapshots.append((module_name, var_name, getattr(module, var_name)))
                setattr(module, var_name, None)
            else:
                snapshots.append((module_name, var_name, None))

    try:
        yield
    finally:
        # Restore
        for module_name, var_name, original_value in snapshots:
            if module_name in sys.modules:
                module = sys.modules[module_name]
                if original_value is None:
                    if hasattr(module, var_name):
                        delattr(module, var_name)
                else:
                    setattr(module, var_name, original_value)


# ─────────────────────────────────────────────────────────────────────────────
# Global Manager Instance (for convenience)
# ─────────────────────────────────────────────────────────────────────────────

# Module-level singleton for use across tests
_default_manager: GlobalStateIsolationManager | None = None


def get_isolation_manager() -> GlobalStateIsolationManager:
    """Get the default GlobalStateIsolationManager instance.

    Returns:
        GlobalStateIsolationManager: Shared instance for convenience
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = GlobalStateIsolationManager()
    return _default_manager
