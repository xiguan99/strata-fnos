# 补丁重放清单（fnOS 打包版）

`fnpack/app/control/` 是官方 Strata Control bundle，目录里自带 `.gitignore` 的 `*`，**不进本仓库**；
它的改动只体现在 fpk 包里，换上游 bundle 时必须照下列补丁重放。两份都是"改一处、跑一次自测"级别的外科手术。

| # | 文件 | 补丁 | 作用 | 存档 |
|---|------|------|------|------|
| 0001 | `control/setup.py` | MTP 层本地复用优先 | 下载前先找 `<数据根>/models/mtp`、历史安装目录的 `mtp/`、`models/mtp/`，有成品就整棵树复用（同盘硬链零拷贝）；`mtp_pack`/`mtp_rt` 按需执行。国内连不上 HF 时靠它活 | `control-setup-mtp-reuse.md` |
| 0002 | `control/setup.py` | `gguf_dir_problem()` 提示人话化 | 自带 GGUF 目录里文件存在但文件名不合官方命名时，直接说"文件名必须完全等于 `Swift-…-{q}-0000{i}-of-00002.gguf`"，并提示用 `ln` 硬链改名；不再只回一句 `folder has no …` 让人误以为目录里没文件 | 本文档 + 源码注释 `fnOS 补丁` |
| 0003 | `control/setup.py` | `verify_sha256(own=True)` 宽容 | 官方在自带 GGUF 尺寸/SHA 与 pinned 不符时 `fail()` 中止（own 分支只保证"不删你的文件"）。改版/破甲权重尺寸必然不同 → 必挂。补丁改成 `warn()` + `return` 继续使用你的文件 | `0003-setup-own-gguf-lenient.patch` |
| 0004 | `host/server/index.mjs` | 局域网 Origin 放行（`isLocalOrigin`） | 官方 `/api/*` 的 Origin 白名单硬编码 `localhost/127.0.0.1/[::1]`，浏览器直连 `<NAS IP>:8791` 时全部 /api 403 → 界面报「打开本地 Strata 应用以使用主机控件」。补丁放行回环 + 主机名（含 `.local`）+ 私有网段（10/8、172.16/12、192.168/16、169.254/16，IPv6 `::1`/`fe80::/10`/`fc00::/7`），公网仍 403 | 本文档 + 源码注释 `fnOS 重放 538dc27d` |

## 重放方式

```bash
cd fnpack/app/control
patch -p0 --dry-run < ../../patches/0003-setup-own-gguf-lenient.patch   # 先干跑
patch -p0           < ../../patches/0003-setup-own-gguf-lenient.patch
python3 -m py_compile setup.py
```

0001 与 0002 没有统一 diff（改在函数体内部），按 `control-setup-mtp-reuse.md` 里的函数全文与锚点插入；
重放后在 `setup.py` 里 `grep -c 'reuse_local_mtp\|改版/破甲权重\|using your own file as it is'`
应分别命中 ≥1。

## 上游版本

`/vol1/1000/work/deepseek/fnos-apps/strata/fnpack/app/control/COMMIT` 记录决定这三份补丁上游 commit（当前 `76f90e58…`，
control 版本 0.1.47、engine 0.1.47）。换 bundle 后先跑一次「自带 GGUF 安装」回归：装完 `GET /api/native` 的
`installed.models[].ownGgufDir` 必须指向你选的那个目录。

## 0.2.6.1 重放结论（2026-10-26）

换 control 0.1.48 → **0.1.51** 后的实际处理：

| # | 结论 | 理由 |
|---|------|------|
| 0001 MTP 本地复用 | **已重放** | 上游仍无此逻辑；`reuse_local_mtp()` 函数体原样搬入，并在 MTP 下载前插入 3 行复用调用。`grep -c reuse_local_mtp setup.py` = 2，`py_compile` 通过 |
| 0002 GGUF 命名提示 | **不再重放** | 上游 0.1.51 把该分支改成直接 `return None`（不再给出提示），函数签名与失败路径已变；强插会盖住上游新的判定顺序。仅影响提示文字，不影响能否使用自带权重 |
| 0003 自带权重宽容校验 | **上游已吸收** | 0.1.51 的 `verify_sha256` 新增 `own_gguf_is_whole()`：自带文件是完整 GGUF 时一律 `warn` 后继续使用，官方原补丁的两处 `fail` 分支已被取代。再打会造成重复逻辑 |

