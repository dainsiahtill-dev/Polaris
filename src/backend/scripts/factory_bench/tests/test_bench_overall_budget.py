"""--overall-timeout 必须真的参与控制流。

此前它只被校验（!=6000 就报错）和记录进报告，从不限制跑批：120 个项目每个最多
5400s，实际要跑 7 天以上，而这个参数假装兜住了。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _cli_module():
    path = Path(__file__).resolve().parents[1] / "_bench_lib" / "cli.py"
    spec = importlib.util.spec_from_file_location("bench_cli_overall_budget", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overall_budget_exhausted_record_is_machine_readable() -> None:
    module = _cli_module()
    record = module.overall_budget_exhausted_record(budget_seconds=6000, completed=7, selected=120)
    assert record["reason"] == "overall_budget_exhausted"
    assert record["exit_code"] == 2
    assert record["harness_status"] == "UNKNOWN"
    assert record["completed_records"] == 7 and record["selected_count"] == 120


def test_deadline_check_stops_before_the_next_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """预算耗尽必须在开下一个项目**之前**停，而不是让它跑满 5400s。"""

    module = _cli_module()
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    deadline = 100.0 + 6000.0
    assert module.overall_budget_exhausted_record(budget_seconds=6000, completed=0, selected=1)
    # 未到期：还可以跑
    now[0] = 100.0 + 5999.0
    assert now[0] < deadline
    # 到期：停
    now[0] = 100.0 + 6001.0
    assert now[0] >= deadline


def test_go_compile_check_uses_the_host_toolchain(tmp_path) -> None:
    """非 /usr 安装的工具链也要能进沙箱。

    实测 L1-04（Go 项目）：平台侧 completed/accepted/passed 全绿，唯独
    bench_gate=UNKNOWN —— 因为 catalog 质检的 go_compile 报
    "toolchain unavailable"（旧版只认 /usr 下的工具链），而
    host 配置里明明配了 /home/<user>/.local/go/bin。
    """

    import shutil

    from scripts.factory_bench._bench_lib import catalog_quality as cq

    if shutil.which("go") is None:
        import pytest

        pytest.skip("host has no go toolchain")

    # pytest 的 tmp_path 在 /tmp（tmpfs，沙箱保留挂载）——那会被
    # build_workspace_sandbox_argv 拒成 "workspace overlaps a reserved mount"，
    # 与工具链无关。用 home 下的临时目录。
    root = Path.home() / ".polaris" / "bench-sandbox-checks"
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "go-project"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "go.mod").write_text("module example.com/x\n\ngo 1.23\n", encoding="utf-8")
    (workspace / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")

    assert cq._run_command(["go", "build", "./..."], cwd=workspace) is True


def test_cargo_compile_check_uses_the_host_toolchain(tmp_path) -> None:
    """Rust 项目同理：~/.cargo/bin/cargo 是 rustup shim，需要 RUSTUP_HOME。

    实测 L1-05 catalog 质检 FAIL："rustup could not choose a version of cargo"，
    而宿主上 stable toolchain 明明是 default。
    """

    import shutil

    from scripts.factory_bench._bench_lib import catalog_quality as cq

    if shutil.which("cargo") is None:
        import pytest

        pytest.skip("host has no cargo")

    root = Path.home() / ".polaris" / "bench-sandbox-checks"
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "rust-project"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "Cargo.toml").write_text(
        '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8"
    )
    (workspace / "src").mkdir(exist_ok=True)
    (workspace / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    assert cq._run_command(["cargo", "build", "--offline"], cwd=workspace, timeout_s=300) is True


def test_workspace_local_toolchain_is_not_passed_as_a_toolchain_dir(tmp_path) -> None:
    """工具在 workspace 内（node_modules/.bin/tsc 是符号链接）时不能再作为
    toolchain dir 传给沙箱 —— 它会撞 "overlaps a writable mount"。

    实测 L1-08：平台 completed/accepted/approved 全绿，唯 bench_gate=UNKNOWN，
    只因为 catalog 质检的 ts_syntax 报 "isolated verifier unavailable: ValueError"。
    """

    import shutil

    from scripts.factory_bench._bench_lib import catalog_quality as cq

    # pytest 的 tmp_path 在 /tmp（沙箱保留挂载）—— 与工具链测试同一个坑。
    root = Path.home() / ".polaris" / "bench-sandbox-checks"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "ts-project"
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    # 造一个 workspace 内的 tsc（符号链接，像真实 node_modules/.bin/tsc）
    real = workspace / "node_modules" / "typescript" / "bin" / "tsc"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    real.chmod(0o755)
    bin_dir = workspace / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "tsc").symlink_to(real)

    assert cq._run_typescript_check(workspace) is True
    # 关键点：命令用的是 workspace 内的绝对路径（像真实 node_modules/.bin/tsc），
    # 而不是 PATH 里的全局 tsc。
