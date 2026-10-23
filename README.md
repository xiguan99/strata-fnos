# strata-fnos

[Strata](https://github.com/Niko1221/Strata) 官方桌面应用（v0.2.2，MIT）的 fnOS 原生打包版。
AppImage 解包全量入库（node / host / control / harness / engine-bundle cuda12），零宿主机依赖；
无壳单进程原生直跑（用户拍板 2026-10-23：自带运行时、能原生绑 0.0.0.0 的程序不套壳反代）。构建：`scripts/build.sh` → `dist/strata_v<版本>_x86.fpk`。

## 运行时架构

- **strata host**（自带 node runtime 跑 `server/index.mjs --dist dist`，单进程原生绑 **0.0.0.0:8791**；桌面图标端口直达，不走统一网关）
- 数据根：所选数据库 `data/Documents/Strata`（Models / Workspace），通过 `HOME` 注入
- 引擎：包内 `engine-bundle/0.1.47-linux-x86_64-cuda-cuda12`（自带 libcublas 12，GPU 空闲时可自管启动）；GPU 被占用时可指向已在跑的 llama.cpp 后端（app 自动发现 `http://127.0.0.1:8080/v1`）

## 安装

fnOS 应用中心 → 手动安装 fpk。向导选数据位置与运行权限（安装后勿改）。装完桌面图标/浏览器端口直达 `http://<NAS>:8791/`。

### 安装器提示（0.2.2.2 起）

- **模型目录属主**：GUI Setup 要对模型目录写 `.done` 完成标记。选已有的 GGUF 目录（如共享目录里的下载残留）时，目录必须**运行用户可写**——向导选「普通用户 (Package)」时运行用户是套件用户（如 `strata`），属主是 `rain`/root 的目录写不进标记，安装器每次重跑 prepare（官方行为：warn 后继续，不致命）。想省事就把模型放进数据目录 `Documents/Strata` 下或 chown 给运行用户。
- **磁盘不够用直读**：GUI「已有文件夹」/ CLI `--gguf-dir <目录>` 支持就地直读——不复制、不重下（按官方 pin 的 size+SHA256 校验你自己的文件），磁盘只需 ~6GB 余量（prepare 输出到数据目录的 packs/）。自己的文件 setup 永不删除。
- **无 GitHub 网络**：本构建在包内捆绑了 `gguf-py`（`control/gguf-py/` = PyPI `gguf` 0.19.0，MIT，上游即 llama.cpp 的 gguf-py；四运行时依赖 numpy/pyyaml/requests/tqdm 在 bundle venv 已齐），打包模式的 installer 不再联网拉 llama.cpp 源码 zip。要严格对齐上游 pin（commit `3cf0325`）的源码版，可用环境变量 `STRATA_LLAMA_CPP_ZIP` 指向镜像/本地 zip（download() 支持 file:// 和纯路径拷贝）。

### 国内网络与模型下载（0.2.2.3 起）

- 本机（及国内多数网络）直连 `huggingface.co` 不通，GUI 里点下载模型/图片读取依赖会报 `cannot reach huggingface.co`。
- 包内 `cmd/main` 已默认注入 `export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"`（hf-mirror 实测可达，约 150KB/s）。**不要**默认改 `STRATA_SOURCE=modelscope`：setup.py 的 `model_source()` 明确说 ModelScope 只给仓库当前文件、丢了 pinned revision，大分片有 sha 不符被删的风险。
- 最省事是**预置文件零下载**：把与 pinned 同名的 gguf/mmproj 放进 `<models-dir>/<tag><size>/` 并保留 `.done` 标记（内含 `sha256 <hex>`，必须与 pinned 值一致），setup 命中标记即跳过下载与重复哈希。标记必须配合真实文件与真实 sha256，不要为过校验伪造。
- 下载失败时 GUI 的错误信息会自动附带上述两条出路的中文引导（本包补丁，`host/server/native-control.mjs` 的 `downloadHint()`）。

### STRATA_REAL_ENGINE 语义（易踩，写清楚）

- **装机默认不设 `STRATA_REAL_ENGINE`**（即 simulated/demo 模式）时，GUI 的「Load model」走官方 native runtime，直接拉起包内 strata engine，**能真加载真答话**（实测双卡 2080Ti：各 21.7G、context 131072、70 tok/s）。
- 设了 `STRATA_REAL_ENGINE=1` 反而走 llama-server 路径（`host/server/index.mjs` 里 `models.REAL` 分支需 `supervisor.findBinary()` 的 llama-server 二进制），本包没有该二进制 → GUI 点加载会秒报 `stopped while starting`。**别设它。**
- 之前文档里「默认 simulated 模拟模式」的说法指的是后端 `kind:"demo"` 的标签，不代表加载是假的——模型加载与推理都是真的。

### 装机后安装/加载卡死（0.2.2.4 修）

- **数据根属主**：装机版数据根常是用户选的共享目录（如 `/vol1/1000`，属主 `rain:root` 755），应用用户（如 `strata` uid 961）建不了 `packs/`、`mtp/` → 官方 `iq_pack.py` 直接 `PermissionError: [Errno 13] Permission denied: '/vol1/1000/packs'`（或 `mtp_fetch.py` 的 `lock_folder` 报 `/vol1/1000/mtp`）。本包 `cmd/main` 启动时以 root 预建 `<数据根>/models`、`packs`、`mtp` 并 `chown` 给运行用户（目录换属主时递归一次），再校验可写。
- **可写性预检**：安装前 `host/server/native-control.mjs` 的 `dataRootProblem()` 先探 `packs/`、`mtp/` 能否写，不能写就直接返回中文提示（「数据根 … 对应用用户不可写（EACCES）…请到 设置 → 模型文件夹 换一个应用能写的目录」），不再冒 Python 堆栈。
- **MTP 复用优先**（补丁存档 `patches/control-setup-mtp-reuse.md`，`control/` 不进 git 需重放）：`control/setup.py` 的 `reuse_local_mtp()` 在下载前先找 `<数据根>/models/mtp` 与历史安装目录的 `mtp/`、`models/mtp/`，有成品就整棵树复用（同盘硬链接零拷贝），`mtp_pack`/`mtp_rt` 也只在缺产物时才跑——本机不通 huggingface 时靠这条活下来。
- **旧失败卡自动清**：一次安装成功后，同 kind 的历史失败任务从 `native-jobs.json` 里移除（界面不再挂着 10-22 那张「下载无法连接到互联网」红卡）。
- **`su -c` 会重置 HOME**：`cmd/main` 用 `su -s /bin/bash <用户> -c` 起 host 时，su 把 `HOME` 换成应用用户的家目录（装机环境常不存在）→ `store.mjs` 报 `No writable data directory found`。现在在 su 内再 `export HOME=<数据目录>` 一次。

### 局域网访问与「打开本地 Strata 应用以使用主机控件」

官方 host 只放行 `Origin` 为 localhost/127.0.0.1/[::1] 的 `/api/*` 请求，其它来源一律 403 并回这句提示。fnOS 装机版没有桌面宿主外壳，用户就是从 `http://<NAS 局域网 IP>:8791` 打开的，于是所有 API 被 403。本包把**本机全部网卡地址与主机名**加入放行名单（`host/server/index.mjs` 的 `isLocalOrigin()`），外网域名仍然拦。

### 自带 GGUF 安装（改版 / 破甲权重）

- **目录位置有陷阱：不要放在 `<数据根>/models/` 下面。** `host/server/native-control.mjs` 的 `installedInfo()` 用
  `basename(dirname(dirname(nativeFile))) === 'models'` 判断这是不是官方安装布局；放在 `<data_dir>/models/<任意名>/` 里
  都会被当成官方目录 → `ownGgufDir` 变空 → 以后在界面点「Apply settings and load」重跑 setup 时**不带 `gguf_dir`**，
  直接走官方下载（一个 84 GB 的坑，磁盘不够就挂）。放 `/vol1/1000/local-models/<你起的名字>/` 这类目录才安全，
  装完 `GET /api/native` 的 `installed.models[].ownGgufDir` 必须等于它。
- **文件名必须是官方全名。** `setup.py` 的 `gguf_choice()` 按 `model_file()` 拼出的完整文件名匹配，例如
  `Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf` 与 `-00002-of-00002.gguf`、`mmproj-Swift-Qwen3.8-Flash-Next-BF16.gguf`。
  破甲/改版权重通常叫 `...-abliterated-IQ3_S-...gguf`，**用 `ln` 硬链接成官方名**（同盘零占用，别复制 80+ GB）。
- **不要伪造 `.done`。** `.done` 里写的是官方 pinned 的 sha256；本包 0003 补丁已让 `own=True` 时尺寸/SHA 不符只 `warn` 不中止
  （官方原版是 `fail` 中止，所以自带权重在官方包里必挂）。
- **选完目录后返回 `ownGgufDir` 才算对**，否则重装会退回官方下载路径。

### 常见故障

- **GUI 里发消息报「The coding assistant could not start」**：host 内嵌的 Harness 进程起不来，最常见原因是凭据文件权限过宽——`<数据根>/.strata/data/agent/home/.credentials.yaml` 要求 owner-only（600），实际为 755 时 harness 的 `credentials` 插件拒绝加载，连带 11 个依赖插件等待。修法：`chmod 600 <数据根>/.strata/data/agent/home/.credentials.yaml` 后重发消息即可（不用重启 app）。诊断日志：`<数据根>/.strata/data/agent/home/logs/startup-*.log`。
- **GUI 点加载模型秒报 `stopped while starting`**：见上面「STRATA_REAL_ENGINE 语义」——别设 `STRATA_REAL_ENGINE=1`。
- **下载模型报 `cannot reach huggingface.co`**：见上面「国内网络与模型下载」。

## 版本

`VERSION` 文件 = 上游版本.构建号。
- **0.2.2.5（本次）**：自带 GGUF / 改版权重支持收口——① 纳入 0003 补丁（`verify_sha256(own=True)` 尺寸或 SHA 不符只 warn 不中止）；② `gguf_dir_problem()` 提示人话化（直说文件名必须等于官方全名，并提示用 `ln` 硬链改名）；③ README 写明自带 GGUF 目录不能放 `<数据根>/models/` 下；④ 新增 `patches/README.md` 补丁重放清单（0001 MTP 复用 / 0002 提示人话 / 0003 own 宽容）。
- **0.2.2.4**：装机链路修复——① `cmd/main` 启动时预建数据根下的 `models`/`packs`/`mtp` 并交付运行用户，装上再也不在 Step 6/7 撞 `PermissionError`；② 安装前数据根可写性预检 + 中文提示；③ MTP 层本地复用优先（零下载）；④ 安装成功清旧失败卡；⑤ 局域网 IP 访问不再被 403（Origin 白名单含本机网卡地址）；⑥ `native-host.json` 的 repo 残留路径自动校正。load/推理链路未改动。
- **0.2.2.3**：去壳重构——删 fnshell 壳与 python312 依赖，单进程原生直跑（STRATA_BIND=0.0.0.0、STRATA_PORT=8791 不变）；桌面图标改端口直达（不占统一网关前缀）；manifest 加 service_port/checkport（照官方 Cloudrive2 包形态）。v0.2.2.2 包被本构建取代，请直接装 0.2.2.3。
- **0.2.2.2**：fork 补丁——① installer 打包模式免 GitHub 下载（捆绑 gguf-py + `STRATA_LLAMA_CPP_ZIP` 镜像变量）；② wizard/文档写明模型目录属主要求与 `--gguf-dir` 直读。
- 0.2.2.1（首个构建，内容=官方 AppImage `Strata-0.2.2-linux-nvidia-cuda12` 解包，SHA256 `7506fd9e…fd539` 校验通过）。
