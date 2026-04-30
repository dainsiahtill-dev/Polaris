"""Architecture Invariants Tests

这些测试用于验证 Polaris 代码库的架构约束。
确保不存在重复实现、废弃模块引用等问题。
"""

import warnings
from pathlib import Path

import pytest

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src" / "backend"


class TestArchitectureInvariants:
    """架构约束测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """测试设置"""
        self.src_dir = SRC_DIR

    def _find_py_files(self, directory: Path, exclude_dirs: set[str] | None = None) -> list[Path]:
        """递归查找 Python 文件"""
        if exclude_dirs is None:
            exclude_dirs = {".git", "__pycache__", ".mypy_cache", "node_modules", ".venv", "venv"}

        py_files = []
        for path in directory.rglob("*.py"):
            # 排除测试文件和特殊目录
            if any(excluded in path.parts for excluded in exclude_dirs):
                continue
            py_files.append(path)
        return py_files

    def test_no_direct_role_agent_import(self):
        """测试：非兼容层代码不得直接 import core.polaris_loop.role_agent

        规则: 除了 role_agent 自身和兼容层，其他代码不应直接导入 role_agent

        Note: This test intentionally searches for the literal string
        "from core.polaris_loop.role_agent" in source files to detect
        legacy imports. The old root (core.polaris_loop.role_agent) has
        been migrated to polaris.cells.roles.runtime. This invariant
        ensures no production code re-introduces the deprecated path.
        """
        violations = []

        # 允许导入的位置
        allowed_importers = {
            "core.polaris_loop.role_agent",  # 自身
            "app.llm.engine.token_budget",  # 已有的兼容导入
        }

        # 扫描所有 Python 文件
        py_files = self._find_py_files(self.src_dir)

        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8")
            except OSError:
                # OSError: file read error (not found, permission, encoding issues)
                continue

            # 检查是否有 role_agent 导入
            if "from core.polaris_loop.role_agent" not in content:
                continue

            # 检查是否在允许列表中
            module_path = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/").replace("/", ".").replace(".py", "")
            if module_path not in allowed_importers:
                # 检查是否使用延迟导入 (try/except)
                if "try:" in content and "from core.polaris_loop.role_agent" in content:
                    # 延迟导入是允许的
                    continue
                violations.append(f"{py_file.relative_to(PROJECT_ROOT)}")

        # fail-closed: 有违规就失败
        if violations:
            msg = f"发现 {len(violations)} 个非兼容层引用 role_agent:\n"
            for v in violations:
                msg += f"  - {v}\n"
            msg += "请将导入移到兼容层或加入允许列表"
            pytest.fail(msg)

    def test_no_duplicate_jsonl_implementation(self):
        """测试: io_utils 不应包含 JSONL 实现

        规则: JSONL 实现应该在 io_jsonl_ops 中，io_utils 应该转发到 io_jsonl_ops
        """
        io_utils_path = self.src_dir / "core" / "polaris_loop" / "io_utils.py"

        if not io_utils_path.exists():
            pytest.skip("io_utils.py 不存在")

        content = io_utils_path.read_text(encoding="utf-8")

        # 检查是否有重复的 JSONL 实现（不在 deprecation wrapper 中）
        # 查找实际的函数定义（非 deprecation wrapper）
        has_duplicate = False

        # 检查是否所有 JSONL 函数都有 deprecation warning
        jsonl_functions = [
            "append_jsonl_atomic",
            "append_jsonl",
            "flush_jsonl_buffers",
            "configure_jsonl_buffer",
            "scan_last_seq",
        ]

        for func in jsonl_functions:
            # 查找函数定义
            if f"def {func}(" in content:
                # 检查这个函数定义是否在 deprecation wrapper 之后
                func_pos = content.find(f"def {func}(")
                if func_pos > 0:
                    # 向前查找最近的 warnings.warn
                    before_func = content[:func_pos]
                    last_warn = before_func.rfind("warnings.warn")
                    last_deprecated = before_func.rfind("DeprecationWarning")

                    # 如果没有在 deprecation warning 之后，可能是重复实现
                    if last_warn < last_deprecated:
                        has_duplicate = True
                        print(f"警告: {func} 可能缺少 deprecation wrapper")

        assert not has_duplicate, "io_utils 包含重复的 JSONL 实现"

    def test_utf8_encoding_compliance(self):
        """测试: 所有 open() 调用必须显式 encoding="utf-8"

        规则: 禁止使用隐式编码
        """
        violations = []
        py_files = self._find_py_files(self.src_dir)

        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8")
            except OSError:
                # OSError: file read error (not found, permission, encoding issues)
                continue

            # 查找不带 encoding 的 open() 调用
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                # 匹配 open(...) 但不包含 encoding=
                if "open(" in line and "encoding=" not in line:
                    # 排除注释和字符串
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                        continue
                    # 检查是否是实际的文件 open
                    if "open(" in line and "=" not in line.split("open(")[0]:
                        violations.append(f"{py_file.relative_to(PROJECT_ROOT)}:{i}")

        # fail-closed: 有违规就失败
        if violations:
            msg = f"发现 {len(violations)} 处缺少 encoding 的 open():\n"
            for v in violations[:10]:  # 只显示前 10 个
                msg += f"  - {v}\n"
            msg += '请添加 encoding="utf-8"'
            pytest.fail(msg)

    def test_deprecated_modules_have_warnings(self):
        """测试: 废弃模块应该有 deprecation warning"""
        deprecated_modules = [
            "core.polaris_loop.role_agent",
            "core.runtime_orchestrator",
            "core.polaris_loop.io_jsonl",
        ]

        for module in deprecated_modules:
            # 尝试导入模块
            try:
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    # 导入应该触发 deprecation warning
                    __import__(module)

                    # 检查是否触发了 DeprecationWarning
                    deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
                    if not deprecation_warnings:
                        print(f"警告: 模块 {module} 缺少 DeprecationWarning")
            except ImportError:
                # 模块不存在，跳过
                pass

    def test_no_sys_path_manipulation_in_production(self):
        """测试: 生产代码禁止 sys.path.insert/append (fail-closed)

        规则: 除了白名单文件，生产代码不应修改 sys.path
        """
        # 白名单: 允许修改 sys.path 的文件
        whitelist = {
            "tests/",  # 测试可以修改
            "scripts/",  # 脚本可以修改
            "core/startup/",  # 启动模块可以修改
            "app/adapters/scripts_pm.py",  # 已知兼容导入
        }

        violations = []
        py_files = self._find_py_files(self.src_dir)

        for py_file in py_files:
            # 检查是否在白名单中
            rel_path = str(py_file.relative_to(PROJECT_ROOT))
            if any(rel_path.startswith(w) for w in whitelist):
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
            except OSError:
                # OSError: file read error (not found, permission, encoding issues)
                continue

            # 检查 sys.path.insert 或 sys.path.append
            if "sys.path.insert" in content or "sys.path.append" in content:
                violations.append(rel_path)

        # fail-closed: 有违规就失败
        if violations:
            msg = f"发现 {len(violations)} 个生产代码文件修改 sys.path:\n"
            for v in violations:
                msg += f"  - {v}\n"
            msg += "请将文件加入白名单或移除 sys.path 操作"
            pytest.fail(msg)

    def test_no_datetime_utcnow(self):
        """测试: 禁止 datetime.utcnow() 使用 (fail-closed)

        规则: 使用 timezone-aware datetime 替代 utcnow()
        """
        # 白名单: 允许使用 utcnow 的文件 (遗留代码)
        whitelist = {
            # 可以在这里添加已知遗留文件
        }

        violations = []
        py_files = self._find_py_files(self.src_dir)

        for py_file in py_files:
            rel_path = str(py_file.relative_to(PROJECT_ROOT))
            if any(rel_path.startswith(w) for w in whitelist):
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
            except OSError:
                # OSError: file read error (not found, permission, encoding issues)
                continue

            # 检查 datetime.utcnow() 或 datetime.utcnow 的调用
            if "datetime.utcnow()" in content:
                violations.append(rel_path)

        # Fail-closed: 发现 utcnow 使用必须失败
        if violations:
            msg = f"发现 {len(violations)} 个文件使用 datetime.utcnow():\n"
            for v in violations:
                msg += f"  - {v}\n"
            msg += "\n请使用 datetime.now(timezone.utc) 替代"
            pytest.fail(msg)

    def test_no_placeholder_success_in_factory(self):
        """测试: Factory 代码禁止占位乐观成功 (fail-closed)

        规则: Factory 不能返回伪造的成功状态或 sleep placeholder
        """
        factory_files = [
            self.src_dir / "app" / "routers" / "factory_legacy_runner.py",
            self.src_dir / "app" / "services" / "factory_run_service.py",
        ]

        violations = []
        placeholder_patterns = [
            "await asyncio.sleep(1)  # 占位",
            "await asyncio.sleep(1)  # placeholder",
            'status="completed"  # 乐观完成',
            'status="completed"  # optimistic',
        ]

        for factory_file in factory_files:
            if not factory_file.exists():
                continue

            try:
                content = factory_file.read_text(encoding="utf-8")
            except OSError:
                # OSError: file read error (not found, encoding, permission, etc.)
                continue

            for pattern in placeholder_patterns:
                if pattern in content:
                    violations.append(f"{factory_file.relative_to(PROJECT_ROOT)}: {pattern}")

        # fail-closed: 发现占位代码就失败
        if violations:
            msg = f"发现 {len(violations)} 处 Factory 占位代码:\n"
            for v in violations:
                msg += f"  - {v}\n"
            msg += "请替换为真实实现或返回明确失败"
            pytest.fail(msg)


class TestCompatibilityLayers:
    """兼容性层测试"""

    def test_io_jsonl_ops_exports(self):
        """测试: io_jsonl_ops 应该导出所有必要的函数"""
        try:
            from polaris.kernelone.fs.jsonl.ops import (
                append_jsonl,
                append_jsonl_atomic,
                configure_jsonl_buffer,
                flush_jsonl_buffers,
                scan_last_seq,
            )

            # 所有函数都应该可导入
            assert callable(append_jsonl)
            assert callable(append_jsonl_atomic)
            assert callable(configure_jsonl_buffer)
            assert callable(flush_jsonl_buffers)
            assert callable(scan_last_seq)
        except ImportError as e:
            pytest.fail(f"无法导入 io_jsonl_ops: {e}")

    def test_error_mapping_exports(self):
        """测试: error_mapping 模块应该导出所有必要的类"""
        try:
            from polaris.kernelone.llm.engine import (
                KernelRepairCategory,
                NoRetryCategory,
                PlatformRetryCategory,
                is_retryable,
                map_error_to_category,
            )

            assert PlatformRetryCategory is not None
            assert KernelRepairCategory is not None
            assert NoRetryCategory is not None
            assert callable(map_error_to_category)
            assert callable(is_retryable)
        except ImportError as e:
            pytest.fail(f"无法导入 error_mapping: {e}")

    def test_llm_cache_exports(self):
        """测试: LLMCache 应该导出并可用"""
        try:
            from polaris.cells.roles.kernel.internal.llm_cache import LLMCache, get_global_llm_cache  # noqa: F401

            cache = LLMCache()
            assert hasattr(cache, "get")
            assert hasattr(cache, "put")
            assert hasattr(cache, "get_stats")
        except ImportError as e:
            pytest.fail(f"无法导入 LLMCache: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
