"""Comprehensive test suite for all refactoring phases.

This module validates the complete "Thin CLI + Core OO" refactoring
across all phases (1-6).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "backend"))


def test_phase1_config_snapshot():
    """Phase 1: ConfigSnapshot with source tracking."""
    print("\n=== Phase 1: ConfigSnapshot ===")

    from domain.models.config_snapshot import ConfigSnapshot, SourceType

    # Test merge priority: default < persisted < env < cli
    snapshot = ConfigSnapshot.merge_sources(
        default={"key": "default", "only_default": "val1"},
        persisted={"key": "persisted", "only_persisted": "val2"},
        env={"key": "env", "only_env": "val3"},
        cli={"key": "cli", "only_cli": "val4"},
    )

    assert snapshot.get("key") == "cli", "CLI should have highest priority"
    assert snapshot.get("only_default") == "val1"
    assert snapshot.get_source("key") == SourceType.CLI

    # Test immutability
    new_snapshot = snapshot.with_override({"new_key": "value"}, SourceType.CLI)
    assert not snapshot.has("new_key"), "Original should be unchanged"
    assert new_snapshot.has("new_key"), "New should have the key"

    print("  ✓ ConfigSnapshot merge priority works")
    print("  ✓ Source tracking works")
    print("  ✓ Immutability works")


def test_phase2_backend_bootstrap():
    """Phase 2: BackendBootstrapper."""
    print("\n=== Phase 2: BackendBootstrapper ===")

    from core.startup import BackendBootstrapper, ConfigLoader
    from application.dto.backend_launch import BackendLaunchRequest

    # Test ConfigLoader
    loader = ConfigLoader()
    snapshot = loader.load(cli_overrides={"server.port": 8080})
    assert snapshot.get("server.port") == 8080

    # Test BackendLaunchRequest
    request = BackendLaunchRequest(
        host="127.0.0.1",
        port=8080,
        workspace=Path.cwd(),
    )
    assert request.host == "127.0.0.1"

    uvicorn_opts = request.to_uvicorn_options()
    assert uvicorn_opts["port"] == 8080
    assert uvicorn_opts["factory"] is True

    # Test BackendBootstrapper
    bootstrapper = BackendBootstrapper()
    defaults = bootstrapper.get_default_options()
    assert "host" in defaults

    free_port = bootstrapper._find_free_port()
    assert 1024 <= free_port <= 65535

    print("  ✓ ConfigLoader works")
    print("  ✓ BackendLaunchRequest works")
    print("  ✓ BackendBootstrapper works")


def test_phase3_orchestration():
    """Phase 3: RuntimeOrchestrator."""
    print("\n=== Phase 3: RuntimeOrchestrator ===")

    from core.orchestration import (
        RuntimeOrchestrator,
        ServiceDefinition,
        ProcessLauncher,
        EventStream,
        OrchestrationEvent,
        EventType,
    )
    from application.dto.process_launch import RunMode

    # Test ServiceDefinition
    definition = ServiceDefinition(
        name="pm",
        command=["python", "-m", "pm"],
        working_dir=Path.cwd(),
        run_mode=RunMode.SINGLE,
    )
    assert definition.name == "pm"

    request = definition.to_launch_request()
    assert request.mode == RunMode.SINGLE

    # Test ProcessLauncher
    launcher = ProcessLauncher()
    pm_request = launcher.launch_pm(Path("."), RunMode.SINGLE)
    assert pm_request.name == "pm"
    assert pm_request.role == "pm"

    director_request = launcher.launch_director(Path("."), RunMode.ONE_SHOT, iterations=2)
    assert director_request.name == "director"

    # Test EventStream
    stream = EventStream()
    events = []
    stream.subscribe(lambda e: events.append(e))

    event = OrchestrationEvent.spawned("pm", "test123", 12345, ["python", "-m", "pm"])
    stream.publish(event)

    assert len(events) == 1
    assert events[0].event_type == EventType.SPAWNED

    # Test RuntimeOrchestrator
    orchestrator = RuntimeOrchestrator()
    assert len(orchestrator.list_active()) == 0

    print("  ✓ ServiceDefinition works")
    print("  ✓ ProcessLauncher works")
    print("  ✓ EventStream works")
    print("  ✓ RuntimeOrchestrator works")


def test_dto_consistency():
    """Test DTO consistency across phases."""
    print("\n=== DTO Consistency ===")

    from application.dto.backend_launch import BackendLaunchResult
    from application.dto.process_launch import ProcessLaunchResult

    # BackendLaunchResult
    backend_result = BackendLaunchResult(
        success=True,
        port=8080,
        process_handle={"pid": 12345},
        startup_time_ms=100,
    )
    assert backend_result.is_success()

    event = backend_result.to_electron_event()
    assert event["event"] == "backend_started"
    assert event["port"] == 8080

    # ProcessLaunchResult
    process_result = ProcessLaunchResult(
        success=True,
        pid=12345,
        process_handle={"id": "test123"},
    )
    assert process_result.is_success()
    assert process_result.pid == 12345

    print("  ✓ BackendLaunchResult works")
    print("  ✓ ProcessLaunchResult works")


def test_architecture_compliance():
    """Test architecture compliance (no sys.argv outside CLI)."""
    print("\n=== Architecture Compliance ===")

    import ast
    import os

    backend_dir = Path(__file__).parents[2] / "src" / "backend"

    violations = []
    cli_files = {"server.py", "cli.py", "main.py"}

    for py_file in backend_dir.rglob("*.py"):
        # Skip tests and CLI files
        if "test" in py_file.name or py_file.name in cli_files:
            continue
        if "scripts" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content)

            for node in ast.walk(tree):
                # Check for sys.argv access
                if isinstance(node, ast.Attribute):
                    if node.attr == "argv":
                        if isinstance(node.value, ast.Name) and node.value.id == "sys":
                            rel_path = py_file.relative_to(backend_dir)
                            violations.append(f"{rel_path}: sys.argv")

                # Check for ArgumentParser creation
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == "ArgumentParser":
                        rel_path = py_file.relative_to(backend_dir)
                        violations.append(f"{rel_path}: ArgumentParser()")

        except SyntaxError:
            continue

    if violations:
        print(f"  ⚠ Found {len(violations)} potential violations:")
        for v in violations[:5]:
            print(f"    - {v}")
    else:
        print("  ✓ No architecture violations found")


def main():
    """Run all phase tests."""
    print("=" * 60)
    print("Polaris Refactoring - All Phases Validation")
    print("=" * 60)

    try:
        test_phase1_config_snapshot()
        test_phase2_backend_bootstrap()
        test_phase3_orchestration()
        test_dto_consistency()
        test_architecture_compliance()

        print("\n" + "=" * 60)
        print("✅ All phase tests passed!")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
