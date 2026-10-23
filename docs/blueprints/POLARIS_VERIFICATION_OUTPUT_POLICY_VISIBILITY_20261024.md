# 验证输出目录的可读交接

状态：最小实现已完成，新回归12通过、相邻105通过、类型与样式通过；ROOT发布检查与独审通过，本次集成main；新项目收益未证。设计先封存于主仓.polaris/audits/verification-output-policy-visibility-20261024/design.json与ROOT-ruling.json；本文是实现后的文档同步，不追溯伪造修前docs时间。

真实问题来自v12 L1-06 R03：host原保存声明dist，目录未存在，实际Verifier有效写根为空；模型只看到effective[]。首次原生request中出现的dist仅是依赖包名，不是其实际授权声明。Director收到真实错误并自主修复，最终原生accepted；原项目ROOT质量FAIL保持，目录信息修复不处理其业务质量。

数据流：既有saved run → EngineeringRunService → engineering_loop临时host提示 → 实际最终provider body；既有Verifier实际scope构造 → 本次input_binding.verification_output_policy → SQLite完成事件 → 工具反馈/冷读取。工程owner、Store和权限源不变。

职责与设计：service只从原saved字段传静态声明；loop只在Director实际有verify工具时追加request-only提示，不写历史；Verifier记录构造时declared/effective/absent及原因，旧scope.output_roots和_profile形状保持。本次只改三个既有生产文件，无新Cell/controller/表/schema或模型填写协议。初始展示不调用_saved_outputs或探测FS作为新准入；Root裁决否决了设计初稿中的该建议，原effect/recovery检查保持。

事实是信息丢失及已进入最终请求的修后事实。帮助模型更早选择合法下一步、降低时延或成本仍是假设，未用付费模型/新项目验证。反证包括：原body已有真实声明；新信息未进入物理body；显式空继承host、旧未知被补值；新提示造成合法read被拒；profile改变使旧完成失效；目录或权限增长。出现任何一项均不得接受。

有限实验：有效RED为6失败/1既有control通过，包含五类信息交接；首次R1队列耗尽属于fixture错误，修正后保留原模型/传输链路复现缺失，不计为因果RED。新12回归核实际service/native client/本地HTTP/final_physical_request.body、多阶段body对应；检查缺失/已存在目录、空与未知冷恢复、attempt构造后FS变化冷投影、拒绝与合法read成对。模型回复/config为明确合成替身，其余SQLite/KFS/sandbox/真实HTTP与capture保留；不能证明真实模型会作更好决定。

权限、错误与恢复：声明是历史host数据，非当前写授权；不存在目录只解释原因，不自动创建。普通工具仍受原scope约束；Verifier只消费构造出的有效根。旧None保持UNKNOWN，[]保持空，旧completion无新字段仍按原freshness规则读取。symlink/越界/当前host拒绝不伪装为absent；效果路径拒绝不因显示信息放开。保存原attempt事实，不在冷读时用当前FS重写历史。

接受终点：有效新断言/安全控制、相邻、静态、既定发布检查与独审通过后main集成，另开新冻结批次执行原L1-07；不再追加图片、进程、自动finalization或目标质量修复为本模块门禁。原始证据在上述.polaris目录，实际ROOT结果将随后补录。descriptor评估：无公开角色/工具签名或可发现能力改变；仅Cell/catalog/subgraph的测试与说明同步，无广泛生成器重写。

ROOT最终资格：架构40、KernelOne ALL十二阶段（368通过/1跳过）、目录增量0/manifest mismatch0通过，既有159条诊断保留。独审另行12/12、实际8个本地HTTP请求/完整body、Ruff/format/项目mypy通过，未发现P0/P1。32份当前源码union全部重算匹配；无额外模型或目标动作。证据root-acceptance.json与independent-review/report.json在上述审计目录。达到组件停止条件，不扩题；下一原L1-07已准备原四输入及单独Java窄根host，待源码提交后正式冻结启动。
