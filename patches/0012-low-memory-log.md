# 0012 · 降级加载留痕（2026-11-01，随 0.2.9.2 出包）

## 为什么

0.2.9.1 实测发现：可用内存低于引擎 arena 时，**不会失败**，而是降级成功。

构造：44 GiB 匿名内存把 `MemAvailable` 压到 44 GB，arena 需要 46 GB，走真实入口 `POST /api/native/operations {kind:"load"}`。

- 引擎日志逐 5 s 打点：`waiting up to 20 s for it (another engine still exiting?) - 0 s / 5 s / 10 s / 15 s so far (STRATA_ARENA_RAM_WAIT_S ...)`，随后 `not enough RAM for them`；
- control 自动降级重试，job 最终 `done`，文案：`... The first start ran out of memory, so Strata started again in low-memory mode: 43 GB of the model stays in RAM and the rest is read from the SSD. It is slower, but it should load.`；
- 代价实测（8K）：prefill **497.4**（正常 976.2，−49%）、TTFT **16.04 s**（正常 8.17 s）、decode 46.9（正常 48.2）。

即「ready 但只有一半速度」。这种实例会在 panel 里显示「已就绪」，与正常实例长得一模一样，事后无法从状态里分辨 —— 比 error 更难查。

## 改了什么

`host/server/native-control.mjs` 的就绪成功点（`engineProof` 四项凭据全过、`instance.status='ready'` 之后、`return` 之前）新增一行 info.log 证据：

```
[fnOS] load ok after Ns: <模型> on GPU <卡号, 1 起>, port <端口>, full memory mode | LOW-MEMORY mode (part of the model is read from the SSD, slower)
```

- 降级判定：`/low.memory|low memory|out of memory|from the SSD/i` 匹配 `job.summary`、第二次尝试的文案 `retried.words`、`instance.notice` 任一处；
- 与 0010 的失败行走成一对：成功必须有 `load ok`，失败必须有 `load failed after Ns: …`，两者都没有说明这次 load 没走真实入口。
- 不改引擎、不改推理路径、不改界面。

## 构建守卫

`scripts/build.sh` 0010 段之后加：

- `grep -q '\[fnOS\] load ok after'` —— 成功侧证据行不许丢；
- `grep -q 'LOW-MEMORY mode'` —— 证据行必须能标出降级状态。

## 验收

装机后 load 一次（内存充足）：`grep '\[fnOS\] load ok' /vol1/@appdata/strata/info.log | tail -1` 必须以 `full memory mode` 结尾。
造降级复现（可选）：先分配匿名内存把 `MemAvailable` 压到 arena 以下再 load，该行必须出现 `LOW-MEMORY mode`，且只做一致性检查、不入性能基线。

口径同时写进 `发布记录/验收流程.md`：带 `LOW-MEMORY mode` 的实例一律不算达标实例。
