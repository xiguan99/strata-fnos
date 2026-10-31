# A. 重基到 0.2.9（app 0.2.9 / control 0.1.55 / 引擎 0.1.55）— 补丁逐个判定

日期：2026-10-31
现状：`fnpack/app/host` = 上游 0.2.9 host + 我方 4 个文件补丁；`fnpack/app/control` = 上游 0.1.55 + 我方 4 个文件补丁；`engine-bundle/` 仍是 0.1.52（待换）；`fnpack/manifest` version 仍是 0.2.7.12（待 bump 0.2.9.1）。

## 一、补丁面（0.2.9 上实际存在的差异）

host 侧（`diff -rq upstream/0.2.9/x-host-0.2.9 fnpack/app/host`，只有 4 个文件）：

| 文件 | 补丁 | 留/废 | 一句话理由 |
|---|---|---|---|
| `server/index.mjs` | 0004 局域网 Origin 放行 | **留** | 手机/局域网用 `http://<NAS IP>:8791` 打开时必须放行，服务端按请求 Host 推导，不写死任何地址 |
| `server/native-control.mjs` | 0005 角色→设备/层序 | **留** | 网页选的 Main/Helper 必须真的落到 `CUDA_VISIBLE_DEVICES` 与 `--layer-split` 顺序，否则「设了不生效」；序号来自设置，不硬编码 |
| 同上 | 0006 首次加载显存预检（三档数字可配） | **留** | 上游把 `Math.max(2560, reserve+1536)` 写死，我们改成设置页三项（floor/extra/headroom），默认行为不变 |
| 同上 | 0005b 从「已存设置」取角色的回退 | **留** | 上游 0.2.9 仍没有这条回退：UI 发推荐卡列表时会把用户已存角色冲掉 |
| 同上 | 0010 ready 四项外部证据 + 入口闸门 | **留** | 谎报 ready 的根因是 control `/health` 在引擎没起时也返 200；四项证据（`loaded:true`／目标卡有本实例引擎进程／该卡显存超门槛／本次 load 后日志出现 `with everything loaded`）缺一即 `failed` 并写明缺哪条 |
| 同上 | 0010b 引擎日志路径两种拼写都试 | **留** | 打包态 `installDir` 已含 `/install`，只拼一种会找 `install/install/…` 并把健康引擎误杀 |
| 同上 | 0009 引擎参数透传（`ENGINE_ARG_FIELDS`） | **留** | 设置页改的 `--kv-resident/--prefill/--pool-workers/--host-core/--spec/…` 写进该模型引擎参数，属通用能力 |
| `server/native-settings.mjs` | 0009 九项设置项 | **留** | 只新增**输入项**（label/min/max/hint），**不带任何本机数值**；符合「配置走网页，不写死」 |
| `dist/assets/index-k1Z_bgvQ.js` | 0008 角色请求参数修正 | **决策点（见第三节）** | 只改「发出去的 `gpus`/`gpuRoles`」，不改任何界面元素；去掉它则网页改角色仍会被推荐覆盖 |

control 侧（只有 4 个文件）：

| 文件 | 补丁 | 留/废 | 理由 |
|---|---|---|---|
| `setup.py` | 0001 `reuse_local_mtp()` | **留** | 国内连不上 HuggingFace；本机/旧安装已有成品 MTP 树时整树复用（同盘硬链，零拷贝） |
| `setup.py` | 0003 自备 GGUF 宽松校验 | **留** | 允许自制量化目录（分片命名不满足官方严格清单），分片缺失仍拒绝 |
| `serve/server.py` | 0005 服务端侧（角色/卡名日志 + 角色来源） | **留** | 与 host 侧配套；卡名走 `nvidia-smi` 查询，不写死 |
| `serve/test_server.py` | 随 0005 的测试同步 | **留** | 保证上游测试仍绿 |
| `tools/strata_mcp.py` | 0007 `_reference` 归位 + class 完整性 | **留** | 装配后 `class Strata` 缺 `_reference` 会让 MCP 工具面报错；属通用修复 |

补丁不涉及的：`VERSION`、`BUNDLE.json` 一律用上游值（0.1.55 / 上游 bundle 描述），不改。

## 二、三块 FAILED hunk 的手工处理

