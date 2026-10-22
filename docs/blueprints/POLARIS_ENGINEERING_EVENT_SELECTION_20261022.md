# Engineering 事件按需读取

日期：2026-10-22。状态：四消费者迁移及有界验证完成，随本批候选提交；未取得新版本原生交付证据。

## 问题、事实和边界

r05 在原 5400 秒期限内未交回 Director 控制，最终 failed／verification not_run／QA 未调用。它同时暴露本地开销与模型迭代未收敛两个问题，本批只处理已复现的事件读取成本。335 个逻辑请求不等于 HTTP 数，原失败和成本未知均保留。

第一处读取偏差：`roles.runtime` 的消费者只要特定事实，却让 `EngineeringSessionStore.load_events` 读取并 JSON 解码整个 journal。终态事件载荷 246,301,283 字节，其中 request_prepared 辅助载荷 200,270,422 字节。视觉四类同角色事实约 43 MB；319 个 Director turn_started 载荷共 638 字节。数据库体积本身不是耗时证明。

有限实验已执行：不可变 seq<=2966／3693 两份 SQLite 副本，每项三次普通计时、一次 cProfile 与一次 tracemalloc。复用真实 store read 方法、review_binding、manifest 和 visual producer，只替换只读连接；没有模型、工具执行、目标验收或原运行重放。prefix 仅裁 events，runs/tool_calls 保留终态，不能当历史恢复快照。r1、r2 实验设置失败均保留，r3 完成。

较长 prefix 的普通计时中位数：turn 身份谓词 1.84132→0.00823 秒；pending_tool_calls 1.82672→0.00618；review_binding 2.95952→1.12803；事件读取加视觉生成 2.09842→0.56368。相同输入的所有双臂返回内容 canonical SHA 相同。当前目标及四个原授权运行资产根的 manifest 哈希仍约 1.11 秒，不能删掉它来放大收益。计时包含实验只读 URI、audit hook 和连接适配开销，不是线上绝对延迟或历史 37.15% 的函数份额；pending 只覆盖本夹具空集合，恢复另测。

证据根：`~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task22-engineering-read-path/`。`experiment/results-r3/profile.json` 保存原始计时和比较；`root-terminal-db-baseline.json` 保存原库指纹；`convergence/` 保存独立活性诊断。

## 模块职责及数据流

```text
原 engineering/sessions.sqlite3（唯一写 owner 不变）
    EngineeringSessionStore 原连接／身份检查
        参数化 kind／role WHERE；按原 seq 顺序读取
            原消费者谓词／视觉生成／候选绑定
```

State owner 仍为 `roles.runtime`；不增加 Cell、controller、缓存、数据库表、索引或结果镜像。Cell 内部读接口复用既有 SQLite／事务能力；工具、KFS、KernelOne、权限和最终 provider request 链不改。

接口：`load_events(session_id: str, *, kinds: tuple[str, ...] | None = None, role: str | None = None) -> list[dict[str, Any]]`。默认行为保持全读；None 表示不筛选，显式空 kinds 返回空，显式空 role 只匹配空值。原 run 存在与控制目录／数据库身份检查必须在空结果前执行。SQL 值全部参数化。

四处迁移：

- loop 的视觉输入只读同角色 message、tool_observed、tool_started、visual_observation_completed，保留全部匹配历史，禁止 tail 截断。
- native 请求身份检查只读同角色 turn_started，保留原 Python seq／kind／role 谓词；不改成存在 SQLite 类型亲和差异的 seq 点查询。
- review_binding 只读 verification_completed；完整当前 manifest／权限／profile／revision／recipe 绑定不变。
- pending_tool_calls 只读 tool_reconciled；原 started 行、复合 turn/call 身份及两连接语义不变。

