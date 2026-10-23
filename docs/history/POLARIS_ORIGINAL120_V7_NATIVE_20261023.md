# Original120 v7 原生尝试与质量复检

源码f71eb109a5070b21c84667ccf81c0be761a3797f。原120目录及host不改；相较v6只把已无余额的DeepSeek QA切到已有M3，属于尝试间配置介入，不是模型优劣对照。没有外部目标修复。

本批实际启动1项：L1-01，run `5ff76e3713924bb9974dbcd7b662a208`，其余119项未在本批启动。该批有效交付0项，不能扩展为历史所有批次的唯一项目数。

结果：正式cancelled，最终verification unknown（取消发生于最后复验内），QA未调用，ProjectOutcome unknown。项目driver2157.02秒，wrapper2173.05秒；原5400/6000秒预算保留，未跑完预算。取消原因为已封存的重复验证/重写预览/失效循环，不归为Provider timeout。终态seq4881、pending0，原生实例与端口已释放；main/NATS/Launcher原birth保持。

真实观察：PM与CE各2个logical call，原任务及建议在实际provider body中完整继承；Director产生代码、依赖准备、28核心测试与build，并自主吸收工具/测试/浏览器绘制错误。正式验证至少10次，Director执行段至少14次后仍未进入QA。258份实际请求phase快照全部封存，0缺失/0观察错误；快照数量不等于真实HTTP发送次数。物理HTTP总数、账单与人工分钟UNKNOWN。

第一工具拒绝是模型写工作区外placeholder，权限正确拒绝且完整反馈在下一实际请求保留。新的共性缺口是file-origin浏览器无法加载普通构建模块：613的实际CORS错误及独立路径控制都已封存。后续同字节preview重写仅mtime/ctime变即使当前验证失效；原强绑定语义没有被放松。

ROOT亲检实际浏览器产物，采集94张原图，亲看11张。两轮各100次公开单步的10秒RGBA与累计闪光序列相同；八种非法输入保留旧画面与时间；悲伤/兴奋10秒累计28/140；新月/满月周期计数相同、实际亮度不同，均为本次有限观察。质量仍FAIL：暂停月相立即改变场景；renderer不消费闪光时间而使发光恒定，counter不能替代可见闪烁。不同种子完整轨迹、单虫闪序与nearest tie独立验证仍未知。

证据入口：`.polaris/bench/original120-20261023-v7/freeze-closed.json`；目标项目`.polaris/audits/root-personal-quality.json`、`root-quality-v7/`、`root-runtime-archive/`、`independent-v7-requests/`、`independent-v7-qa/`。9480份源码/受控operator文件和冻结输入全未变化。历史失败与取消保留。

下一步按[离线Web入口方案](../blueprints/POLARIS_OFFLINE_WEB_ORIGIN_20261023.md)关闭一个工具接口，资格通过后再新冻结原L1-01。TaskMarket/Scout/交接质量三项条件实验已列入原特性恢复研究，仍非本项启动门槛；长期记忆不做。
