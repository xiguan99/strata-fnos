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

## 版本

`VERSION` 文件 = 上游版本.构建号。
- **0.2.2.3（本次）**：去壳重构——删 fnshell 壳与 python312 依赖，单进程原生直跑（STRATA_BIND=0.0.0.0、STRATA_PORT=8791 不变）；桌面图标改端口直达（不占统一网关前缀）；manifest 加 service_port/checkport（照官方 Cloudrive2 包形态）。v0.2.2.2 包被本构建取代，请直接装 0.2.2.3。
- **0.2.2.2**：fork 补丁——① installer 打包模式免 GitHub 下载（捆绑 gguf-py + `STRATA_LLAMA_CPP_ZIP` 镜像变量）；② wizard/文档写明模型目录属主要求与 `--gguf-dir` 直读。
- 0.2.2.1（首个构建，内容=官方 AppImage `Strata-0.2.2-linux-nvidia-cuda12` 解包，SHA256 `7506fd9e…fd539` 校验通过）。