`patch -p4 -l --fuzz=3` 打 `host-native-control.diff` 时 6/9 成功，3 块 FAILED（上游 0.2.9 把 `namedInFile` 改成含 `gpu_roles`、并新增 `native-ds-args.mjs`，锚点全移位）。处理方式：不猜偏移，直接按 `.rej` 里记录的**期望成果**在 0.2.9 新结构里找同形位置插入：

1. `cfg` 组装后加 `for (const f of ENGINE_ARG_FIELDS) …`（引擎参数透传）——插在 `const cfg={...validateNativeSettings(...),vision}` 之后、`const namedInFile=` 之前。
2. 角色回退分支 —— 插在 `if(args.gpuRoles!==undefined){…}` 的 `else-if` **之后**（插在 if 行后会产生 `if … else {…} else if …` 语法错，实测 `SyntaxError: Unexpected token 'else'`）。
3. `buildConfig(...)` 解构加 `mainCardNote`（下游用的是这个名字，不能改成 `cardNote`）。

验证：`node --check fnpack/app/host/server/native-control.mjs` → SYNTAX OK；build.sh 的 15 条锚点断言全部命中（`rolesToDevices`、`CUDA_VISIBLE_DEVICES=${shown.join`、`CUDA0. Choose the Main card`、`with everything loaded`、`ENGINE_ARG_FIELDS` 等）。

前端 `index-k1Z_bgvQ.js`：0.2.9 的 bundle 与 0.2.7 同形（角色计算 `Vt/st/Qe` 三处），三处语义重放后 `node --check`（--input-type=module）通过，且 `gpuRoles:` 键命中 1 次。

## 三、需要你拍板的一处：0008（前端 bundle）

用户口径「界面原始啥样就啥样」与你的「卡角色补丁要留」在这里重叠。事实：

- 0008 **不改界面外观/布局**，只改请求体里的 `gpus` / `gpuRoles` 字段，让「网页上选的主卡/副卡」不被推荐算法覆盖。
- 去掉 0008 的后果：网页选角色 → 请求里仍带推荐卡列表 → 服务端若采信就以推荐为准，用户设的角色在双卡机上会"看着设了其实没生效"。
- 保留 0008 的代价：包内有 1 个前端文件与上游字节不同（虽然只差这一处）。

给我的建议：**保留 0008**，并在发布记录里写明「唯一一处前端改动，只影响角色请求参数」。若你要求前端零修改，我就只留服务端回退——但请在验收时按「网页改角色」这条路径实测，确认是否还能生效。

## 四、发布前自查（已做）

| 检查 | 结果 |
|---|---|
| 包内本机路径（`/vol1/1000`、`/vol1/@appdata`） | **0 命中**（host/control 源码） |
| 包内本机主机名/IP（`192.168.*`、`dshwebui`、`fnnas`） | **0 命中**（唯一命中是上游自带测试文件的样例 IP） |
| 包内本机模型名（`swift-iq3_s`、`swift-abliterated`） | **0 命中** |
| 包内调优数值（`20480`、`262144`、`1700`） | 仅上游 catalog 的 `contextMax:262144` 与设置项 `min/max`，**无我们的运行值** |
| 预设是否进包 | **否**：预设落 `fnOS 数据目录/.config/strata/presets/`，由用户在本机页面保存 |
| UI 改动 | **1 处**（`index-k1Z_bgvQ.js`，见第三节，待你确认） |
| 版本号 | 待 bump `0.2.9.1`（manifest + `VERSION` 文件），引擎目录换 `0.1.55-linux-x86_64-cuda-cuda12` |

## 五、装机构建步骤（方案通过后执行）

1. `python3 -m py_compile fnpack/app/control/setup.py`、`serve/server.py`、`tools/strata_mcp.py`
2. `rm -rf fnpack/app/engine-bundle/*` → 拷入 `0.1.55-linux-x86_64-cuda-cuda12`（含 `libstrata_kq.so`、`strata-vision`、`libcudart/cublas*`）
3. `fnpack/manifest` version → `0.2.9.1`，`fnpack/app/control/VERSION` 保持上游 `0.1.55`
4. `bash scripts/build.sh`（守卫：node --check、test-gpu-roles.py、class 完整性、同名版本禁覆盖、图标权限 644）
5. 装机 `trim-cli app uninstall strata --yes` → `install-fpk`，页面加载 IQ3_S 实测
