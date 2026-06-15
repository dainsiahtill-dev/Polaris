# F15 — Mid-run backend death must not freeze the Director dispatch

> Blueprint (§4.1 two-phase). Scope: `polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py::_drive_director_workers`.
> Date: 2026-06-15. Author: weak-model harness hardening loop.

## 1. 现象 (Symptom)

r42 在 03:09 LAN 后端 (`192.168.10.11:8189`, provider `openai_compat-1781325474837`) 中途死亡后整体
**停摆 6h21m**，QA 从未运行，产物只写了 `style.css` / `readme.md`，`index.html` / `main.js` 缺失。
4-backend Director 并行池里只要有一个后端在运行中途掉线，整条 dispatch 就被冻住。

## 2. 根因 (Root cause)

两层叠加，**没有一处真正是“无超时的 HTTP 调用”**——HTTP 层其实有 60s 默认超时
(`normalize_timeout_seconds(None, 60)=60`)。真正的冻结是 **dispatch 层的毒丸循环 + 无界 join**：

1. **毒丸再入循环 (poison re-claim loop)**：
   - 绑定到死后端的 worker 在 `consumer.poll_once()` 里跑一个 director turn → LLM 调用 60s 超时失败 →
     `_process_claim` 落入 `except Exception` → `fail_task_stage(requeue_stage="pending_exec")`，step 立刻回到
     `pending_exec`。
   - 该 worker 失败返回后 `while not stop` 循环里立刻 `continue` 重新 `poll_once`（immediate re-poll），
     而存活 worker 此刻在 `time.sleep(poll_interval=0.05)`。死 worker 在微秒级再次抢到刚 requeue 的同一个 step。
   - 如此 claim→fail(≈60–90s)→requeue→re-claim，直到 `max_claims_per_worker=256`。
     `256 × ≈90s ≈ 6.4h`——与实测 6h21m 吻合。
2. **终止条件 + join 双重冻结**：
   - 终止判据 `all(idle)` 永不成立：死 worker 永远在 claim 中（`idle=False`），存活 worker 抽干后只能空转等它。
   - `for thread in threads: thread.join()` 无界——即使 stop 置位，主线程仍会无限等待卡死的 worker。

死后端的 step 虽然 requeue 了，却被**同一个死 worker 反复抢回**，永远到不了存活后端。

## 3. 设计原则 (Design principle — 用户裁决)

> 「挂死的 worker 不能冻结全局，其租约到期后 step 自动 requeue 到活后端」
> 「即便其中有些掉线了（就跳过/忽略），依旧能够跑」

死/挂的 worker 必须**停止抢单并自我退场**，把 requeue 的 step 让给存活后端；任何单个 worker 都
**不可能**冻结整池。修复全部落在编排层 `_drive_director_workers`，**不**改 consumer / market / provider，
blast radius 最小。

## 4. 方案 (Two layers, both in `_drive_director_workers`)

### Layer A — Worker 自我退场 (primary, handles poison loop)

每个 worker 维护 `backend_failures` 连续失败计数：

- **后端存活信号 → 清零**：claim 批次里任一 `ok=True`，或失败 `reason ∈ {step_target_missing,
  repair_shrank_file, scope_conflict, missing_blueprint}`（这些证明模型**真的跑了**，是内容/竞争/载荷问题，
  后端健康）。
- **死后端签名 → 自增**：`poll_once` 抛异常（timeout / connection / circuit_open），或失败
  `reason == missing_execution_evidence`（空输出——死/挂后端的特征）。
- 失败的 claim **不**置 `idle=True`（避免与“市场抽干”混淆而误触发 `all(idle)` 提前终止）；保持 `idle=False`。
- `backend_failures >= DEATH_THRESHOLD`（env `KERNELONE_DIRECTOR_WORKER_DEATH_THRESHOLD`, 默认 3）时
  **retire**：`logger.warning` + `_pool_trace`，置 `idle[index]=True`（计入 all-idle 配额让池能正常终止）、
  退出本 worker 循环。requeue 的 step 此后只能被存活 worker 抢到 → 「step 自动 requeue 到活后端」。
