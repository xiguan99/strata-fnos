#!/bin/bash
# strata-fnos 构建脚本（照 qwenpaw-fnos 版式）
# 流程: 树内容=官方 AppImage 解包（node/host/control/harness/engine-bundle，已入库）→ 生成图标 → 隐私扫描 → fnpack 出 .fpk
# v0.2.2.3 起无壳（不注入 fnshell），单进程原生直跑官方 host
# 运行环境: NAS 本机（ffmpeg + scripts/fnpack 二进制）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FP_BIN="$ROOT/scripts/fnpack-1.2.3-linux-amd64"
VERSION="$(cat "$ROOT/VERSION" | tr -d '[:space:]')"
echo "═══ 构建 strata v$VERSION ═══"

APP="$ROOT/fnpack/app"

# ── 1) 运行时树注入（内容主体已入库；无壳，单进程原生直跑）──
echo "[1/3] 生成图标 ..."
# 图标：官方源图 512×512 → 256/64（NAS 无 PIL，ffmpeg lanczos）
SRC_ICON="$ROOT/assets/Strata.png"
ffmpeg -y -v error -i "$SRC_ICON" -vf "scale=256:256:flags=lanczos" "$APP/ui/images/icon_256.png"
ffmpeg -y -v error -i "$SRC_ICON" -vf "scale=64:64:flags=lanczos"   "$APP/ui/images/icon_64.png"
cp "$APP/ui/images/icon_256.png" "$ROOT/fnpack/ICON.PNG"
cp "$APP/ui/images/icon_256.png" "$ROOT/fnpack/ICON_256.PNG"
# 断链清理（安装器遍历全树设权限，断链会炸）
n=$(find "$APP" -xtype l | wc -l); [ "$n" -gt 0 ] && { find "$APP" -xtype l -delete; echo "  清理断链 $n 个"; }

echo "[2/3] 隐私扫描 ..."
# 本机绝对路径：全树扫（官方资产理论上也应干净）
if grep -rIl -e '/vol[0-9]\+/@app\(home\|data\)/' "$APP" --exclude-dir=node_modules 2>/dev/null | head -5 | grep -q .; then
  echo "✗ 疑似泄漏本机路径，中止（自行 grep 处理）"; exit 1
fi
# 密钥样式：只扫自产文件（上游 vendored 源码里的 token 赋值是代码，不算）
if grep -rn -e 'sk-[A-Za-z0-9]\{20,\}' -e 'token=[A-Za-z0-9]' "$ROOT/fnpack/cmd" "$ROOT/fnpack/config" "$ROOT/fnpack/app/ui" "$ROOT/README.md" "$ROOT/scripts/build.sh" 2>/dev/null | head -5 | grep -q .; then
  echo "✗ 自产文件疑似泄漏密钥，中止（自行 grep 处理）"; exit 1
fi

# 图标权限：fnOS 应用中心以组身份读取 ICON.PNG，组无读权限会静默退回默认占位图
chmod 644 "$ROOT/fnpack/ICON.PNG" "$ROOT/fnpack/ICON_256.PNG" 2>/dev/null || true

# ── 2.5) 补丁守卫：局域网 Origin（2026-10-29 回归事故，同步上游时被丢掉过一次）──
echo "[2.5/3] 补丁守卫：局域网 Origin ..."
HOST_JS="$APP/host/server/index.mjs"
if ! grep -q 'isLocalOrigin' "$HOST_JS"; then
  echo "✗ $HOST_JS 缺局域网 Origin 补丁（isLocalOrigin 0 命中）——上游同步把它丢了"; \
  echo "  按 patches/README.md 的 0004 重放后再构建"; exit 1
fi
grep -q 'if (!isLocalOrigin(origin.hostname))' "$HOST_JS" || {
  echo "✗ isLocalOrigin 存在但白名单判断没接上（仍是官方硬编码 localhost/127.0.0.1）"; exit 1; }
if command -v node >/dev/null 2>&1; then
  node --check "$HOST_JS" || { echo "✗ $HOST_JS 语法检查失败"; exit 1; }
fi
echo "  ✓ 局域网 Origin 补丁在位"

# ── 2.6) 补丁守卫：主卡选择必须落到引擎（2026-10-29 回归事故：0.1.52 起主卡存成裸 "gpu": 0，引擎跑到 40HX 上）──
echo "[2.6/3] 补丁守卫：主卡落到引擎 ..."
NC_JS="$APP/host/server/native-control.mjs"
grep -q 'function rolesToDevices' "$NC_JS" || {
  echo "✗ $NC_JS 缺角色→设备解析补丁（rolesToDevices 0 命中）——上游同步把它丢了"; \
  echo "  按 patches/README.md 的 0005 重放后再构建"; exit 1; }
grep -q 'CUDA_VISIBLE_DEVICES=${shown.join' "$NC_JS" || {
  echo "✗ 角色补丁不完整（日志那一行没写出角色/物理卡/CUDA_VISIBLE_DEVICES 三样）"; exit 1; }
grep -q 'CUDA0. Choose the Main card' "$NC_JS" || {
  echo "✗ 角色补丁缺「解析不出就拒绝启动」的拦截"; exit 1; }
grep -q 'function lastRoles' "$NC_JS" || {
  echo "✗ 角色补丁缺 lastRoles 兜底（上次运行的主卡）"; exit 1; }
grep -q 'function lastTuneRoles' "$NC_JS" || {
  echo "✗ 角色补丁缺 lastTuneRoles（设置文件丢了角色时读上次 tune 的主卡）"; exit 1; }
