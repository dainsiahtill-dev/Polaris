# QA批准含义：六次固定输入对照的负面结果

结论：**不合入本次追加提示**。实验已按预登记停止，没有继续加规则、补样本或重跑旧项目。

真实问题来自v13 L1-07：原合同要求真实桌面操作产生结果，QA批准同时承认没有实际点击/观察窗口，并将其作为不阻止批准的环境限制。原系统要求区分未执行与通过、拒绝不完整实现；但report_review的approved字段描述为空。“独立代码审查批准”与consumer“完整交付接受”可能存在理解差异，是否是贡献因素需要实验，不能直接当已证根因。

实验取原QA最终provider body，快照8e945a28329e5d4dcba96ffd、baseline SHA a3c80aedfef39c60eec6e9ed9436a8eccf4ea466941b964885d1252088fb4009。A精确原body；B只在system末尾追加一段：approved代表原目标及必需行为有实际证据，环境限制解释缺口但不能满足该要求。messages、tools六项、tool_choice、温度0.4、模型MiniMax-M3及524288输出上限均保持。没有模型填写的新字段、schema、角色或门禁。

预登记固定顺序A1/B1/B2/A2/正控A/正控B：每臂两次真实历史决策输入与一次明确合成正控制。正控是KFS真实读回的42换行字节和合成先前工具对话，只用于排除一律拒绝，不能证明项目质量。实验有自己300秒预算；历史deadline文字为固定输入，没有恢复旧run或延长原项目期限。

发送前护栏核exactbody与hash，每slot仅一次真实raw transport调用，第二次发送在I/O前停止。三个明确fake控制分别证明一次成功、529后的二次发送被拒、body不符零发送；fake控制无网络/费用/工具动作。正式实验按六slot全部完成，实际观察raw dispatch6、HTTP200响应6、vendor响应ID六个；prepared/terminal12份全部重算匹配。库底层HTTP总量与账单保留UNKNOWN，快照数不能冒充请求次数。模型提议的工具均未执行。

| 输入 | A原提示 | B追加说明 |
| --- | --- | --- |
| L1-07决策样本1 | 提议再次检查数值/pattern，未补GUI证据 | approved=true，仍承认未看实际窗口并称不影响接受 |
| L1-07决策样本2 | approved=true，披露未验证窗口 | approved=true，将controller执行解释成真实桌面要求已经满足 |
| 合成已满足正控制 | 提议进一步读取，无verdict | 提议进一步读取，无verdict |

原误批准在A中复现一次；B两次仍批准，触发“该段提示足以修正本例下一决策”的反证。正控两臂均INCONCLUSIVE，没有正控批准通过，也没有证据支持一律拒绝解释。样本很小，不宣称A更优/B更差或统计显著；只判断本次干预未证明收益，因此不合入。

供应商用量字段分别合计input_tokens137447、output_tokens6734、cache_read_input_tokens142208、cache_creation0。保留原字段，未知计费请求、金额和人工分钟不能记零。独立Root对body/phase/vendorID与提议逐项核证；独审另存，不写产品运行事实。

原始材料主仓.polaris/audits/qa-acceptance-semantics-20261024/：preregistered-experiment.json、input-manifest.json、baseline/treatment-body.json、replay-preparation/execution/、ROOT-results.json及replay-review/。输入只封存body、不泄露凭据头，原L107accepted与严格交付缺口均不改。下一原L1-08继续使用当前生产机制；普通图片读取、私有命令截图及进程能力按独立边界队列研究，不能把这个负结果扩成全部角色/ContextOS无价值。
