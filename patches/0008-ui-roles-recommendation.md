# 0008 `host/dist/assets/index-*.js` + `host/server/native-control.mjs`：网页里的角色选择不再被丢掉

`fnpack/app/host/dist/**` 是上游预构建的前端产物（仓库里没有 UI 源码），所以这条补丁同样以
「为什么 + 怎么验证」记录，改坏了由 `scripts/build.sh` 的出包前自检拦住。

## 事故（2026-10-30，用户报「网页里设了第二卡辅助，实际全在一张卡上」）

用户在 8791 的角色界面把第二张 2080 Ti 设成 Helper，加载出来的实例却是单卡
（`native-instances/<id>.json` 里 `gpu=1`、`gpu_roles={"0":"off","1":"main"}`）。

链路（压缩后的前端变量名）：

```js
// 用户的选择正好等于「推荐」时，状态被清成 null（这是原设计：null 表示用推荐）
Fe = ns => S(Jd(ns, at.roles) ? null : ns)

// 但取有效角色时，null 先回落到「已加载实例的角色」，推荐排在它后面
Je = ee.length>1 ? (R ? F$(R,ee) : In ?? at.roles) : at.roles
//                  R=roles(可能为 null)  In=已加载实例的角色  at=推荐

// 加载请求：R 为 null 时永远走「推荐卡列表」，gpuRoles 根本没发出去
...ee.length>1 ? R&&Ke ? {gpuRoles:Je, ...} : re.length ? {gpus:re, gpusFromRecommendation:!0, ...} : {} : {}
```

服务端 `native-control.mjs` 对 `gpusFromRecommendation` 的语义是「这不是人选的」，
配置里又已经有卡号时直接忽略它 —— 于是最终仍按上一次运行的角色起单卡。
两条路径一起把用户的选择吃掉了，界面上却显示 Helper 已选中。

## 改法

1. 前端：`Je` 不再用 `In ??` 压掉推荐 —— `roles` 为空就用推荐；加载请求在 `ee.length>1`
   时**一律发 `gpuRoles`**（推荐表也发），`gpus` + `gpusFromRecommendation` 这条旧路径删掉。
   服务端拿到明确角色表就走 `if(args.gpuRoles!==undefined)` 分支，按角色算物理设备。
2. 后端：`if(args.gpuRoles!==undefined)` 分支补一行角色日志（原来只有「从上次运行恢复」
   那条路径会打），格式与 0005 一致：
   `roles chosen in the screen: {"0":"off","1":"main","2":"helper"} | physical: [2,1] | CUDA_VISIBLE_DEVICES=2,1`

## 验证

- `bash -n` / `node --check` 通过；
- `scripts/build.sh` 断言：`Je=ee.length>1?R?F$(R,ee):at.roles:at.roles` 在、`gpusFromRecommendation`
  不在、`roles chosen in the screen` 在；
- 装机后走真实入口（8791 的 `POST /api/native/operations`）用推荐表加载，`info.log` 应出现
  `physical: [2,1]`，引擎日志出现 layer split 与两张 2080 Ti。
