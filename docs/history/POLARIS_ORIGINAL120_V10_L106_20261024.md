# 原120项 v10：L1-06真实运行与个人复查

项目“月球邮局明信片生成器”，原源码冻结d152c0e49c94b23cf4fa170c5f684774fa4f87ca，run bbe52b3382384f93ba56184f5414c2a1，工作区 `/home/dains/Documents/polaris-bench-projects/original120-20261023-v10/L1-06`。9497份源码/执行文件、配置、原模型和5400/6000秒预算在完整运行期间保持；没有热修、外部目标编辑或代装依赖。

**项目交付失败**：原生failed／verification not_run／QA not_run／ProjectOutcome not_accepted。**产物在已观察场景通过**：ROOT实际调用现成ELF20次、亲看四原SVG和两私有变体，但这些不代替正式验证或QA，不升级原失败。

## 终止的第一偏差

Director在1353通过原授权工具清理可选dist、写.gitignore，1354实际exit0。原SVG/TXT成品仍在examples；下一模型请求准备因EngineeringVerification把未来输出写许可重新解释为“目录现在必须存在”，在1360抛出verification output is unavailable。目录实际缺失，不是猜测的inode漂移，也没有工具调用丢失。没有进入正式检查或独立QA。

[输出许可与推进方案](../blueprints/POLARIS_OUTPUT_DECLARATION_LIVENESS_20261024.md)只分开许可声明和本次实际可写挂载，保留完整原声明与安全校验；每次新验证attempt更新有效子集，单次attempt冻结，查询不mkdir、不添权限。真实夹具基线capture后删除目录使第三请求不可达；无初始capture同权限对照可继续。最终修后原capture反例也能走到实际检查和独立QA夹具。这不是新模型项目交付结果。

## 角色与执行事实

PM、CE各实际运行一次，Director持续处理工具、编译、测试和视觉反馈，普通失败没有重启规划。120条逻辑prepared请求来自原journal（PM7、CE14、Director99）；242份物理请求相位快照来自121对prepared/terminal，其中Director相位100。二者不混为实际HTTP数，费用和全部HTTP保持UNKNOWN。

PM5343字、CE4325字最终建议确有计划/架构内容，并逐字进入Director首份物理请求；31条PM/CE工具及反馈链完整。CE较早草稿不自动交接，只有最终摘要传入，影响未知，未声称角色增益。Director实际请求已包含新独立QA指导、9个工具及524288输出上限/100万窗口。

实际普通工具输出出现103条核心断言与83条入口断言全部通过，并实际打开多张正常、长姓名、特殊字符卡。它们仍不是正式Verifier或QA。私有home中的CMake安装不跨命令保留；实际schema已经明示fresh/discarded，不能把这一现象直接当平台违约。Director最终用现有g++/Make完成真实产物；窄持久环境载体仅作后续设计，未实现。

## ROOT个人检查

8个只读无网络K1观测调用，共20次现成CLI：13对SVG/TXT、6个非法输入和1次地点列表。原35文件和build/moonpost（156592字节、nlink1、SHA d8eac0c144d21847e04fa508ff59de60389592ccd7ca10be0fa031078ca79a52）前后保持不变。输出只在私有/sandbox-output，独立观测不能补齐原生成品或原生收据。

- 同输入seed42的SVG/TXT字节完全复现；43/0实际改变星点坐标，问候保留。
- 三个地点改变地点码和诗句意象；问候变化实际改变诗句并保留用户实词。
- 13对输出XML解析、收寄人、日期/星期、三行语义诗句及问候一致；不是只检查文件存在。
- 特殊字符按文字显示；长姓名和120码点问候在所看画面换行，没有覆盖地址或冲出页面。排版朴素，未宣称高端视觉质量。
- 2026-02-30、2026-02-29、1900-02-29、空问候、121码点与小数seed均非零拒绝，两私有原有效文件hash不变。

限制：未独立重编译证明源码到ELF完全对应，未测任意无界姓名/所有极端组合/未知地点或缺参数；build编译缓存仍不在项目.polaris，未外部搬动或清理。四原SVG与两私有SVG来源明确，不把观测私有变体当原交付。

## 封存与资源

- 项目.polaris/audits/root-personal-quality.json及quality-preparation/ROOT-semantic-comparison.json：个人结果与20次原始输出、六张图。
- 项目.polaris/audits/root-first-output-binding-failure.json：1353/1354/1360及原物理请求引用。
- 项目.polaris/audits/root-all242-final-requests.json：242份归档/当前快照字节、wire hash、身份/原目标/工具/预算全部核对无错，当前缺失0；不等于242次HTTP。
- 项目.polaris/audits/root-terminal-resource-recheck.json：pending0、backend81524/frontend81663及driver/archive已退出，49978/5174关闭；主实例三个进程birth不变。
- 主仓.polaris/audits/output-declaration-liveness-20261024/：实验、修复、资格与独审；主仓.polaris/audits/command-state-boundary-20261024/仅后续设计。

累计本轮原目录6个不同项目已终态并个人复查：5个产物FAIL、1个所测产物场景通过；完整无人值守交付通过0，其余114未启动。来源包含多个冻结版本，只作覆盖统计，不作单版本成功率或架构对照结论。
