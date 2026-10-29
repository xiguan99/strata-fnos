# 0007 `control/tools/strata_mcp.py`：`_reference` 归位 + class Strata 完整性自检

`fnpack/app/control/**` 不入 git（vendored 上游运行时），所以这条补丁以「为什么 + 怎么验证」记录，
而不是 diff；改坏了由 `scripts/build.sh` 的出包前自检拦住。

## 事故（2026-10-29，0.2.7.5 包）

为了修「整个模型被误标成缺引擎」，往 `strata_mcp.py` 的 `class Strata` **类体中间**插了一个
模块级函数：

```python
def _reference(value) -> bool:      # ← 顶格，类体在这里结束
    ...
    # ---- what is installed
    def configs(self) -> list[Path]: ...   # 之后 270 行方法全部变成 _reference 的内嵌函数
```

结果 `class Strata` 只剩 641 行之前的部分（21 个成员），`hardware`/`configs`/`model_paths` 全部丢失。
运行期 `Tools.strata_models()` 里 `s.hardware()` → `AttributeError: 'Strata' object has no attribute 'hardware'`
→ `internal error in strata_models` → host `/api/native` 的 `catalog` 整块降级：
`families: []`、`recommendation: {family: null, model: null}`，界面显示「此安装未报告受支持的显卡」，
「调优模型 / 下载并加载」按钮同时变灰。**显卡探测本身没问题**（`/api/hardware` 一直 200、三张卡都在）。

## 修法

`_reference()` 移到 `class Strata` **之前**（模块级，第 493 行），类体不再被截断。
注意 1542–1990 的 `shard_gaps` / `expected_files` / `files_progress` / `tool_defs` 等**本来就是模块级**
（上游在 1538 行放了 `SHARD_NAME`/`DEFAULT_MMPROJ_BYTES` 常量，类体不可能跨过它们续写），不要被它们误导。

## 自检（build.sh 出包前，不通过不许出包）

```bash
cd fnpack/app && ./control/python/bin/python3 -c "
import sys;sys.path.insert(0,'control');import tools.strata_mcp as m
assert hasattr(m.Strata,'hardware'),'Strata.hardware missing'
assert hasattr(m.Strata,'configs')
print('ok')"
```

build.sh 里的版本还额外断言：`hardware/configs/read_config/model_paths/install_job/tracked_server` 六个方法都在、
`hardware/configs/model_paths` 没有掉到模块级、`inspect.getsource(Strata)` 行数 > 900。

## 验收（0.2.7.6 装机）

- 自检输出：`ok (class Strata 1038 行, 64 个成员)`
- `/api/native?refresh=1` → `catalog.families` 8 个（qwen/swift/coder/unsloth/gyro/nvfp4/qwen36/deepseek41）、
  `contexts` 8 个、`recommendation = {family: qwen, model: IQ3_S, context: 131072}`、
  `installed.models[0].missing = []`、`ready = true`
