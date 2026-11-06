#!/usr/bin/env python3
"""Apply F2+F1 to the strata model settings (and mirror pool-workers into the preset),
then unload+load through the real 8791 entry, measuring reload time and 5xx during load.

Phases (separate CLI verbs so each can be reported):
  before   -> dump current settings (args/env) + preset args/env to before-f2f1.json
  patch    -> PATCH settings (pool-workers 8, + 3 cache flags, + STRATA_POOL_SPIN_US=0) and preset pool-workers 8
  reload   -> unload + load, poll 8791/18500 every 1.5 s, print status-code histogram + duration
  verify   -> print engine pid env/args + info.log tail line + instance status
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8791"
MODEL = "swift-iq3_s"
OUT = "/vol1/1000/work/deepseek/fnos-apps/strata/rebase-0.2.9/before-f2f1.json"
PRESET = "/vol1/@appdata/strata/data/.config/strata/presets/9cfe6d30aac84a2fbd5a894cffc3aed9.json"
INFO = "/vol1/@appdata/strata/info.log"
ENGINE_LOG_DIR = "/vol1/@appdata/strata/data/.local/share/strata/install"

CACHE_FLAGS = [("--conversation-cache-mib", "2048"), ("--conversation-cache-slots", "2"), ("--prompt-cache", "2")]


def req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def settings():
    st, j = req("/api/native/models/%s/settings" % MODEL)
    assert st == 200, (st, j)
    return j


def do_before():
    s = settings()
    v = s.get("values") or {}
    p = json.load(open(PRESET))
    dump = {"settings": {"args": v.get("args"), "env": v.get("env")},
            "preset": {"args": p.get("settings", {}).get("args"), "env": p.get("settings", {}).get("env")},
            "captured": time.strftime("%F %T")}
    json.dump(dump, open(OUT, "w"), indent=1, ensure_ascii=False)
    args = v.get("args") or []
    print(json.dumps({"args_len": len(args), "env": v.get("env"),
                      "pool_workers_idx": args.index("--pool-workers") if "--pool-workers" in args else None,
                      "pool_workers_val": args[args.index("--pool-workers") + 1] if "--pool-workers" in args else None,
                      "has_cache_flags": [f for f, _ in CACHE_FLAGS if f in args],
                      "preset_pool_workers": (p.get("settings", {}).get("args") or {}).get("--pool-workers")},
                     ensure_ascii=False))


def set_kv(args, flag, val):
    """set flag=val in a flat args list, appending if absent (value always follows the flag)."""
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args) and not str(args[i + 1]).startswith("--"):
            args[i + 1] = val
        else:
            args.insert(i + 1, val)
    else:
        args.extend([flag, val])
    return args


def do_patch():
    s = settings()
    v = s.get("values") or {}
    args = list(v.get("args") or [])
    env = dict(v.get("env") or {})
    orig = dict(json.load(open(OUT))["settings"])
    orig_env = orig["env"]
    set_kv(args, "--pool-workers", "8")
    for f, val in CACHE_FLAGS:
        set_kv(args, f, val)
    env["STRATA_POOL_SPIN_US"] = "0"
    st, j = req("/api/native/models/%s/settings" % MODEL, "PATCH", {"values": {"args": args, "env": env}})
    print("PATCH settings:", st, str(j)[:200])
    # mirror pool-workers into the preset (preset overrides instance args)
    p = json.load(open(PRESET))
    pa = p.setdefault("settings", {}).setdefault("args", {})
    if pa.get("--pool-workers") is not None:
        pa["--pool-workers"] = 8
        json.dump(p, open(PRESET, "w"), indent=1, ensure_ascii=False)
        print("preset --pool-workers -> 8")
    else:
        print("preset has no --pool-workers, nothing mirrored")
    # confirm
    s2 = settings()
    a2 = (s2.get("values") or {}).get("args") or []
    print(json.dumps({"pool_workers": a2[a2.index("--pool-workers") + 1],
                      "cache": {f: a2[a2.index(f) + 1] for f, _ in CACHE_FLAGS if f in a2},
                      "spin": ((s2.get("values") or {}).get("env") or {}).get("STRATA_POOL_SPIN_US"),
                      "orig_pool_workers": orig["args"][orig["args"].index("--pool-workers") + 1],
                      "orig_env_count": len(orig_env)}, ensure_ascii=False))


def instance():
    st, j = req("/api/native")
    ins = (j.get("instances") or []) if isinstance(j, dict) else []
    return ins[0] if ins else None


def probe(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def do_reload():
    inst = instance()
    assert inst, "no instance to unload"
    print("unload", inst["id"], req("/api/native/instances/%s/unload" % inst["id"], "POST"))
    time.sleep(3)
    hist = {}
    t0 = time.time()
    st, j = req("/api/native/operations", "POST", {"kind": "load", "args": {"model": MODEL}})
    print("load POST", st, str(j)[:120])
    # poll while loading
    ready = None
    while time.time() - t0 < 1500:
        for label, url in (("panel", BASE + "/"), ("api", BASE + "/api/native"),
                           ("v1models", BASE + "/v1/models"), ("engine", "http://127.0.0.1:18500/v1/models")):
            code = probe(url)
            hist.setdefault((label, code), 0)
            hist[(label, code)] += 1
        inst = instance()
        if inst and inst.get("status") == "ready":
            ready = time.time() - t0
            break
        if inst and inst.get("status") == "error":
            print("INSTANCE ERROR")
            break
        time.sleep(1.5)
    print(json.dumps({"reload_s": round(ready, 1) if ready else None,
                      "status_hist": {f"{k[0]}:{k[1]}": v for k, v in sorted(hist.items())}}, ensure_ascii=False))


def do_verify():
    inst = instance() or {}
    pid = None
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            cl = open("/proc/%s/cmdline" % d, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        if "components/engine" in cl and "strata" in cl and "serve" in cl:
            pid = int(d)
    envs = {}
    if pid:
        for kv in open("/proc/%d/environ" % pid, "rb").read().decode("utf-8", "replace").split("\0"):
            if kv.startswith("STRATA_"):
                k, _, v = kv.partition("=")
                envs[k] = v
    log = os.path.join(ENGINE_LOG_DIR, "strata-%s.log" % MODEL)
    ready_lines = []
    for line in open(log, errors="replace").read().splitlines()[-400:]:
        if "with everything loaded" in line or "VRAM free" in line:
            ready_lines.append(line[:160])
    tail = open(INFO, errors="replace").read().splitlines()[-3:]
    print(json.dumps({"instance_status": inst.get("status"), "pid": pid,
                      "spin_us": envs.get("STRATA_POOL_SPIN_US"), "sm75": envs.get("STRATA_SM75_PERF"),
                      "engine_ready_lines": ready_lines[-2:], "info_tail": [t[:170] for t in tail]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    {"before": do_before, "patch": do_patch, "reload": do_reload, "verify": do_verify}[sys.argv[1]]()
