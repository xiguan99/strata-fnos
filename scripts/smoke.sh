#!/usr/bin/env bash
# strata 冒烟：出包前必须跑。覆盖三件以前漏掉的事：
#   1) 用**套件自带解释器** import serve/server.py（系统 python 缺 jinja2，用错解释器等于没测）
#   2) 模型加载链：真正 spawn serve/server.py 子进程（--engine mock），/health 必须 200
#   3) 补丁新增代码里「用了但没 import」的模块静态检查
#   4) 变体守卫：上游来源校验在位、不夹带自打补丁、cmd/main 绑 0.0.0.0
#   5) 真启 host 实测来源校验：局域网同源放行、外站与别的本机端口 403、改数据要 x-strata-token
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/fnpack/app"
CTL="$APP/control"
PY="$CTL/python/bin/python3.12"
PORT="${SMOKE_PORT:-8799}"
pass=0; fail=0
ok(){ pass=$((pass+1)); echo "  ✓ $*"; }
bad(){ fail=$((fail+1)); echo "  ✗ $*"; }

echo "── 1) 套件自带解释器 import serve/server.py"
if out=$(cd "$CTL" && PYTHONDONTWRITEBYTECODE=1 "$PY" -c "import serve.server" 2>&1); then
  ok "import serve.server"
else
  bad "import serve.server"; echo "$out" | tail -6
fi

echo "── 2) 模型加载链：spawn serve/server.py（--engine mock 代替真引擎）"
log=$(mktemp)
( cd "$CTL" && PYTHONDONTWRITEBYTECODE=1 "$PY" serve/server.py --engine mock --host 127.0.0.1 --port "$PORT" >"$log" 2>&1 ) &
pid=$!
ready=0
for _ in $(seq 1 60); do
  if curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then ready=1; break; fi
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.5
done
if [ "$ready" = 1 ]; then ok "子进程起来且 /health 200"; else bad "子进程未就绪"; tail -15 "$log"; fi
kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true
rm -f "$log"

echo "── 3) 补丁新增代码的 import 对齐（静态检查）"
if out=$(python3 "$ROOT/scripts/check-unbound-modules.py" \
    "$CTL/serve/server.py" "$CTL/setup.py" "$CTL/tools/strata_mcp.py" "$CTL/serve/test_server.py" 2>&1); then
  echo "$out" | sed 's/^/    /'
  if echo "$out" | grep -q "used but never bound"; then bad "有模块用了但没 import"; else ok "无未绑定模块引用"; fi
else
  bad "静态检查脚本自身失败"; echo "$out" | tail -5
fi

echo "── 4) 变体守卫（原版直通：上游来源校验在位，无自打补丁）"
[ -f "$APP/host/server/request-guard.mjs" ] && ok "request-guard.mjs 在位" || bad "缺 request-guard.mjs"
grep -q 'STRATA_ALLOWED_HOSTS' "$APP/host/server/request-guard.mjs" && ok "guard 是 #7130 版本" || bad "guard 不是 #7130 版本"
grep -q 'createRequestGuard' "$APP/host/server/index.mjs" && ok "index.mjs 已接 guard" || bad "index.mjs 没接 guard"
if grep -q 'isLocalOrigin' "$APP/host/server/index.mjs"; then bad "残留旧 0004（isLocalOrigin）"; else ok "无 0004 残留"; fi
grep -q '^export STRATA_BIND="0.0.0.0"' "$ROOT/fnpack/cmd/main" && ok "cmd/main 绑 0.0.0.0" || bad "cmd/main 未绑 0.0.0.0"

echo "── 5) 局域网来源校验实测（真启 host，同源放行 / 外站与别的本机端口拦截 / 改数据要 token）"
HPORT="${SMOKE_HOST_PORT:-8798}"
hh=$(mktemp -d); hlog=$(mktemp)
( HOME="$hh" STRATA_PORT="$HPORT" STRATA_BIND=0.0.0.0 "$APP/node/node" "$APP/host/server/index.mjs" >"$hlog" 2>&1 ) &
hpid=$!
hready=0
for _ in $(seq 1 40); do
  if curl -fsS -m 2 "http://127.0.0.1:$HPORT/" -o "$hh/index.html" 2>/dev/null; then hready=1; break; fi
  kill -0 "$hpid" 2>/dev/null || break
  sleep 0.5
done
if [ "$hready" = 1 ]; then
  ok "host 起来且页面 200"
  if grep -q 'name="strata-token"' "$hh/index.html"; then ok "页面注入了 x-strata-token"; else bad "页面没有 token meta"; fi
  LANIP=$(hostname -I 2>/dev/null | awk '{print $1}'); [ -n "$LANIP" ] || LANIP=192.168.0.1
  tok=$(grep -o 'name="strata-token" content="[a-f0-9]*"' "$hh/index.html" | sed 's/.*content="//;s/"//')
  c1=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "Host: $LANIP:$HPORT" -H "Origin: http://$LANIP:$HPORT" "http://127.0.0.1:$HPORT/api/native")
  [ "$c1" = 200 ] && ok "局域网同源 /api/native 200" || bad "局域网同源被拦（$c1）"
  c2=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "Origin: http://evil.example" "http://127.0.0.1:$HPORT/api/native")
  [ "$c2" = 403 ] && ok "外站 Origin 403" || bad "外站 Origin 未拦（$c2）"
  c3=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "Origin: http://127.0.0.1:2295" "http://127.0.0.1:$HPORT/api/native")
  [ "$c3" = 403 ] && ok "别的本机端口 Origin 403" || bad "别的本机端口 Origin 未拦（$c3）"
  c4=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST -H "Host: $LANIP:$HPORT" -H "Origin: http://$LANIP:$HPORT" -H 'content-type: application/json' -d '{"gib":8}' "http://127.0.0.1:$HPORT/api/native/ram-reserve")
  [ "$c4" = 403 ] && ok "改数据无 token 403" || bad "改数据无 token 未被拦（$c4）"
  c5=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST -H "Host: $LANIP:$HPORT" -H "Origin: http://$LANIP:$HPORT" -H "x-strata-token: $tok" -H 'content-type: application/json' -d '{"gib":8}' "http://127.0.0.1:$HPORT/api/native/ram-reserve")
  [ "$c5" = 200 ] && ok "改数据带 token 200" || bad "改数据带 token 被拦（$c5）"
else
  bad "host 未就绪"; tail -15 "$hlog"
fi
kill "$hpid" 2>/dev/null || true; wait "$hpid" 2>/dev/null || true
rm -rf "$hh" "$hlog"

echo "── 结果：$pass 通过 / $fail 失败"
[ "$fail" -eq 0 ]
