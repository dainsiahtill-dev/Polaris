# 恢复经验证的候选文件

在冻结的工程 host 配置中设置 `"retain_verified_candidate": true`。默认关闭。开始新 run 时，工程 owner 将选择保存到原始 scope。恢复时原 run 和当前 host 都须开启保留，旧 run 不会被追认。每次真实验证通过后，平台把候选的工作区文件按字节和元数据与该次验证清单核对，并再次观察当前清单。有效记录位于同一次 `verification_completed` 事件的 `input_binding.verified_checkpoint`。SHA-256 寻址的 TAR 保存在该工作区受保护的 `.polaris/runtime/engineering/checkpoints/`。相同内容复用同一个文件。校验失败和后续修改保留先前成功的版本。存储开销由开启保留的主机承担，目前没有自动清理。

捕获范围是授权的工作区内可读路径及声明的验证输出。外部依赖、主机运行时、缓存、`.git` 和 `.polaris` 不进入导出。单次最多 4096 个条目、64 MiB 未压缩归档。支持普通单链接文件和目录。范围内出现软链接、硬链接、特殊对象、大小超限等情况时，快照状态保留为 `unavailable`。若捕获期间候选发生变化，当次验证归为 `unknown`。检查通过、捕获可用及 QA 验收分别保留。事件只新增归档摘要、格式、状态和计数。

本机 CLI 使用与原 run 对应的宿主配置。当前配置须继续允许文件保留及原权限 profile。以下命令中的环境变量由操作者提供，目标目录必须尚不存在。

```bash
rtk proxy env KERNELONE_ENGINEERING_HOST_CONFIG="$host_config" python -m polaris.delivery.cli console \
  --workspace "$workspace" --role director --engineering-action export \
  --run-id "$run_id" --destination "$destination" --verification-seq "$event_seq"
```

省略 `--verification-seq` 时，CLI 选择最近一次成功保留的验证版本。导出成功后，标准输出的 JSON 包含 `run_id` 和 `destination`。

宿主程序通过现有 `EngineeringRunService` 导出。须安装当前权限解析器，使原权限仍可重新核验。传入自己控制的、已存在的父目录下一个新的绝对路径。

```python
from pathlib import Path

restored = service.export_verified_candidate(
    session_id,
    destination=Path("/srv/polaris-recovery/candidate-001"),
)
```

默认导出最近一次成功保留的候选。传入 `verification_seq` 可选定旧的 `verification_completed` 事件。导出校验归档摘要、每个文件的验证清单和当前授权范围；仅在新建目录内恢复文件与目录，保留所有者执行位，使用私有访问权限。已存在的目标、活动工作区内的目标和指向其它目录的链接会被拒绝。失败后清理由本次操作独占创建的目录。整个过程不执行被恢复的程序、验证命令或外部副作用。文件恢复不代表环境、外部服务、所有权、时间戳及 ACL 的复原，也不使历史候选获得当前验收资格。
