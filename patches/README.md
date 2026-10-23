# 补丁重放清单（fnOS 打包版）

`fnpack/app/control/` 是官方 Strata Control bundle，目录里自带 `.gitignore` 的 `*`，**不进本仓库**；
它的改动只体现在 fpk 包里，换上游 bundle 时必须照下列补丁重放。两份都是"改一处、跑一次自测"级别的外科手术。

| # | 文件 | 补丁 | 作用 | 存档 |
|---|------|------|------|------|
| 0001 | `control/setup.py` | MTP 层本地复用优先 | 下载前先找 `<数据根>/models/mtp`、历史安装目录的 `mtp/`、`models/mtp/`，有成品就整棵树复用（同盘硬链零拷贝）；`mtp_pack`/`mtp_rt` 按需执行。国内连不上 HF 时靠它活 | `control-setup-mtp-reuse.md` |
| 0002 | `control/setup.py` | `gguf_dir_problem()` 提示人话化 | 自带 GGUF 目录里文件存在但文件名不合官方命名时，直接说"文件名必须完全等于 `Swift-…-{q}-0000{i}-of-00002.gguf`"，并提示用 `ln` 硬链改名；不再只回一句 `folder has no …` 让人误以为目录里没文件 | 本文档 + 源码注释 `fnOS 补丁` |
| 0003 | `control/setup.py` | `verify_sha256(own=True)` 宽容 | 官方在自带 GGUF 尺寸/SHA 与 pinned 不符时 `fail()` 中止（own 分支只保证"不删你的文件"）。改版/破甲权重尺寸必然不同 → 必挂。补丁改成 `warn()` + `return` 继续使用你的文件 | `0003-setup-own-gguf-lenient.patch` |

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
