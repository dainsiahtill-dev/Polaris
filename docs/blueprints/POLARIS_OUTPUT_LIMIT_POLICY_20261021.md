# 按模型上限执行的输出预算

状态：共享机制已实现，当前模型参数已真实核验；新的动画项目交付仍待验收。用户最终决定为“按照每个模型所提示的上限来”，不统一设置百万输出。

## 当前事实与第一偏差

冻结 `2c7b0c56e` 的原生动画运行 `1be9405400f34a8aa5c3ff12cd692218` 最终 `failed / not_accepted`，没有产出源码、图像、验证或 QA；内部 harness 原样记 UNKNOWN。PM、CE、Director 确实运行，原 owner 关闭了项目后端和前端父子进程。该失败不改写为成功。

最终物理请求中的 `max_tokens=3000` 来自共享执行器的 `cfg.setdefault("max_tokens", 3000)`。Provider 当时只配置 `max_output_tokens=65536`，执行器没有采用它。3000 是当时请求的配置预算，不是模型能力上限。多次截断后，最后的 `max_tokens + content:null` 又先被当作损坏协议而终止，未进入原来的截断反馈。

另有两个独立问题：实例初始化在两个位置同步构造 embedding 模型，首次启动发生两次身份检查超时后才恢复，具体耗时因果尚未确定；ContextOS 把 `unknown/not_applicable` 字符串当 truthy 显示 PASS。前者未在此修改，后者已按实际状态修正。

## 唯一状态 owner 与机制

沿用既有 LLM 配置存储、K1 执行器、TokenBudgetManager 和物理请求捕获。不增加角色合同、总账或控制器。

1. 没有单独设置 `max_tokens` 时，采用该 provider/model 配置的 `max_output_tokens`；`null` 视作未设置。较小的显式请求值继续保留。
2. 配置上限、模型配置能力边界、当前上下文可用空间和实际发送值分别记录。模型边界与真实上下文窗口约束保留；PM/CE 不再把最大输出上限解释成必须完整预留的空间，原需求与身份仍须保留。
3. `generation_budget` 保留 requested、ceiling、effective、来源和调整原因。`output_token_limit` 直接读取最终物理 body；与已准入预算冲突的覆盖不能悄悄发送。
4. 截断且缺失/空 content 的原始响应继续耐久保存，以零工具效果进入原角色的现有反馈。非截断协议损坏仍明确失败。不增加重试次数、超时、分块提示规则或项目特殊分支。
5. Coverage 只有 `true/present` 为 PASS，`false/missing` 为 MISS，`not_applicable` 为 N/A，其余为 UNKNOWN。原审计值保持不变。

## 当前已配置模型

| 模型 | 当前输出配置 | 证据与适用边界 |
|---|---:|---|
| MiniMax-M3 | 524,288 | 同一路由对百万参数返回 400 并明确给出此上限；四角色通过真实 K1 路径发送 524288 并成功返回。上下文配置仍为 1,000,000。 |
| deepseek-v4-flash | 393,216 | 官方明确 384K 的精确值；当前路由接受此数字并返回 OK。别名的服务版本可能变化，须记录实际响应。 |
| mimo-v2.6-pro | 131,072 | 官方 `max_completion_tokens` 上限；当前 `max_tokens` 数字请求返回 200，但短回复不能证明该旧字段是否被执行为上限。 |
| kimi-for-coding | 原 16,384 保留 | 当前订阅返回 403，会员路由输出上限 UNKNOWN；未用上下文窗口或其他 Moonshot 型号猜测。 |

只修改对应模型的输出控制字段；角色绑定、凭证、其他设置和上下文窗口保持原值。正式保存及冷读已核对。允许配置到百万的 schema 范围不表示任何模型都支持百万输出。实际生成能力、账单和物理 HTTP 总发送次数不能由短响应或快照数量推断。

## 实验、反证与停止条件

合成模型只用于接口、预算、恢复和拒绝反例；真实 HTTP 与当前 provider 探针分别标注。旧 `content:null` 原始响应在解析器上得到 RED→GREEN；新预算覆盖两个协议、四角色、invoke/stream、较小显式值、空值、元数据 cap、窗口不足和 wire 不一致。真实四角色探针省略调用方输出覆盖，检查配置经过完整执行器后确实进入 body 与审计。它不是项目交付。

若新正式运行仍发送 3000、预算与 body 不符、丢失角色/原需求、扩大窗口、QA 复用旧候选，当前解释或实现不成立。若预算正确但项目质量仍失败，也必须记录失败，不能以配置修复代替交付。

当前模块在相邻接口、必要回归和独立审查闭合后停止研究，冻结新源码与模型配置，重新运行同一任务要求的全新隔离动画网页。只有实际源码、真实验证、当前独立视觉 QA、主 Agent 质量检查及资源闭合均有证据，才能声称本次网页交付。原 120 项剩余 117 项、模型对照与规模经济性没有因此完成。长期记忆不做。

证据根：`~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task17-native-vision/output-boundary/`。模型官方链接及协议探针原始响应见 `per-model-limit-audit.json`、`per-model-route-probes/report.json`；当前 M3 实际四角色请求见 `live-native-cap-probe-20261021-233623-b5de6af3/`。
