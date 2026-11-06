#!/bin/bash
# 上传 strata v0.2.9.1 的 fpk 资产到已存在的 GitHub Release，并核对 size/digest。
# 带自动重试：每次重试先清掉上次残留的 starter 资产（GitHub 单请求不可续传）。
# 用法：DSH_SECRET_GITHUB_PAT_PUSH=... setsid nohup bash upload-asset-0291.sh &
set -uo pipefail
export http_proxy=http://127.0.0.1:10809
export https_proxy=http://127.0.0.1:10809

REPO=xiguan99/strata-fnos
RID=432217665
NAME=strata_v0.2.9.1_x86.fpk
FPK=/vol1/1000/work/deepseek/fnos-apps/strata/dist/strata_v0.2.9.1_x86.fpk
EXPECT_SIZE=1148417832
EXPECT_SHA=ab37b181feb5e6dc7df7636487cb4135aed7c4f20a14f66bede77a0a872c6f4a
TOK=${DSH_SECRET_GITHUB_PAT_PUSH:?missing token}
LOG=/vol1/1000/work/deepseek/fnos-apps/strata/rebase-0.2.9/upload-asset-0291.log
MAX_TRY=4

log() { echo "[$(date '+%F %T')] $*" >>"$LOG"; }

# 本地先复核，对不上就停手（Rain 的规矩：不传错的包）
sz=$(stat -c %s "$FPK")
sh=$(sha256sum "$FPK" | cut -d' ' -f1)
log "local check: size=$sz sha256=$sh"
if [ "$sz" != "$EXPECT_SIZE" ] || [ "$sh" != "$EXPECT_SHA" ]; then
  log "ABORT: local file mismatch (expect $EXPECT_SIZE / $EXPECT_SHA)"
  echo 2 > /tmp/upload-0291.rc; exit 2
fi
log "local check OK"

clean_stale() {
  local ids id code
  ids=$(curl -s -H "Authorization: Bearer $TOK" \
    "https://api.github.com/repos/$REPO/releases/$RID/assets" \
    | python3 -c 'import sys,json;print(" ".join(str(a["id"]) for a in json.load(sys.stdin)))' 2>/dev/null)
  for id in $ids; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE -H "Authorization: Bearer $TOK" \
      "https://api.github.com/repos/$REPO/releases/assets/$id")
    log "deleted stale asset $id HTTP=$code"
  done
}

for try in $(seq 1 $MAX_TRY); do
  log "=== attempt $try/$MAX_TRY ==="
  clean_stale
  code=$(curl -sS -T "$FPK" -X POST \
    --connect-timeout 30 --keepalive-time 30 \
    -H "Authorization: Bearer $TOK" \
    -H "Content-Type: application/octet-stream" \
    "https://uploads.github.com/repos/$REPO/releases/$RID/assets?name=$NAME" \
    -o /tmp/asset-resp.json -w '%{http_code}')
  log "upload HTTP=$code"
  if [ "$code" = "201" ]; then
    log "upload succeeded on attempt $try"
    break
  fi
  sleep 20
done

# 核对
curl -s -H "Authorization: Bearer $TOK" \
  "https://api.github.com/repos/$REPO/releases/$RID/assets" -o /tmp/assets-final.json
python3 - "$EXPECT_SIZE" "$EXPECT_SHA" >>"$LOG" 2>&1 <<'PY'
import json, sys
size_exp, sha_exp = int(sys.argv[1]), sys.argv[2]
d = json.load(open('/tmp/assets-final.json'))
if not d:
    print('VERIFY FAIL: no assets'); sys.exit(1)
a = d[0]
size = a['size']; dig = a.get('digest') or ''
sha = dig.split(':', 1)[1] if dig.startswith('sha256:') else ''
print('name  :', a['name'])
print('state :', a['state'])
print('size  :', size, '(expect', size_exp, ')', 'OK' if size == size_exp else 'MISMATCH')
print('digest:', dig or '(none)')
print('sha   :', sha, 'OK' if sha == sha_exp else 'MISMATCH')
print('url   :', a['browser_download_url'])
ok = size == size_exp and sha == sha_exp
print('RESULT:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
PY
rc=$?
log "verify rc=$rc"
echo "$rc" > /tmp/upload-0291.rc
exit $rc