grep -q 'function vramRefusal' "$NC_JS" || {
  echo "✗ 缺显存预检补丁（0006：卡装不下就在启动前拦住并说清数字）"; exit 1; }
MCP_PY="$APP/control/tools/strata_mcp.py"
SRV_PY="$APP/control/serve/server.py"
grep -q 'def gpu_names_by_index' "$SRV_PY" || {
  echo "✗ $SRV_PY 缺卡名日志补丁（gpu_names_by_index 0 命中）"; \
  echo "  按 patches/README.md 的 0005 重放后再构建"; exit 1; }
python3 "$ROOT/scripts/test-gpu-roles.py" || {
  echo "✗ 主卡角色映射没通过（一卡/主卡 0 号/主卡+副卡都要成立）"; exit 1; }
grep -q 'def log_card_note' "$SRV_PY" || {
  echo "✗ card_note 没写进运行日志（角色那一行只会在控制台，日志里看不到）"; exit 1; }
grep -q 'CUDA_VISIBLE_DEVICES={visible}' "$SRV_PY" || {
  echo "✗ 卡名日志补丁不完整（card_note 没把 CUDA_VISIBLE_DEVICES 打进日志）"; exit 1; }
if command -v node >/dev/null 2>&1; then
  node --check "$NC_JS" || { echo "✗ $NC_JS 语法检查失败"; exit 1; }
fi
"$APP/control/python/bin/python3.12" -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$SRV_PY" \
  || { echo "✗ $SRV_PY 语法检查失败"; exit 1; }
echo "  ✓ 主卡选取 + 卡名日志补丁在位"
# 0007（2026-10-29）：横幅别说谎 —— 打包版走 Strata Control 原生引擎，不再是「模拟/设计迭代」
IDX_JS="$APP/host/server/index.mjs"
grep -q 'native engine — models load through Strata Control' "$IDX_JS" \
  || { echo "✗ 推理横幅补丁丢失（打包版又变回 simulated 文案）"; exit 1; }
grep -q 'vramSignature' "$NC_JS" \
  || { echo "✗ 显存台账按参数签名失效的补丁丢失（改了 --kv-resident 仍会被旧数字拦住）"; exit 1; }
grep -q 'const limit=needed?needed+512:floor' "$NC_JS" \
  || { echo "✗ 显存预检余量补丁丢失（会把刚好装得下的加载判死）"; exit 1; }
grep -q 'def _reference(value)' "$MCP_PY" \
  || { echo "✗ 安装器组件引用补丁丢失（会把整个模型误标成缺引擎）"; exit 1; }

# 0007b（2026-10-29）：打包前自检 —— strata_mcp.py 的 class Strata 必须完整。
# 事故：往类体中间插了一个模块级 def，类体在第 641 行就结束，其后 270 行方法全掉到模块级，
# 运行期 s.hardware() → AttributeError → /api/native 的 catalog 整块降级成「此安装未报告受支持的显卡」。
( cd "$APP" && "./control/python/bin/python3" - <<'PYCHK'
import sys, inspect
sys.path.insert(0, "control")
import tools.strata_mcp as m
missing = [n for n in ("hardware","configs","read_config","model_paths","install_job",
                       "tracked_server")
           if not hasattr(m.Strata, n)]
assert not missing, f"Strata 缺方法: {missing}"
leaked = [n for n in ("hardware","configs","model_paths") if callable(getattr(m, n, None))]
assert not leaked, f"这些方法掉到模块级了（类体被截断）: {leaked}"
lines = len(inspect.getsource(m.Strata).splitlines())
assert lines > 900, f"class Strata 只有 {lines} 行（应 >900），类体被截断"
print(f"ok (class Strata {lines} 行, {len(m.Strata.__dict__)} 个成员)")
PYCHK
) || { echo "✗ strata_mcp.py 自检失败：class Strata 不完整，不许出包"; exit 1; }

# ── 2.9) 同名版本不许覆盖：该出的新包必须 bump VERSION（2026-10-29 Rain 要求）──
if [ -f "$ROOT/dist/strata_v${VERSION}_x86.fpk" ] && [ "${STRATA_ALLOW_REBUILD:-0}" != "1" ]; then
  echo "✗ dist/strata_v${VERSION}_x86.fpk 已存在：同一版本号不出第二个包，请先 bump VERSION（开发期覆盖请设 STRATA_ALLOW_REBUILD=1）"
  exit 1
fi

# ── 3) fnpack ──
echo "[3/3] fnpack 打包 ..."
sed -i "s/^version .*=.*/version               = $VERSION/" "$ROOT/fnpack/manifest"
cd "$ROOT/fnpack"
"$FP_BIN" build
mv -f strata.fpk "$ROOT/dist/strata_v${VERSION}_x86.fpk"
ls -la "$ROOT/dist/"
# ── 出包后硬断言：图标必须 644（否则应用中心显示占位图）──
CHK="$(mktemp -d)"
tar -xzf "$ROOT/dist/strata_v${VERSION}_x86.fpk" -C "$CHK" ICON.PNG ICON_256.PNG || true
for ic in ICON.PNG ICON_256.PNG; do
  m=$(stat -c%a "$CHK/$ic" 2>/dev/null || echo missing)
  [ "$m" = "644" ] || { echo "✗ $ic 权限=$m（必须 644）"; rm -rf "$CHK"; exit 1; }
done
rm -rf "$CHK"
echo "  ✓ 图标权限 644 断言通过"
echo "═══ 完成 ═══"
