#!/bin/bash
set -u
cd /vol1/1000/work/deepseek/fnos-apps/strata/rebase-0.2.9
GG=/vol1/1000/local-models/swift-abliterated-iq3_s/Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf
export STRATA_BENCH_TARGET_TOKENS=8000 STRATA_BENCH_MAX_TOKENS=192
echo "[exp] baseline arm start $(date +%H:%M:%S)"
python3 bench91k.py dd-base 2
echo "[exp] baseline done, start dd $(date +%H:%M:%S)"
( timeout 75 dd if="$GG" of=/dev/null bs=4M iflag=direct status=none; echo "[dd] end $(date +%H:%M:%S)" ) &
DD=$!
sleep 3
python3 bench91k.py dd-io 2
wait $DD
echo "[exp] all done $(date +%H:%M:%S)"
