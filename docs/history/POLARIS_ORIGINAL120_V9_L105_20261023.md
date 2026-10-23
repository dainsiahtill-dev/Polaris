# 原120项 v9：L1-05 运行与亲检

本次真实项目未成功交付。源码 `cce3e862b90164ba60a235dc02cc2fdbbbc4e075`，run `90b231ec129e4c39907f3b9834a96dea`，工作区 `/home/dains/Documents/polaris-bench-projects/original120-20261023-v9/L1-05`。PM、CE、Director 实际运行，独立 QA 没有开始。单次正式取消请求 HTTP 超时，随后持久事实确认 cancelled／verification unknown／QA not_run／ProjectOutcome unknown；不重复 POST、不改写历史。原5400秒项目预算与6000秒外层预算没有扩大。

原v8四项与本项合计：5个不同项目已启动、终态、个人复查并释放资源；严格交付通过0，未启动115。历史其它版本尝试保留，此数不是成立以来尝试总数。

## 机制证据

显式工具链绑定在本次正式路径生效：沙箱中的裸 cargo/rustc 实际返回1.96.0。后续 Director 自行修正构建输出根，实际输出显示 release 完成及30+5+9测试通过；历史管道曾掩盖内部失败，所有这些失败保留，不能据最后一次表面退出0宣布整个项目成功。

交接卡点：最近三组循环，20个业务文件及八份SVG内容哈希不变，只发生构建/复制元数据变化。capture-final 包含真实图像；verify-final 无图像。可写预检使当前验证失效，下一验证frame又不满足Director图像终检，QA始终未开始。合法的仅截图下一步仍存在，未证明机械死锁。

[交接蓝图](../blueprints/POLARIS_DIRECTOR_QA_HANDOFF_20261023.md)提出必需独立QA承担最终视觉验收，保留Director自主视觉能力与无QA时的视觉终检。七个有限模块控制已经实际执行，原路由QA到达0、处理路由1；缺失/失败检查、QA缺源码或图片仍拒绝。模型、QA到达及继续执行为明确替身，真实Store/Verifier/隔离命令保留；控制不产生批准或交付成功，不能证明模型质量或经济性提高。

## ROOT个人质量

只读调用已由平台构建的release ELF10次，8个K1隔离观测，未执行外部编译、测试、安装或目标编辑；私有SVG输出只作为观测，未补造项目产物。完整硬链接清单为2条、nlink2，执行前后原20文件和二进制字节/元数据保持，实际参数和原stdout/stderr留存。

亲自查看四张原始SVG：50/50、全零中性、鲜5满盘、XML特殊菜名均有正面证据。负值、6、小数、缺少味觉四种实际输入均返回2，保留私有空间里原有有效SVG字节；同参重复SVG哈希相同。

质量FAIL，去重为一类“建议与区域不一致”：辅味画在单侧，却要求左右分置；每侧50%与两侧合计50%矛盾；全五味并列时配料区共60%，建议却不超过30%；独占单色仍给出双色建议。这些来自实际CLI与卡片，不是源码猜测，也不是多个独立平台缺陷。QA尚未运行，不能把它计成QA已漏判。

第一次外部全页截图超时保留。仅改为视口截图后，四个原780×660 SVG在1100×900内完整可见，3000ms未扩大、CLI未重跑，四张原图已个人查看。两次浏览器的driver/context/本地bridge均闭合。

## 证据与边界

- 项目 `.polaris/audits/root-personal-quality.json`：个人结论、10次CLI与4张图片。
- 项目 `.polaris/audits/root-native-recheck/quality-first-failures.json`：去重语义失败与原始输出。
- 项目 `.polaris/audits/independent-v9-convergence/report.json`：三组真实请求、候选、图像与验证对照。
- 项目 `.polaris/audits/root-convergence/resume-terminal-resource-recheck.json`：持久取消、pending0、实例进程/端口关闭；主实例三个进程birth未变。
- 主仓 `.polaris/audits/director-qa-handoff-20261023/`：七个控制、RED/GREEN回归、独立审查与新版本资格（状态以原文件为准）。

632份最终请求快照封存，不能等同632次HTTP；全部实际HTTP、账单、人工分钟仍UNKNOWN。原编译cache仍在target与dist/cargo-target，尚未统一进项目.polaris，未移动或清理。独立复查没有重编译证明最终ELF与当前源码完全对应，未声称完整长菜名压力通过。新生产交接变更须另行资格、提交、冻结与正式项目验证，旧取消不升级为成功。
