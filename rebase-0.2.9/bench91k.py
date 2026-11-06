#!/usr/bin/env python3
"""91k prompt / 8K prefill + 512 decode cold-run benchmark for strata (native, 8791 gateway).

Usage: python3 bench91k.py <tag> [runs]
Writes one JSON line per run to stdout; also appends to results-91k.jsonl next to this file.

Cold = unique nonce salt in the prompt prefix every run (cache_n must be 0).
Records per run: prefill/decode tok/s, TTFT, per-layer host ms, ms/window, GPU util/power,
loadavg, MemAvailable, page cache (Cached), pswpout, engine CPU cores burned.
Does NOT change any config and does NOT unload anything.
"""
import glob
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

BASE = "/vol1/1000/work/deepseek/fnos-apps/strata/rebase-0.2.9"
LOG = "/vol1/@appdata/strata/data/.local/share/strata/install/strata-swift-iq3_s.log"
URL = "http://127.0.0.1:8791/v1/chat/completions"
TARGET_TOKENS = int(os.environ.get("STRATA_BENCH_TARGET_TOKENS") or 91000)
MAX_TOKENS = int(os.environ.get("STRATA_BENCH_MAX_TOKENS") or 512)
HZ = os.sysconf("SC_CLK_TCK")


def api_key():
    try:
        k = (json.load(open("/vol1/@appdata/strata/data/.strata/data/state.json")).get("serving") or {}).get("apiKey")
        if k:
            return k
    except Exception:
        pass
    for p in sorted(glob.glob("/vol1/@appdata/strata/data/.strata/data/native-instances/*.json"), key=os.path.getmtime, reverse=True):
        d = json.load(open(p))
        if d.get("api_key") and d.get("status") != "stopped":
            return d["api_key"]
    raise SystemExit("no api key")


KEY = api_key()
FILLER = ("The maintenance log records routine checks of the cooling loop, fan curves and power draw. "
          "Nothing unusual was reported during the shift, and the operator signed off after inspecting the rack. ")


def prompt_of(target):
    n = max(1, int(target / 36.45))          # calibrated: one filler block ≈ 36.45 tokens
    salt = "[run %s] " % uuid.uuid4().hex[:10]  # cold: different prefix every run -> no prefix cache
    return ("以下是设备维护记录的长文档，请阅读后回答最后一个问题。\n\n" + salt + FILLER * n +
            "\n\n问题：上面反复提到的检查对象是哪三项？请一句话回答。")


def meminfo():
    m = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        m[k] = int(v.strip().split()[0]) // 1024   # MiB
    return m


