#!/usr/bin/env python3
"""Read-only: join host /api/monitor request records (wall clock) with engine-log
per-request lines (ground truth), segmented per engine run, then bucket by the
external CPU hog (whisper started 22:57:46). Does not touch the engine or config."""
import json, re, statistics, urllib.request, datetime, csv

MON = "http://127.0.0.1:8791/api/monitor"
LOG = "/vol1/@appdata/strata/data/.local/share/strata/install/strata-swift-iq3_s.log"
BASE = "/vol1/1000/work/deepseek/fnos-apps/strata/rebase-0.2.9/"
OUT = BASE + "requests-join.tsv"

REQ = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([\d.]+) tok/s\), "
                 r"(\d+) generated in (\d+) ms \(([\d.]+) tok/s\)")
HIT = re.compile(r"hit rate: ([\d.]+)% \((\d+) hits / (\d+) lookups\)")
DT = re.compile(r"decode timing: (\d+) windows,.*?([\d.]+) ms/window = (?:adapt wait ([\d.]+) \+ )?verify .*?"
                r"GPU-reach wait ([\d.]+) \+ per-layer host ([\d.]+) \[(.*?)CPU ([\d.]+)\]")
PARK = re.compile(r"conversation cache: parked (\d+) tokens in ([\d.]+) ms; parked=\d+ bytes=(\d+)")
START = re.compile(r"\[strata\] (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) engine started")


def segments():
    """[{start: dt, reqs: [...]}, ...] split by 'engine started' lines."""
    segs, cur = [], None
    with open(LOG, errors="replace") as f:
        for line in f:
            m = START.search(line)
            if m:
                cur = dict(start=datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), reqs=[])
                segs.append(cur)
                continue
            if cur is None:
                continue
            m = REQ.search(line)
            if m:
                cur["reqs"].append(dict(
                    pn=int(m.group(1)), reused=int(m.group(2)), chunk=int(m.group(3)),
                    pre_ms=int(m.group(4)), pre=float(m.group(5)),
                    gen=int(m.group(6)), dec_ms=int(m.group(7)), dec=float(m.group(8)),
                    wait=[], host=[], cpu=[], hit=None, parks=[]))
                continue
            q = cur["reqs"][-1] if cur["reqs"] else None
            if q is None:
                continue
            m = DT.search(line)
            if m:
                q["wait"].append(float(m.group(4))); q["host"].append(float(m.group(5)))
                q["cpu"].append(float(m.group(7)))
                continue
            m = HIT.search(line)
            if m:
                q["hit"] = float(m.group(1)); continue
            m = PARK.search(line)
            if m:
                q["parks"].append((int(m.group(1)), float(m.group(2)), int(m.group(3))))
    return segs


def monitor():
    with urllib.request.urlopen(MON, timeout=10) as r:
        return json.load(r)["requests"]


def main():
    segs = segments()
    mon = []
    for r in sorted(monitor(), key=lambda r: r["at"]):
        at = datetime.datetime.fromisoformat(r["at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        mon.append((at, r))
    rows = []
    for i, seg in enumerate(segs):
        end = segs[i + 1]["start"] if i + 1 < len(segs) else datetime.datetime(2100, 1, 1)
        recs = [(a, r) for a, r in mon if seg["start"] <= a < end]
        reqs = [q for q in seg["reqs"] if q["gen"] >= 20]
        if not recs or not reqs:
            continue
        n = min(len(recs), len(reqs))
        for (at, r), q in zip(recs[-n:], reqs[-n:]):
            rows.append(dict(run=seg["start"].strftime("%H:%M:%S"), time=at.strftime("%m-%d %H:%M:%S"),
                             pn=q["pn"], reused=q["reused"], pre=round(q["pre"], 1), gen=q["gen"],
                             dec=round(q["dec"], 1), tps=r.get("tps"),
                             host=round(statistics.median(q["host"]), 2) if q["host"] else "",
                             cpu=round(statistics.median(q["cpu"]), 2) if q["cpu"] else "",
                             wait=round(statistics.median(q["wait"]), 2) if q["wait"] else "",
                             hit=q["hit"] if q["hit"] is not None else "",
                             park=len(q["parks"])))
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t"); w.writeheader(); w.writerows(rows)
    print("rows", len(rows), "->", OUT)
    for r in rows:
        print("%s %s pn=%-6s reused=%-6s pre=%-7s gen=%-5s dec=%-5s host=%-6s cpu=%-7s wait=%-6s hit=%-5s park=%s" % (
            r["run"], r["time"], r["pn"], r["reused"], r["pre"], r["gen"], r["dec"], r["host"], r["cpu"], r["wait"],
            r["hit"], r["park"]))
    buckets = {"whisper OFF (≤22:57)": [r for r in rows if r["time"] < "11-01 22:57"],
               "whisper ON (≥22:57)": [r for r in rows if r["time"] >= "11-01 22:57"]}
    for label, b in buckets.items():
        if not b:
            print(label, "n=0"); continue
        cold = [r for r in b if r["reused"] == 0]
        print("%-22s n=%2d cold=%d  dec med %5.1f  pre(cold) med %6.1f  host med %5.2f  cpu med %6.2f  wait med %5.2f" % (
            label, len(b), len(cold), statistics.median([r["dec"] for r in b]),
            statistics.median([r["pre"] for r in cold] or [0]),
            statistics.median([r["host"] for r in b if r["host"] != ""] or [0]),
            statistics.median([r["cpu"] for r in b if r["cpu"] != ""] or [0]),
            statistics.median([r["wait"] for r in b if r["wait"] != ""] or [0])))


if __name__ == "__main__":
    main()
