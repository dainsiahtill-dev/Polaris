---
status: accepted
date: 2026-11-12
scope:
  - "polaris/cells/instances/internal/service.py"
  - "polaris/delivery/cli/backend.py"
  - "src/frontend/src/api.ts"
  - "src/frontend/src/services/instances.ts"
---

# ADR：把实例的本地端点与发布地址拆成两个事实

## 状态

已实施（accepted）。实例记录同时携带本地端点与发布地址；渲染器对被策略拒绝的跨源后端改走同源代理。

## 上下文

实例默认只绑回环地址，因此另一台宿主机（例如 WSL2 工作区的 Windows 宿主机）打不开实例页面，也读不到实例 API。Supervisor 从未把 `--host` 传给 backend 或 renderer 进程，两者都落在 CLI 默认的回环地址上；注册表随后公布一个消费者拨不通的地址。

为了让实例可被另一台宿主机观察，第一版直接把注册表里的 `backend_url` / `frontend_url` 换成了发布地址。结果是本地控制面与验收路径一起失败：验收要求实例端点必须是回环来源，对外地址被判定为 “instance endpoint is missing, foreign or reserved”，Bench 运行在启动阶段就以 ValueError 结束。同时渲染器把这个跨源后端带进实例页面，而随包发布的 connect-src 策略只允许页自身来源与回环来源，浏览器因此拒绝该来源的每个请求和每帧 runtime WebSocket，页面显示“实时连接已断开”。

## 问题陈述

一个实例需要同时满足两个互相冲突的约束：

1. 本地控制面、验收路径与 Bench runner 只允许触达本机上的实例端点，地址必须是回环来源，否则 fail-closed。
2. 远端消费者需要一条可拨通的地址，才能在另一台宿主机上观察实例页面与实时通道。

把这两个事实压在同一个字段上，任何一次修改都会破坏另一条约束。

## 决策

把“本地端点”和“发布地址”拆成两个独立事实：

- `backend_url` / `frontend_url` 保持回环，本地端点的语义完全不变，验收与 Bench 的回环契约原样成立。
- `public_backend_url` / `public_frontend_url` 新增为发布地址，指向消费者实际能拨通的宿主机地址；未配置公开地址时回落到回环。
- 绑定地址与发布地址分开配置：`KERNELONE_INSTANCE_BIND_HOST` 决定进程监听地址，`KERNELONE_INSTANCE_PUBLIC_HOST` 决定注册表公布的地址；通配绑定地址不是可拨通地址，因此默认回落回环。
- 渲染器不再把绝对跨源后端带进实例页面：实例页面通过自身来源的 dev proxy 触达本实例的后端（每个实例的 renderer 都以 `KERNELONE_BACKEND_PORT` 指向自己的后端），该来源落在 connect-src 的 `'self'` 之内。对策略不允许的显式后端绑定，渲染器忽略它而不是让浏览器逐个拒绝请求。

## 后果

收益：实例在另一台宿主机上可被观察且实时通道真实可用；本地控制面与验收路径继续 fail-closed；实例身份、token、workspace 绑定保持完整；默认行为（未配置公开地址时）与改动前完全一致。

技术债：注册表多出两个字段，任何消费注册表投影的地方都必须明确自己需要本地端点还是发布地址；发布地址的可达性依赖部署方正确配置 `KERNELONE_INSTANCE_PUBLIC_HOST`，配置错误时消费者会连不上而不会退回到别的地址。

## 验证方法

- `pytest polaris/cells/instances/tests/ -q`（含新增的绑定与发布地址回归用例）
- `pytest polaris/delivery/cli/tests/test_backend_instance_publication.py -q`
- `npx vitest run --config src/frontend/vitest.config.ts src/frontend/src/api.test.ts src/frontend/src/app/launcher`
- 浏览器证据：从另一台宿主机打开 `http://192.168.10.2:5174/?instance=...`，记录零 Content Security Policy 违规、零失败请求，以及 runtime WebSocket 持续送达帧。

对应验证卡：`docs/governance/templates/verification-cards/vc-20261112-instance-published-address.yaml`。
