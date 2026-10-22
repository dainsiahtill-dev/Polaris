# 当前运行预算可见性：限定候选

日期：2026-10-22。状态：r2 尾部观测17行候选已完成有界资格验证，并在冻结 `b41245103c47dcfae9dc7f4063861cc1ee9b1cac` 完成一次原生 r06：completed／passed／QA approved／accepted；ROOT task_success=yes、质量中等且保留缺陷，已知资源闭合。实际证据见[实验报告](../experiments/POLARIS_RUNTIME_BUDGET_VISIBILITY_20261022.md)。本例不单独证明 H2 交付收益。

## 事实与假设

r05 运行在原 deadline 前未进入正式验证或独立 QA。实际读到的 turn 2634、3030、3114、3686 物理 body 没有可用剩余秒数；host 已持有同一持久化 `RunLimits.deadline`，并用于限制实际工具／模型调用。2634 的快照时刻约剩2325秒，3114约剩1383秒；这不是模型实际收到请求时的精确剩余时间，也不保证足够完成 QA。

T23 的三组交接说明对照未观察到下一步验证／交回变化。因此不集成 H1 提示词，不追加一轮只能观察是否复述时间的六请求 H2 筛查。H2 仅为候选解释：模型看不到整个运行剩余预算，可能不能合理安排实现、验证和交付。真正的首次因果偏差仍 UNKNOWN。

研究事实见 `~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/preparation/`。最小完整实验采用一个新身份的原生运行；失败即如实保留，不靠增加预算或替补追求通过。

## 唯一机制变化

复用 `roles.runtime` 原 `run_engineering_loop` 和 `RunLimits`。在 Director 每次实际请求准备时，把 host 已有 deadline、观测 wall-clock 时刻及两者之差的近似剩余秒数作为一条临时 user message 附到完整工具结果／图片之后。保持原 system、会话前缀、当前图片、工具、权限、检查、模型路由及 cap；不加入 H1 交接段、停止命令、质量答案或强制留时。

只构造请求用的副本，不把动态预算作为新的持久化会话 message 累积，也不改原 system。下一次或冷恢复时从原持久化 deadline 重新计算；不复制旧运行或旧请求的数值。未配置 deadline、非 Director 调用保持原输入。总耗时／已用时间没有可靠来源时不补算或虚构。

首个已测实现把时钟追加在 system，组件语义检查通过后，ROOT 在冻结前识别了稳定前缀风险，未启动原生运行。动态内容放在 system 会改变后续长历史之前的前缀；[Anthropic 官方缓存说明](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)要求匹配缓存前缀。T23 原始 usage 也显示长输入存在真实缓存读取，但不是这次放置方式的成本对照。故改为尾部观测，并实测最终两个 native codec 的工具配对、图片和原 system 保留。该调整只承诺不主动改动既有前缀，不保证 M3 缓存命中或账单下降；r1 已有代码／测试／失败和资格记录保留，不能冒充 r2 证据。

状态 owner、SQLite、授权、取消、工具执行、验证、QA 及实际 deadline enforcement 均不改变。文本明确预算是准备时刻的观察，不承诺发送／模型收到时仍有相同余量，也不赋予时间或权限。不新增状态表、controller、后台 timer、轮询、API、配置开关或模型维护字段。

## 修改范围与局部验证

- `internal/engineering_loop.py`：同一 owner 内生成当前请求的预算说明；不改 provider cap、retry 或 completion 判定。
- `tests/test_engineering_budget_visibility.py`：真实 store／循环及真实 native codec 的限定实验；替身只替代模型输出和传输，不证明模型收敛。
- `tests/test_engineering_verification_ownership.py`：ROOT 修正真实 wire 消费测试对整段文本仅含一个 JSON 的假设；保留检查来源、退出码及最终配方断言，并核对合并在后的 host 观测。
- Cell、graph 的测试声明及本文、实施计划、验证卡同步；无新增跨 Cell 边界或 effect。

原 r1 两文件按字节保留；对原 system 放置方案观察到 3 failed／4 passed 的 r2 机制 RED。尾部方案在实际 Python3.12.3 的8模块共122项通过，包含新增7项；两文件 Ruff／格式／Mypy 的3.12类型环境检查通过。真实两轮循环、OpenAI／Anthropic codec 的 loopback body 与 ContextOS 物理快照核对一致，原 system、前缀、工具配对、图像、fixture cap 和持久化历史保留。新 service 重新打开 SQLite 后沿用原 deadline，不是新解释器或任意崩溃恢复证明。

