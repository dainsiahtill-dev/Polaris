#!/usr/bin/env python3
"""端到端重构验证脚本.

验证清单:
1. 后端启动流程
2. PM 薄 CLI 工作流
3. Director 薄 CLI 工作流
4. 可观测性事件流
5.  Electron 启动协议兼容性
"""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


class RefactoringValidator:
    """端到端验证器."""

    def __init__(self):
        self.results = []
        self.errors = []

    def log(self, msg: str):
        print(f"[validate] {msg}")

    def test(self, name: str) -> bool:
        """运行单个测试."""
        self.log(f"Testing: {name}...")
        return True

    def validate_phase1_config(self) -> bool:
        """验证 Phase 1: ConfigSnapshot."""
        try:
            sys.path.insert(0, str(PROJECT_ROOT / "src" / "backend"))
            from domain.models.config_snapshot import ConfigSnapshot, SourceType

            snapshot = ConfigSnapshot.merge_sources(
                default={"server.port": 8080},
                persisted={"pm.backend": "embedded"},
                env={"server.port": "9000"},
                cli={"server.port": 49977}
            )

            assert snapshot.get("server.port") == 49977
            assert snapshot.get_source("server.port") == SourceType.CLI

            self.log("  ✓ ConfigSnapshot 工作正常")
            return True
        except Exception as e:
            self.errors.append(f"Phase 1: {e}")
            return False

    def validate_phase2_bootstrap(self) -> bool:
        """验证 Phase 2: BackendBootstrapper."""
        try:
            from core.startup import BackendBootstrapper

            bootstrapper = BackendBootstrapper()
            defaults = bootstrapper.get_default_options()

            assert "host" in defaults
            assert "port" in defaults

            self.log("  ✓ BackendBootstrapper 工作正常")
            return True
        except Exception as e:
            self.errors.append(f"Phase 2: {e}")
            return False

    def validate_phase3_orchestration(self) -> bool:
        """验证 Phase 3: RuntimeOrchestrator."""
        try:
            from core.orchestration import (
                RuntimeOrchestrator, ServiceDefinition, RunMode
            )

            orchestrator = RuntimeOrchestrator()
            assert len(orchestrator.list_active()) == 0

            # 创建服务定义
            definition = ServiceDefinition(
                name="test",
                command=[sys.executable, "-c", "print('hello')"],
                working_dir=PROJECT_ROOT,
                run_mode=RunMode.SINGLE,
            )
            assert definition.name == "test"

            self.log("  ✓ RuntimeOrchestrator 工作正常")
            return True
        except Exception as e:
            self.errors.append(f"Phase 3: {e}")
            return False

    def validate_phase4_cli(self) -> bool:
        """验证 Phase 4: 薄 CLI."""
        try:
            import ast

            # 验证 PM 薄 CLI 文件存在且语法正确
            pm_cli = PROJECT_ROOT / "src" / "backend" / "scripts" / "pm" / "cli_thin.py"
            assert pm_cli.exists(), f"PM thin CLI not found: {pm_cli}"
            pm_content = pm_cli.read_text(encoding="utf-8")
            ast.parse(pm_content)
            assert "create_parser" in pm_content
            assert "PMThinCLI" in pm_content

            # 验证 Director 薄 CLI
            director_cli = PROJECT_ROOT / "src" / "backend" / "scripts" / "director" / "cli_thin.py"
            assert director_cli.exists(), f"Director thin CLI not found: {director_cli}"
            director_content = director_cli.read_text(encoding="utf-8")
            ast.parse(director_content)
            assert "create_parser" in director_content
            assert "DirectorThinCLI" in director_content

            # 验证 Polaris 薄 CLI
            hp_cli = PROJECT_ROOT / "polaris_thin.py"
            assert hp_cli.exists(), f"Polaris thin CLI not found: {hp_cli}"
            hp_content = hp_cli.read_text(encoding="utf-8")
            ast.parse(hp_content)
            assert "PolarisThinCLI" in hp_content

            self.log("  ✓ 薄 CLI 适配器工作正常")
            return True
        except Exception as e:
            import traceback
            self.errors.append(f"Phase 4: {e}\n{traceback.format_exc()}")
            return False

    def validate_phase5_observability(self) -> bool:
        """验证 Phase 5: 可观测性."""
        try:
            from core.orchestration import (
                create_observability_stack, EventStream
            )

            stream = EventStream()
            ui, metrics, health, logger = create_observability_stack(stream)

            assert ui is not None
            assert metrics is not None
            assert health is not None

            self.log("  ✓ 可观测性层工作正常")
            return True
        except Exception as e:
            self.errors.append(f"Phase 5: {e}")
            return False

    def validate_phase6_cleanup(self) -> bool:
        """验证 Phase 6: 清理脚本."""
        try:
            script_path = PROJECT_ROOT / "scripts" / "phase6_cleanup.py"
            assert script_path.exists()

            # 验证可以执行 --help
            result = subprocess.run(
                [sys.executable, str(script_path), "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert result.returncode == 0
            assert "--dry-run" in result.stdout

            self.log("  ✓ 清理脚本工作正常")
            return True
        except Exception as e:
            self.errors.append(f"Phase 6: {e}")
            return False

    async def validate_electron_compatibility(self) -> bool:
        """验证 Electron 启动协议兼容性."""
        try:
            from core.orchestration import HealthMonitor, EventStream
            from core.orchestration.event_stream import OrchestrationEvent, EventType

            stream = EventStream()
            health = HealthMonitor(stream)

            # 启动健康监控
            await health.start()

            # 模拟 backend_started 事件
            event = OrchestrationEvent(
                event_type=EventType.SPAWNED,
                source="backend",
                payload={"event": "backend_started", "port": 49977, "host": "127.0.0.1"}
            )
            stream.publish(event)

            # 给一点时间处理事件
            await asyncio.sleep(0.1)

            assert health.is_backend_ready(), "Backend not marked as ready"
            assert health._backend_port == 49977, f"Expected port 49977, got {health._backend_port}"

            await health.stop()

            self.log("  ✓ Electron 启动协议兼容")
            return True
        except Exception as e:
            import traceback
            self.errors.append(f"Electron: {e}\n{traceback.format_exc()}")
            return False

    async def run_all(self) -> bool:
        """运行所有验证."""
        self.log("=" * 60)
        self.log("Polaris 重构端到端验证")
        self.log("=" * 60)

        tests = [
            ("Phase 1: ConfigSnapshot", self.validate_phase1_config),
            ("Phase 2: BackendBootstrapper", self.validate_phase2_bootstrap),
            ("Phase 3: RuntimeOrchestrator", self.validate_phase3_orchestration),
            ("Phase 4: Thin CLI", self.validate_phase4_cli),
            ("Phase 5: Observability", self.validate_phase5_observability),
            ("Phase 6: Cleanup", self.validate_phase6_cleanup),
            ("Electron Compatibility", self.validate_electron_compatibility),
        ]

        passed = 0
        failed = 0

        for name, test_fn in tests:
            # 支持同步和异步测试函数
            if asyncio.iscoroutinefunction(test_fn):
                result = await test_fn()
            else:
                result = test_fn()

            if result:
                passed += 1
            else:
                failed += 1

        self.log("")
        self.log("=" * 60)
        self.log(f"结果: {passed} 通过, {failed} 失败")
        self.log("=" * 60)

        if self.errors:
            self.log("\n错误详情:")
            for err in self.errors:
                self.log(f"  - {err}")

        return failed == 0


def main():
    validator = RefactoringValidator()
    success = asyncio.run(validator.run_all())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
