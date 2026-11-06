#!/usr/bin/env python3
"""Roll F2+F1 back to the values captured in before-f2f1.json, then reload.

Why: the F2+F1 arm did not make the 91k benchmark faster (prefill -6.5%,
decode +21% but inside the baseline's own spread), so Rain's rule 5 says
restore the original values immediately and report; no F3, no ad-hoc extras.
"""
import json, os, shutil, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8791"
HERE = os.path.dirname(os.path.abspath(__file__))
BEFORE = os.path.join(HERE, "before-f2f1.json")
BACKUP = os.path.join(HERE, "backup-20261101-2221")
PRESET_DIR = "/vol1/@appdata/strata/data/.config/strata/presets"
PRESET_ID = "9cfe6d30aac84a2fbd5a894cffc3aed9"


def req(path, method="GET", body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def state():
    return req("/api/native", timeout=120)[1]


def main():
    before = json.load(open(BEFORE))
    orig_args = before["settings"]["args"]
    orig_env = before["settings"]["env"]

    st, _ = req("/api/native/models/swift-iq3_s/settings", "PATCH",
                {"values": {"args": orig_args, "env": orig_env}})
    print("patch settings:", st, flush=True)

    src = os.path.join(BACKUP, PRESET_ID + ".json")
    dst = os.path.join(PRESET_DIR, PRESET_ID + ".json")
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print("preset restored from backup:", dst, flush=True)

    # confirm the flags are gone / back
    st, cur = req("/api/native/models/swift-iq3_s/settings")
    args = cur.get("values", {}).get("args") or []
    out = {}
    for i, x in enumerate(args):
        if x in ("--pool-workers", "--conversation-cache-mib",
                 "--conversation-cache-slots", "--prompt-cache"):
            out[x] = args[i + 1] if i + 1 < len(args) else None
    print("flags now:", out, "env:", cur.get("values", {}).get("env"), flush=True)

    s = state()
    inst = (s.get("instances") or [{}])[0]
    iid = inst.get("id")
    print("instance before unload:", inst.get("status"), iid, flush=True)
    if iid:
        st, _ = req("/api/native/instances/%s/unload" % iid, "POST", {})
        print("unload:", st, flush=True)

    t0 = time.time()
    st, job = req("/api/native/operations", "POST",
                  {"kind": "load", "args": {"model": "swift-iq3_s"}})
    print("load:", st, (job or {}).get("id"), flush=True)

    hist = {}
    deadline = t0 + 1500
    ready_at = None
    while time.time() < deadline:
        time.sleep(5)
        code = req("/v1/models")[0]
        hist["v1:%d" % code] = hist.get("v1:%d" % code, 0) + 1
        s = state()
        inst = (s.get("instances") or [{}])[0]
        if inst.get("status") == "ready" and inst.get("port"):
            ready_at = time.time() - t0
            break
    print(json.dumps({"reload_s": round(ready_at, 1) if ready_at else None,
                      "hist": hist}, ensure_ascii=False), flush=True)

    s = state()
    jid = (job or {}).get("id")
    j = [x for x in (s.get("jobs") or []) if x.get("id") == jid][0]
    print("job:", j["state"], "|", (j.get("summary") or "")[:200], flush=True)
    inst = (s.get("instances") or [{}])[0]
    print("instance:", inst.get("status"), inst.get("port"), flush=True)


if __name__ == "__main__":
    main()
