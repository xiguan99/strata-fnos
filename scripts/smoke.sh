#!/usr/bin/env bash
# strata 冒烟：出包前必须跑。覆盖三件以前漏掉的事：
#   1) 用**套件自带解释器** import serve/server.py（系统 python 缺 jinja2，用错解释器等于没测）
#   2) 模型加载链：真正 spawn serve/server.py 子进程（--engine mock），/health 必须 200
#   3) 补丁新增代码里「用了但没 import」的模块静态检查
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

echo "── 4) 补丁守卫（内容层面）"
grep -q 'isLocalOrigin' "$APP/host/server/index.mjs" && ok "0004 局域网 Origin" || bad "0004 局域网 Origin"
grep -q 'function rolesToDevices' "$APP/host/server/native-control.mjs" && ok "0005 角色→设备" || bad "0005 角色→设备"
grep -q 'def gpu_names_by_index' "$CTL/serve/server.py" && ok "0005 卡名日志" || bad "0005 卡名日志"
if grep -q 'functools\.' "$CTL/serve/server.py"; then
  grep -q '^import functools' "$CTL/serve/server.py" && ok "functools 有 import" || bad "serve/server.py 用了 functools 但没 import"
else ok "无 functools 依赖"; fi
grep -q 'engineProof' "$APP/host/server/native-control.mjs" && ok "0010 就绪外部凭据" || bad "0010 就绪外部凭据"

echo "── 结果：$pass 通过 / $fail 失败"
[ "$fail" -eq 0 ]