def engine_pid():
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            cl = open("/proc/%s/cmdline" % d, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        if "components/engine" in cl and "strata" in cl and "serve" in cl:
            return int(d)
    return None


def cpu_ticks(pid):
    tot = 0
    for t in glob.glob("/proc/%d/task/*/stat" % pid):
        try:
            parts = open(t).read().rsplit(")", 1)[1].split()
            tot += int(parts[11]) + int(parts[12])   # utime + stime (fields 14,15 after ')' split)
        except Exception:
            pass
    return tot


def gpu_sample():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,power.draw",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    r = {}
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 4:
            r[p[0]] = {"util": float(p[1]), "mem": float(p[2]), "power": float(p[3])}
    return r


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.s = {"loadavg": [], "util": {1: [], 2: []}, "power": {1: [], 2: []},
                  "memavail": [], "cached": [], "pswpout": []}

    def run(self):
        while not self.stop.is_set():
            la = os.getloadavg()[0]
            mi = meminfo()
            sw = 0
            try:
                for line in open("/proc/vmstat"):
                    if line.startswith("pswpout"):
                        sw = int(line.split()[1])
                        break
            except Exception:
                pass
            g = gpu_sample()
            self.s["loadavg"].append(la)
            self.s["memavail"].append(mi.get("MemAvailable", 0))
            self.s["cached"].append(mi.get("Cached", 0))
            self.s["pswpout"].append(sw)
            for idx in (1, 2):
                if str(idx) in g:
                    self.s["util"][idx].append(g[str(idx)]["util"])
                    self.s["power"][idx].append(g[str(idx)]["power"])
            self.stop.wait(0.5)

    def summary(self):
        def med(xs):
            xs = sorted(xs)
            return round(xs[len(xs) // 2], 1) if xs else None

        def mx(xs):
            return round(max(xs), 1) if xs else None
        return dict(loadavg_med=med(self.s["loadavg"]), loadavg_max=mx(self.s["loadavg"]),
                    gpu1_util=med(self.s["util"][1]), gpu2_util=med(self.s["util"][2]),
                    gpu1_power=med(self.s["power"][1]), gpu2_power=med(self.s["power"][2]),
                    memavail_min=min(self.s["memavail"]) if self.s["memavail"] else None,
                    cached_med=med(self.s["cached"]),
                    pswpout_delta=self.s["pswpout"][-1] - self.s["pswpout"][0] if len(self.s["pswpout"]) > 1 else 0)


def parse_log(offset):
    """decode-timing lines written while our request ran."""
    try:
        with open(LOG, "r", errors="replace") as f:
            f.seek(offset)
            text = f.read()
    except Exception:
        return {}
    rows = []
    for line in text.splitlines():
        if "strata decode timing:" in line:
            r = {}
            try:
                r["ms_window"] = float(line.split(" ms/window =")[0].split(",")[-1].strip().split()[-1])
                r["host_ms"] = float(line.split("per-layer host ")[1].split(" [")[0])
                r["cpu_ms"] = float(line.split(" CPU ")[1].split("]")[0])
                r["gpu_wait_ms"] = float(line.split("GPU-reach wait ")[1].split(" ")[0])
                r["vram_hits"] = float(line.split("VRAM hits ")[1].split(",")[0])
                r["pcie"] = float(line.split("PCIe ")[1].split(";")[0])
                rows.append(r)
            except Exception:
                continue
    if not rows:
        return {}
    def med(k):
        xs = sorted(x[k] for x in rows if k in x)
        return round(xs[len(xs) // 2], 2) if xs else None
    return {("log_" + k): med(k) for k in ("ms_window", "host_ms", "cpu_ms", "gpu_wait_ms", "vram_hits", "pcie")} | {"log_lines": len(rows)}


def run_once(tag, n):
    text = prompt_of(TARGET_TOKENS)
    body = {"model": "swift-iq3_s", "messages": [{"role": "user", "content": text}],
            "max_tokens": MAX_TOKENS, "temperature": 0}
    pid = engine_pid()
    off = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    t0 = time.time()
    c0 = cpu_ticks(pid) if pid else 0
    smp = Sampler()
    smp.start()
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            j = json.loads(r.read())
        err = None
    except Exception as e:
        j, err = {}, repr(e)
    wall = time.time() - t0
    c1 = cpu_ticks(pid) if pid else 0
    smp.stop.set()
    smp.join(timeout=3)
    t = (j.get("timings") or {}) if j else {}
    msg = (((j.get("choices") or [{}])[0].get("message") or {}) if j else {}).get("content") or ""
    rec = dict(tag=tag, run=n, time=time.strftime("%H:%M:%S"), wall=round(wall, 1), error=err,
               prompt_n=t.get("prompt_n"), cache_n=t.get("cache_n"), pred_n=t.get("predicted_n"),
               prefill_tps=round(t.get("prompt_per_second") or 0, 1),
               decode_tps=round(t.get("predicted_per_second") or 0, 1),
               ttft_s=round((t.get("prompt_ms") or 0) / 1000, 2),
               engine_cores=round((c1 - c0) / HZ / wall, 2) if pid and wall > 0 else None,
               engine_pid=pid, answer=msg[:40])
    rec.update(smp.summary())
    rec.update(parse_log(off))
    return rec


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    for i in range(1, n + 1):
        r = run_once(tag, i)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        with open(os.path.join(BASE, "results-91k.jsonl"), "a") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        time.sleep(20)   # let the engine settle; conversation cache parks between runs
