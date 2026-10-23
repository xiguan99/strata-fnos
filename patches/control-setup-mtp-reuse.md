# control/setup.py 补丁：MTP 层本地复用优先（v0.2.2.4）

`fnpack/app/control/` 是官方 Strata Control bundle（自带 `.gitignore` 的 `*`，不进本仓库），
它的改动只体现在 fpk 包里，重新解包上游 bundle 时必须重放。本文件是唯一权威记录。

目标：装机环境连不上 Hugging Face（`hf-mirror` 也只有 ~150KB/s、常握手超时），
而本机往往已有一份成品 MTP draft layer（`<数据根>/models/mtp`，或历史安装目录的 `mtp/`、`models/mtp/`）。
官方 setup 只按"目标目录里有没有"判断，目标空就下载——必须先把本机已有的那份复用过来。

## 1) 新增函数（插在 `def fetch_mtp(` 之前）

```python
def reuse_local_mtp(mtp: Path, data: Path, elsewhere) -> str:
    """装机环境常常连不上 Hugging Face，而本机可能已经有一份成品 MTP 层（<data>/models/mtp，或以前安装目录的
    mtp/、models/mtp/）。先把那份整棵树复用过来（tensors/、rt/、manifest 一起；同一文件系统用硬链接，零拷贝），
    只有本机确实没有时才去下载。复用完 gguf 与 rt 都在本地生成，不再联网。"""
    candidates = [data / "models" / "mtp"] + [Path(e) / "models" / "mtp" for e in elsewhere] + \
                 [Path(e) / "mtp" for e in elsewhere]
    for src in candidates:
        ready = src / "mtp-q2_0.gguf"
        try:
            if not ready.is_file() or ready.stat().st_size < 1_000_000:
                continue
            if src.resolve() == mtp.resolve():
                continue
        except OSError:
            continue
        try:
            mtp.mkdir(parents=True, exist_ok=True)
            for root, _dirs, files in os.walk(src):
                rel = os.path.relpath(root, src)
                here = mtp if rel == "." else mtp / rel
                here.mkdir(parents=True, exist_ok=True)
                for name in files:
                    if name.endswith(".lock"):
                        continue
                    item, dest = Path(root) / name, here / name
                    if dest.exists():
                        continue
                    try:
                        os.link(item, dest)                 # same filesystem: no copy
                    except OSError:
                        try:
                            shutil.copy2(item, dest)        # another drive: copy it once
                        except OSError as e:
                            warn(f"could not copy {item} ({e})")
            if (mtp / "mtp-q2_0.gguf").is_file():
                return str(src)
        except OSError as e:
            warn(f"could not reuse the draft layer at {src} ({e})")
    return ""
```

## 2) 真正执行的 MTP 分支（Linux，`ok(f"model prepared: {pack}")` 之后那一段）

原：

```python
    if corrupt or not (rt / "experts.bin").exists():
        say("  The MTP draft layer (speculative decoding, ~2x faster output) comes from the original Qwen checkpoint:")
        say("  only its ~5 GB of MTP tensors are downloaded.")
        fetch_mtp(mtp, env)
        run([sys.executable, str(ROOT / "tools" / "mtp_pack.py"), "--src", str(mtp), "--experts", "q2_0",
             "--out", str(mtp / "mtp-q2_0.gguf")], env=env)
        run([sys.executable, str(ROOT / "tools" / "mtp_rt.py"), "--gguf", str(mtp / "mtp-q2_0.gguf"), "--out", str(rt)],
            env=env)
```

改后：

```python
    if corrupt or not (rt / "experts.bin").exists():
        say("  The MTP draft layer (speculative decoding, ~2x faster output) comes from the original Qwen checkpoint:")
        say("  only its ~5 GB of MTP tensors are downloaded.")
        reused = "" if corrupt else reuse_local_mtp(mtp, data, elsewhere)
        if reused:
            ok(f"the draft layer is already on this PC: reused it from {reused} (nothing downloaded)")
        else:
            fetch_mtp(mtp, env)
        if not (mtp / "mtp-q2_0.gguf").exists():           # a reused layer already has it
            run([sys.executable, str(ROOT / "tools" / "mtp_pack.py"), "--src", str(mtp), "--experts", "q2_0",
                 "--out", str(mtp / "mtp-q2_0.gguf")], env=env)
        if not (rt / "experts.bin").exists():
            run([sys.executable, str(ROOT / "tools" / "mtp_rt.py"), "--gguf", str(mtp / "mtp-q2_0.gguf"), "--out", str(rt)],
                env=env)
```

## 3) 另一处（Step 7 的 use_mtp 分支，`mtp = (find_in(roots, "mtp/mtp-q2_0.gguf") ...)` 之后）

原：

```python
    if use_mtp and not (mtp / "mtp-q2_0.gguf").exists():
        say("  The MTP draft layer (speculative decoding) comes from the original Qwen checkpoint: only its ~5 GB of MTP tensors are downloaded.")
        fetch_mtp(mtp, env)
```

改后：

```python
    if use_mtp and not (mtp / "mtp-q2_0.gguf").exists():
        reused = reuse_local_mtp(mtp, data, elsewhere)
        if reused:
            ok(f"the MTP draft layer is already on this PC: reused it from {reused} (nothing downloaded)")
    if use_mtp and not (mtp / "mtp-q2_0.gguf").exists():
        say("  The MTP draft layer (speculative decoding) comes from the original Qwen checkpoint: only its ~5 GB of MTP tensors are downloaded.")
        fetch_mtp(mtp, env)
```

## 验证（v0.2.2.4 实测）

数据根 `/tmp/st4/share`，`models/mtp` → `/vol1/1000/mtp`（6.5G，含 `mtp-q2_0.gguf`、`tensors/`、`rt/`），目标 `mtp/` 清空后跑 install：

```
[ok] the draft layer is already on this PC: reused it from /tmp/st4/share/models/mtp (nothing downloaded)
[ok] MTP draft layer: /tmp/st4/share/mtp/rt
```

install 45 秒完成（Step 6 pack 复用 + Step 7 MTP 复用），目标 `mtp/` 6.5G 完整，全程无 huggingface 请求。
