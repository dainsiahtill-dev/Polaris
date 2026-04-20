# Toad

最小可运行的 Polaris `toad` 入口。

当前实现不复刻上游 `batrachianai/toad` 的完整 TUI 与 shell 特性，而是把 Polaris 现有稳定的角色终端主机收敛成单一入口：

`toad -> polaris.delivery.cli.toad.app.run_toad() -> polaris.delivery.cli.terminal_console.run_role_console()`

最小运行方式：

```bash
python -m polaris.delivery.cli.toad --workspace . --role director
```
