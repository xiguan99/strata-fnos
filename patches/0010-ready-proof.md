# 0010 — 「就绪」必须外部可验：谎报 ready 修掉 + 参数不全的 load 请求入口拒绝

日期：2026-10-31（0.2.7.11）
来源要求：Rain m02777（用户实测后当场发现）——「这是我不能接受的缺陷：不能因为 start 返回成功就报 ready。
就绪判断必须有外部可验的凭据：进程存在 + 目标卡显存占用超过阈值 + 引擎日志出现 startup 完成行 + 端口可达。
任何一项不满足都必须报 failed 并说明缺哪条。另外：参数不全的 load 请求应该在入口就拒绝。」

## 事故现场（可复现）

`POST /api/native/operations {"kind":"load","args":{"model":"swift-iq3_s"}}`（只给模型名，不给卡/角色）

- 21 秒后 job 变成 `succeeded`，页面显示「已就绪」；
- 同一时刻 `GET /api/native` 的 `instances = []`，`nvidia-smi` 两卡各 1 MiB，引擎进程不存在；
- 引擎日志 `install/strata-swift-iq3_s.log` 没有任何新的启动段。

## 根因

`fnpack/app/host/server/native-control.mjs` 的就绪循环只判断 HTTP 状态码：

```js
const response = await fetcher(`http://127.0.0.1:${instance.port}/health`, ...);
if (response.ok) { instance.status = 'ready'; ... }
```

而 Strata Control 的 `/health`（`/vol1/@appcenter/strata/control/serve/server.py:7142`）在 **服务在跑、引擎没起** 时
照样返回 `200`，正文是 `{"loaded": false, ...}`。于是「Control 起来了」被当成了「引擎起来了」。

## 改动文件

- `fnpack/app/host/server/native-control.mjs`
  - 新增 `computeApps()`：`nvidia-smi --query-compute-apps=pid,used_memory,gpu_uuid` + index/uuid 映射，
    得到「哪张卡上有计算进程、占多少」。
  - 新增 `engineLogFile(model)` / `ENGINE_READY_LINE` / `engineProof(instance,{model,cfg,since})`：
    四项外部凭据逐条核对，返回 `{ok, missing[]}`。
    1. `/health` 正文 `loaded === true`（只认这个字段，不再只看状态码）；
    2. 选定卡上存在计算进程；
    3. 选定卡已占用显存 ≥ `max(1024, 0.8 × 该模型在该卡的历史需求)`（需求来自 `native-vram.json`，无记录时 1024 MiB）；
    4. 该模型的引擎日志本次被写过（mtime ≥ 本次开始时间 − 5s）且末尾 40 KB 出现 `with everything loaded` /
       `ready to serve` / `server is ready`。
  - 就绪循环：`loaded !== true` 或 `engineProof` 不通过都**不算就绪**，继续轮询；`loaded === false` 超过 90 秒
    提前收手（Control 已报 start 完成却仍说没加载，等满 5 分钟只是拖慢真相）；超时后停止实例并
    抛 `The engine did not come up: <缺的每一条>. ...`。
  - 入口闸门：`operation('load')` 里 `args.model` 缺失/空串直接
    `throw new Error('Choose a model to load.')`，不再创建空 job。
- `scripts/build.sh`：守卫 0010，缺任一项断言就不许出包（防止同步上游时把这段判据冲掉）。

## 验证口径

1. 故意只传模型名的 load 请求 → job 变 `failed`，错误里逐条列出缺哪几项，且 `instances = []`、两卡显存不变。
2. 正常请求（页面入口，带角色/卡）→ `ready` 且四项全部成立（进程在选定卡、显存到量、日志有完成行、`loaded:true`）。
3. 失败路径不许再出现「页面已就绪但 `nvidia-smi` 上没有进程」。