换 bundle 的完整步骤：`rsync -a --delete` 覆盖 `host/ harness/ node/ control/` → 重放 0001 → `py_compile setup.py` → 换 `engine-bundle/<版本目录>` → 改 `VERSION` → `bash scripts/build.sh`。

## 0004 局域网 Origin 补丁（2026-10-29 回归事故后固化）

**事故**：v0.2.7 同步上游（`b5d5173c` / `cf1c57f8` / `59a93fb6`）时，v0.2.2.4 的 `538dc27d` 局域网 Origin 补丁没有重放回来 →
`grep isLocalOrigin host/server/index.mjs` = 0 命中 → 局域网访问所有 `/api` 403 → 顶部红条「打开本地 Strata 应用以使用主机控件」。
用户当场只能看不能控。

**重放方式**（改一处、两处插入）：

1. 第 34 行附近导入：`import { homedir } from 'node:os';` → `import { homedir, hostname as osHostname } from 'node:os';`
2. `const server = http.createServer(` **之前**插入 `localHostNames` 集合 + `privateV4` + `isLocalOrigin()`（全文见仓库同目录 `0004-host-local-origin.md`）
3. `/api/*` 的 Origin 判断：`if (!['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname))` → `if (!isLocalOrigin(origin.hostname))`

**守卫**：`scripts/build.sh` 的 `[2.5/3] 补丁守卫：局域网 Origin` 会硬断言

```bash
grep -q 'isLocalOrigin' host/server/index.mjs
grep -q 'if (!isLocalOrigin(origin.hostname))' host/server/index.mjs
node --check host/server/index.mjs
```

任一不通过就中止构建 —— **同步上游后忘记重放 = 打不出包**，不会再静默出回归包。