- `poll_once` 抛异常**不再** `stop.set()` 杀死全池——只累加该 worker 的失败计数；存活 worker 继续抽干。

### Layer B — 有界 join + 停滞看门狗 (backstop, handles a truly-hung poll_once)

Layer A 只在 `poll_once` **返回**后生效。若 `poll_once` 自身永不返回（socket 卡在 60s 超时之下、
或不可取消的 C 调用），线程卡死、主 join 阻塞。故把无界 join 换成停滞感知等待：

- 看门狗按「全局已完成 claim 行数」(`sum(len(b) for b in results)`) 衡量前进。
- 连续 `STALL_TIMEOUT`（env `KERNELONE_DIRECTOR_DRIVE_STALL_SECONDS`, 默认 900s）无任何前进 → `stop.set()` 并跳出。
- 随后 `thread.join(timeout=…)` 有界收尾；卡死的 daemon 线程被放弃（daemon，不阻塞进程退出），
  其租约 (visibility_timeout) 到期后由 market 自动 requeue，下个 cycle 经 build 期健康检查路由到存活后端。

毒丸场景由 Layer A 在 ~3 个失败 turn（≈3–5min）内解决；看门狗只兜底真正的 hang。两者都把
最坏冻结从 6h 降到分钟级且**有限**。

### 终止/异常语义修正

- 结尾 `if errors: raise errors[0]` 改为 `if errors and not any_success: raise errors[0]`：
  单后端瞬断不再炸掉整条 dispatch；只有**全部** worker 零成功才把错误上抛给外层 loop guard。

## 5. 数据流 (control flow)

```
worker thread _run(index, consumer, provider_id):
  loop while not stop and claims < cap:
    batch = poll_once()            # 异常 → backend_failures++; 达阈值 retire; 否则 sleep+continue
    if batch:
      record + tag _director_backend
      if batch_shows_live_backend: backend_failures = 0   # ok=True 或 model-ran reason
      else:                        backend_failures += 1   # missing_execution_evidence
      idle[index] = False
      if backend_failures >= DEATH_THRESHOLD: retire(); return
      continue                     # immediate re-poll (high load)
    else:                          # 真·空 claim = 市场对我无单
      idle[index] = True; if all(idle): stop.set(); return
      sleep(poll_interval)

main:
  start threads
  watchdog: while any alive and not stop:
     if no progress for STALL_TIMEOUT: stop.set(); break
  for t: t.join(timeout=bounded)
  if errors and not any_success: raise errors[0]
```

## 6. 风险与边界 (Risks & boundaries)

- **误退健康 worker**：弱模型内容重试 (`step_target_missing` 等) 不计入失败计数，已按 reason 区分；
  阈值默认 3，给瞬时抖动留余量。
- **看门狗误杀慢 turn**：默认 900s 远大于单 turn（含重试）耗时；env 可调；任一成功 claim 即重置计时。
- **死 worker 永久退场**：单次 run 内不再复用该后端（即使它恢复）——符合「掉线就跳过、继续跑」的裁决；
  下个 cycle 的 build 期 `_reachable_provider_pool` 会重新纳入恢复的后端。
- 不改 consumer/market/provider；不引入 business code；UTF-8；类型完整。

## 7. 验证 (Tests)

`test_director_worker_pool.py` 新增：
1. 死后端 worker（持续 `missing_execution_evidence`）在阈值后退场，存活 worker 抽干市场，merged 含存活成功；
   整体在有界时间返回（不冻结）。
2. `poll_once` 持续抛异常的 worker 不再杀全池：与存活 worker 同池时返回存活结果、不 raise。
3. 停滞看门狗：一个 worker 的 `poll_once` 永久阻塞，设小 `STALL_TIMEOUT`，断言 drive 在有界时间返回存活结果。
4. 既有 `test_worker_error_is_surfaced`（单 boom worker 零成功 → 仍 raise）保持绿。

门禁：`ruff check --fix` + `ruff format` + `mypy` + `pytest`（fail-closed）。