ROOT 首轮 r1 整合在 `.venv` Python3.13 为584 passed／5 failed，均因测试端缺PIL；选实际原生Python3.12.3（Pillow10.3.0）复验相关36项通过，旧失败保留。r2 再以实际3.12完整运行589项：588通过，1项因测试把 JSON 后合法合并的预算文本也交给 `json.loads` 而失败。ROOT 单独复现并保全实际四次loopback请求／native快照，确认必需／自选检查来源与退出码9均完整。只修测试解析及尾部断言后，整个验证来源模块与预算模块共9项通过，类型／格式检查通过；产品17行未再改，没有第三次整跑或把这些重叠结果相加。

r2 的 K1 ALL十二阶段通过（368 passed／1 skipped，含release tests），严格目录原159条报告字节与旧版本相同，显式历史基线增量零新增。独立 r2 代码审查无开放 P0/P1/P2。详见外部 `implementation-r2/`、`qualification-r2/`、`feedback-consumer-fix/` 与 `review/`；r1资料另保留，不能冒充r2或模型收益。

## 集成、原生实验与停止条件

以下保留冻结前确定的执行约束；本次第4—6步已实际完成，终态与未完成研究见后文，不再将原生实验列为待启动。

局部与相邻回归、必要独立审查、K1 ALL／目录增量检查通过后冻结源码和关键配置。原始 strict 目录债务保留，不改写基线。组件通过只允许进入原生实验，不宣布无人值守质量或 H2 优势成立。

用同一原始鹈鹕动画网页需求、PM→CE→Director 协作、相同角色路由与工具／视觉／QA 要求，新建隔离 workspace。项目仍5400秒，外层6000秒，不增加单次 timeout、retry、替补、人工修复或模型输出限制；原必要检查不降。运行期间冻结文件不变，所有产物由 Polaris 自身工具生成或修复。

实际接受标准是：当前候选真实检查通过、有效独立 QA、ROOT 亲自检查原画面与交互质量、原 owner 完成结果提交和资源闭合。达到 deadline 或 terminal 即结束本次尝试并封存；失效 QA、超时、缺失或未知证据不能判成功。记录全部尝试、实际 usage／耗时；HTTP 次数及账单无物理证据时 UNKNOWN。

一个新运行即使通过，也只证明这个冻结候选的一次完整结果。相较 r05 还包含此前 T22 读取优化、随机输出及时间条件差异，不能单独归因于 H2，更不能证明最佳组合或平台优于其它 Agent。没有改善时不增加停止规则或延长时间，沿第一失败 owner 决定后续。

## r06 实际结果与剩余边界

run `7dd30e15505e42c098daf90602625d94` 在5173.18秒完成，原必要检查实际exit0、当前709项输入绑定及3个产物哈希与QA批准一致。原生与ROOT已知资源分别闭合；无外部代修、装依赖或代跑原生验收，冻结源码／关键资产未因检查器修复改变。ROOT亲看16张最终原图，结合200张原始截图与实际键盘／DOM序列，六维评分为鹈鹕3、车2、骑乘3、运动3、交互3、呈现3；后齿轮位置／转率与reduce控件状态不一致两项缺陷保留。

401条工具观察含16失败、0pending；PM／CE／Director／QA prepared分别3／4／377／13，397 prepared与397 terminal为快照相位，非HTTP发送数。harness用量摘要仅18观察且覆盖未知，全量usage、HTTP、账单、人工分钟UNKNOWN。资格仍是588通过／1测试消费解析失败后相关9项通过，产品17行未再改；K1 ALL十二阶段、严格目录159既存／零新增，不能称单次589通过。

外部补充检查器R1失败退出2及清理字段false保留，ROOT独立核对已知进程／端口闭合；仅外部检查器修正后R2实际观察成功。这不是平台失败被擦除或目标代修。预算送达／被注意与单案例可行已观察；H2因果、缓存／经济收益、最佳角色组合、ContextOS相对优势及全120项仍未证明。seq3893／3925同输入仅revision变化导致视觉证据失效的反例是T25待研究项，不在本次结果中标作已修复。