错误语义：未知 run 和物理身份变化仍拒绝；选中事件的坏 JSON 仍失败。过滤查询不读取无关 payload，因此不证明全库健康；与全量方法的输出等价只声明于有效 journal。默认全量读取的错误行为保持。恢复仍用原身份、权限、deadline 和全部必要事实，不自动重放 UNKNOWN 效果。

## 假设、反证和停止条件

H：下推已知消费谓词可以减少无关解码，同时保持这些消费者结果。反证是任一工具／失败／当前图片或歧义事实丢失、合法恢复改变、拒绝错误扩大、或在原固定输入上无实际局部收益。模型为什么继续迭代、剩余预算呈现是否有益、资源 manifest 如何提速均未由本批证明，不并入本次修改。

先让新接口测试在旧实现失败；再改最小读取及四消费者。验证默认／None／空过滤、顺序、未知 run、控制文件身份、SQL 参数边界、选中坏 JSON、重复 call／frame 视觉语义；在新解释器重开真实 store，验证原事实可读且不重放。跑当前 QA／验证／事件／恢复及视觉相邻回归、必要 KernelOne／目录门禁，独立审查后集成。

最后对原不可变 prefix 比较真正修后实现与原 full 方法的结果和局部耗时。旧原库保持 failed，不改候选或收据。上述范围满足即可结束此主题，另起冻结原生运行检验交付；不把任何局部加速当平台完成。

## 最终实现验证

四个产品文件共 25 行新增、6 行删除（不含测试与文档），未增加控制器、表、缓存或权限。新真实 store 测试旧实现 26 failed、修后 26 passed；33 个工程测试模块共 532 passed、1 条 fixture 导入警告，五文件 Ruff／格式／Mypy 通过。KernelOne ALL 12 阶段通过（368 passed、1 skipped），单独发布架构检查 18 passed。套件存在交叠，不相加为独立测试总数。目录严格审计仍有 159 条历史诊断，报告 SHA 与 T21 一致；显式原历史 baseline 的增量检查 0 新问题、0 manifest mismatch。首次漏传 baseline 的增量失败保留，未改 baseline 或清除旧债。

主 Agent 随后对真实新接口及当前消费者纯读表达式进行了 full3693 前缀核验，每项三次：

| 路径 | 当前默认全读后 Python 过滤 reference | 实际 SQL 下推 |
|---|---:|---:|
| native turn 身份谓词 | 1.804215 秒 | 0.007781 秒 |
| pending_tool_calls | 1.800526 秒 | 0.006748 秒 |
| review_binding（保留当前 manifest） | 3.007252 秒 | 1.225064 秒 |
| 事件读取及视觉 producer | 2.134985 秒 | 0.428337 秒 |

12 次比较均正常返回、canonical 输出 SHA 相同；六份视觉输出均保留当前 3 图。字符串 seq、错误角色及缺失 seq 仍拒绝；原 SQLite、不可变副本、目标及全部原授权资产 manifest 和 4 个产品源码指纹前后相同。reference 是当前默认全读后过滤替身，不是整个历史 loop；测量期间其它回归并行，不是隔离统计或生产延时估计。原型实验 seq2966 的图片块为 0（绑定最终产物，旧图省略），seq3693 为 3，不能混写。

实际结果：`post-integration/newresults/verification.json`；独立复核：`causal-review/final-implementation-profile-review.json`；代码审查：`code-review/review.json`，无开放 P0／P1／P2。`implementation/` 和 `qualification/` 保留 RED、GREEN、回归、静态及发布检查。K1 ALL 原标准命令将报告写入既有默认 workspace/meta/governance_reports，已逐字归档 qualification/kernelone-all-report.json；后续命令应显式指定审计路径，未重跑或伪造结果。

这关闭了已识别的四处过量读取；manifest 成本、其它未迁移读路径和 Director 不交回控制仍有边界。r05 的 failed／not_run／QA 未调用保持，当前主实例未因此重启或获新交付证明。下一项分开验证通用交接说明或真实剩余预算，不能用本地提速替代最终网页质量与无人值守交付。
