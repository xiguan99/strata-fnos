#!/usr/bin/env bash
# 把 fpk 上传到指定 release 作为资产（流式 -T，避免 --data-binary @- 读进内存 OOM）。
set -uo pipefail
REPO="${REPO:-xiguan99/strata-fnos}"
REL_ID="${REL_ID:?release id 必填}"
FILE="${1:?fpk 路径必填}"
NAME="$(basename "$FILE")"
TOKEN="${GITHUB_TOKEN:?需要 GITHUB_TOKEN}"
LOG="${LOG:-/vol1/1000/work/deepseek/logs/up_${NAME}.log}"
mkdir -p "$(dirname "$LOG")"

for attempt in 1 2 3; do
  echo "== attempt $attempt $(date +%H:%M:%S)" >>"$LOG"
  curl -sS --proxy http://127.0.0.1:10809 \
    -X POST "https://uploads.github.com/repos/$REPO/releases/$REL_ID/assets?name=$NAME" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/octet-stream" \
    -H "Expect:" \
    -T "$FILE" >>"$LOG" 2>&1
  rc=$?
  echo "== attempt $attempt rc=$rc size_sent=$(stat -c %s "$FILE") $(date +%H:%M:%S)" >>"$LOG"
  if [ $rc -eq 0 ]; then
    echo "OK" >>"$LOG"
    exit 0
  fi
  sleep 10
done
echo "FAILED after 3 attempts" >>"$LOG"
exit 1
