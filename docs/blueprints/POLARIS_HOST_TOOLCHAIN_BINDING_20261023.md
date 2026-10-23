# Host 工具链目录的显式绑定

状态：设计已选定，实施与接受证据待补；不是交付能力证明。基线 main `16c8dbf67a96c0960e323aba617d24209960629f`。原目录 v8 四次运行均已终态、个人复验与资源闭合；旧失败不改写。

## 问题、事实与假设

L1-04 首个真实命令报告 go not found。宿主已有 Go1.23.8，但当前 profile 未声明其窄读根，K1 固定 PATH 也不包含该目录。Director 在多个独立命令内重新下载 Go1.22.5，并出现 SSL 失败。私有 /tmp 每次重建是现有安全语义，不能把下载失败、编译失败、测试管道 exit0 或最终模型连接失败混成一个根因。

第一偏差位于 host 环境配置到实际命令环境的边界。原接口只能声明可读目录，无法明确配置额外命令搜索目录；上下文、模型输出和 read_roots 不应暗中扩大权限。

最小实验已真实运行：同一空合成 workspace、同一绝对 Go argv、空写根、禁网和 K1 物理 owner，A 无外部挂载 exit1/ENOENT；B 只增加已安装 Go 的窄只读根，exit0 输出 go1.23.8。源码、工具链关键5文件和夹具未变。证据：`.polaris/audits/host-toolchain-mount-20261023/ROOT-result.json`。这只证明窄挂载的可见性；PATH、持久化、冷恢复、编译和完整交付收益尚未证明。

假设：显式 host 工具链目录经过同一权限与恢复链进入 K1 PATH，可让合法命令复用已授权的现有编译器，减少重复准备。如果仅加窄挂载仍无法执行绝对 compiler，原解释将被推翻；如果接口丢字段或撤销后仍执行，则实现不能接受。

## 架构与职责

```text
Host permission profile / Settings / frozen host JSON
  -> PermissionScope.toolchain_bin_dirs
  -> EngineeringRunService + engineering SQLite 保存原授权
  -> 当前 host policy 校验（创建、恢复、每次副作用）
  -> Director / QA role scope / Verifier scope
  -> EngineeringTools._process
  -> KernelOne resolve_toolchain_bin_dirs + 原 sandbox + 原 process owner
  -> 当前固定 PATH 后追加有序 host 目录
```

已有 Cell 和 K1 owner 不变，不新增 controller、任务表、ledger、工具、模型业务合同或目录扫描推断。PM/CE/QA 的角色与写权限仍由现有角色策略决定。

- `toolchain_bin_dirs` 是 host-only 有序目录元组，默认空；不接受模型参数赋权。
- 缺省与显式空均不增加目录。空 payload 按原编码省略，保留旧 scope/request key/候选绑定字节；旧空运行恢复时不能继承新 profile 的目录。
- 目录必须绝对、真实存在、无 NUL/冒号，并 canonical 化；只能位于已经声明的目录只读挂载或原有系统只读挂载内，不增加 mount。拒绝控制根、私有临时根及与写根重叠的目录。
- K1 public `resolve_toolchain_bin_dirs(dirs, *, workspace, read_roots, write_roots)` 仅返回有序 canonical 目录，不执行、不验证整个 scope、不授予新权限；完整旧挂载安全规则继续由原 builder 执行。
- 当前固定系统 PATH 保持前缀，之后按 host 声明顺序追加。该配置不覆盖系统同名编译器；需要严格版本选择时仍可使用已授权的绝对命令。
- 恢复保持原身份、原权限、原目录顺序与 deadline；新 host policy 只可进一步限制，撤销或目录失效必须在副作用前明确拒绝。
- 不改变命令取消、超时、未知效果、物理 drain、工具归一化、最终请求或产品实时单轨语义。

## 最小迁移与验证

ROOT 独占 K1 sandbox、process public export 与既有 sandbox tests；实现者负责 host config/bootstrap、PermissionScope、session/service、roles、verification、tools 八个生产文件和相关组件测试。完成全部 producer/consumer/序列化/冷恢复路径后，才允许下一候选运行。

先用真实 K1 合成外部工具目录复现：只读目录已挂载但普通命令不能通过 PATH 找到；新显式目录使同一普通命令执行，且仍不能写工具目录、读取控制根、连接网络。与工具目录无关的原默认 sandbox 回归继续通过。

角色/host 组件实验覆盖：host JSON与Settings roundtrip、完整 server 保存、无 server 的普通 partial 保存、空值清空；SQLite 新实例冷恢复、旧 payload 缺省、显式空不继承、目录撤销；QA/Verifier scope 不丢字段；每次工具副作用重新校验；相邻实际工具执行与退出结果。夹具只替换外部编译器为明确标注的小型可执行脚本，不证明真实语言项目完成。

接受条件：原反例失败/修后合法路径通过，必要非法反例在副作用前拒绝；相邻 scope 消费者、类型/样式、架构门禁、K1 ALL、catalog fail-on-new 和独立审阅满足要求。之后提交 main、冻结新源码/host 配置/工具链证据，另起原目录批次继续 L1-05。模块成功不能计入原120交付成功数。

## 停止与边界

不在本修改中改变 /tmp/HOME/cache 私有 tmpfs 的生命周期，不开放 host .polaris 给目标进程，不做长期记忆、自动安装、通用 env override、旧 TaskMarket 准入或 QA 提示词修改。已有匿名临时空间与用户项目持久根的语义差异保留为独立待核项。

收益须通过新冻结运行实际确认：首次合法 compiler 使用、重复下载、真实检查、时间和全部尝试成本。HTTP 次数/账单未知仍 UNKNOWN；成功数为0时单次成功成本未定义。