**回归验证**（装机后必跑）：

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Origin: http://<NAS 局域网 IP>:8791' http://127.0.0.1:8791/api/health   # 200
curl -s -H 'Origin: http://<NAS 局域网 IP>:8791' http://127.0.0.1:8791/api/native | head -c 120                      # 200 + version
curl -s -o /dev/null -w '%{http_code}\n' -H 'Origin: http://example.com' http://127.0.0.1:8791/api/health             # 403
```

## 0005 主卡角色必须落到引擎（2026-10-29 回归事故后固化）

**事故**：0.1.52 起模型设置文件里只剩裸 `"gpu": 0`（setup 写的），没有 `gpu_roles`。host 的 `buildConfig()` 认为「文件里已经写了卡」就直接沿用 →
引擎拿到 `CUDA_VISIBLE_DEVICES=0`，也就是 nvidia-smi 第 0 张卡（本机是 CMP 40HX 8G，不是人选的 RTX 2080 Ti 22G）；
`card_note()` 又只在编号「错位」时打印，`0` 与引擎自己的 `GPU 0` 一致 → 日志里一行都不说，看起来像主卡选择被吞了。

**语义（Rain 2026-10-29 定）**：不写死任何 index。角色表（`gpu_roles = {"0":"helper","1":"main"}`，`off` 不参与）→ 按阶段序算出物理设备集合
（Helper 在前、Main 最后）→ 注入 `CUDA_VISIBLE_DEVICES`，引擎侧的 CUDA0 就是被选中的主卡；一卡/两卡/三卡、主卡是 0 号，都必须成立。
解析不出角色且兜不成，就明确报错拒绝启动，绝不按引擎默认 CUDA0 硬起。

**host 侧 `host/server/native-control.mjs`**
- `rolesToDevices(roles)`：角色表 → `{devices, roles}`，要求恰好一个 `main`，helper 按编号升序在前、main 最后；
- `lastRoles(model)`：读 `native-instances/*.json` 里该模型**最近一次**记录过角色的实例，只继承**主卡**、其余置 `off`（绝不顺手占用别人在用的卡）；
- `buildConfig()` 在原有 `args.gpuRoles` / `args.gpus` 两条分支之后加兜底 `else`：
  1. 屏幕上选过（`args.gpuRoles` / `args.gpus`）→ 原样采用；
  2. 文件里有 `gpu_roles` → 按角色重算 `gpu`/`gpu_order`/`gpu_cards`（防止文件里 `gpu` 与角色不一致）；
  3. 都没有 → `lastRoles()` 继承上次主卡；
  4. 仍然解不出、且机器 >1 张卡、文件只命名 ≤1 张卡 → **throw**：`Choose the Main card (Main / Helper / Off per card) in Settings -> the card roles screen, then load again.`；
  5. 单卡机器 → 直接用那张卡。
- `loadEngine()` 写出实例配置后 `console.log('[fnOS] ' + mainCardNote)`，note 固定三样：`roles: {...} | physical: [...] | CUDA_VISIBLE_DEVICES=...`。

**上游 Python `control/serve/server.py`**
- `gpu_names_by_index()`（`lru_cache`）：`nvidia-smi --query-gpu=index,name` 查一次，给启动行带上卡名；
- `card_note()`：**只要给了卡就打印**（含 `gpu: 0` 这种编号一致的情况），并输出 `roles GPU 0=off, GPU 1=main;` + 物理卡名 + `CUDA_VISIBLE_DEVICES`，例如：
  `[strata] roles GPU 0=off, GPU 1=main; running on GPU 1 (NVIDIA GeForce RTX 2080 Ti) (as nvidia-smi numbers it, CUDA_VISIBLE_DEVICES=1); the engine's log calls it GPU 0 / CUDA0`
- `log_card_note(cfg, note)`：`card_note()` 的那一行除了打控制台，也**追加写进运行日志**（`cfg["log"]`）——应用里能看到的日志
  就是这份，只有控制台等于没人看得见；
- `control/serve/test_server.py::GpuChoice` 同步改为「编号一致也要打印」。

**离线验证（不需要卡、不需要模型）**：`scripts/test-gpu-roles.py` 从 `native-control.mjs` 里逐行原样抽出 `roleList` /
`rolesToDevices`，用 `node --test` 跑 6 个用例：主卡=0（单卡可见集 `[0]`）、主卡=1、主卡+副卡（helper 在前 `[0,1]` / `[2,3]`）、
`off` 卡永不出现、无 main/双 main 返回 null（对应「拒绝启动」）、三卡机主卡在任意 index。`node --test` 6/6 通过，
已接进 `scripts/build.sh` 的 `[2.6/3]`，改坏即构建失败。

**守卫**：`scripts/build.sh` 的 `[2.6/3] 补丁守卫：主卡落到引擎` 硬断言 `rolesToDevices` / `lastRoles` / `mainCardNote=\`roles` / `CUDA0. Choose the Main card` / `def gpu_names_by_index` / `CUDA_VISIBLE_DEVICES={visible}`，外加 `node --check` 与 Python `ast.parse`。

**验收（装机后「加载模型」）**：日志里应同时出现三样 —— `[fnOS] roles: ... | physical: [...] | CUDA_VISIBLE_DEVICES=...`、
`[strata] roles GPU x=main; running on GPU x (<卡名>) ... CUDA_VISIBLE_DEVICES=x`、引擎自己的 `GPU 0 / CUDA0`。

**装机实证（0.2.7.3，2026-10-29 19:46 加载 swift-iq3_s）**：
```
info.log:392   [fnOS] roles restored from the last run (other cards Off): {"0":"off","1":"main"} | physical: [1] | CUDA_VISIBLE_DEVICES=1
strata-swift-iq3_s.log:568  [strata] roles GPU 0=off, GPU 1=main; running on GPU 1 (NVIDIA GeForce RTX 2080 Ti) (as nvidia-smi numbers it, CUDA_VISIBLE_DEVICES=1); the engine's log calls it GPU 0
引擎自身       GPU 0: NVIDIA GeForce RTX 2080 Ti, compute capability 7.5
```
实例 `ready`；验完立即卸载，`nvidia-smi` 显示 GPU1 回落 299MiB、GPU0（视频平台）全程未被触碰。

**tune 文件缺 `gpu_roles` 的根因**：`writeTuneConfig()` 写的正是 `buildConfig()` 出来的 cfg，而旧 `buildConfig()` 只在屏幕传了 `gpuRoles` 时才产出
`gpu_roles`；setup 生成的设置文件只有 `gpu`，于是 tune 文件也跟着没有角色 —— 属**上游设计缺口**（不是我们改坏的回归），已由本补丁一并覆盖：
现在任何一次 load/tune 出去都带 `gpu_roles`。
