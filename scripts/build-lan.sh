#!/bin/bash
# strata 原版（pristine）构建脚本：只做 fnOS 打包，不含任何 fnOS 补丁
# 树内容 = 官方 AppImage 解包（node/host/control/harness）+ 官方 engine bundle，原样打包
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FP_BIN="$ROOT/scripts/fnpack-1.2.3-linux-amd64"
VERSION="$(cat "$ROOT/VERSION" | tr -d '[:space:]')"
echo "═══ 构建 strata(原版+局域网放行) v$VERSION ═══"

APP="$ROOT/fnpack/app"
export PYTHONDONTWRITEBYTECODE=1

# ── 0) 原版校验：树里不许出现我们补丁的任何痕迹 ──
echo "[0/3] 原版校验（补丁痕迹必须为 0）..."
for pair in \
  "host/server/native-control.mjs:rolesToDevices" \
  "control/serve/server.py:def gpu_names_by_index" \
  "control/setup.py:reuse_local_mtp" \
  "control/tools/strata_mcp.py:def _reference" \
  "host/server/native-control.mjs:engineProof"; do
  f="${pair%%:*}"; pat="${pair#*:}"
  if grep -q "$pat" "$APP/$f" 2>/dev/null; then
    echo "✗ $f 里出现补丁痕迹「$pat」——这不是原版树"; exit 1
  fi
done
grep -q "isLocalOrigin" "$APP/host/server/index.mjs" || { echo "✗ 缺 0004 isLocalOrigin"; exit 1; }
echo "  ✓ 仅含 0004（isLocalOrigin），其余补丁痕迹为 0"

# ── 1) 图标 ──
echo "[1/3] 生成图标 ..."
SRC_ICON="$ROOT/assets/Strata.png"
ffmpeg -y -v error -i "$SRC_ICON" -vf "scale=256:256:flags=lanczos" "$APP/ui/images/icon_256.png"
ffmpeg -y -v error -i "$SRC_ICON" -vf "scale=64:64:flags=lanczos"   "$APP/ui/images/icon_64.png"
cp "$APP/ui/images/icon_256.png" "$ROOT/fnpack/ICON.PNG"
cp "$APP/ui/images/icon_256.png" "$ROOT/fnpack/ICON_256.PNG"
find "$APP" -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
n=$(find "$APP" -xtype l | wc -l); [ "$n" -gt 0 ] && { find "$APP" -xtype l -delete; echo "  清理断链 $n 个"; }
chmod 644 "$ROOT/fnpack/ICON.PNG" "$ROOT/fnpack/ICON_256.PNG" 2>/dev/null || true

# ── 2) 隐私扫描 ──
echo "[2/3] 隐私扫描 ..."
if grep -rIl -e '/vol[0-9]\+/@app\(home\|data\)/' "$APP" --exclude-dir=node_modules 2>/dev/null | head -5 | grep -q .; then
  echo "✗ 疑似泄漏本机路径，中止（自行 grep 处理）"; exit 1
fi
if grep -rn -e 'sk-[A-Za-z0-9]\{20,\}' -e 'token=[A-Za-z0-9]' "$ROOT/fnpack/cmd" "$ROOT/fnpack/config" "$ROOT/fnpack/app/ui" "$ROOT/scripts/build-pristine.sh" 2>/dev/null | head -5 | grep -q .; then
  echo "✗ 自产文件疑似泄漏密钥，中止（自行 grep 处理）"; exit 1
fi

# ── 3) fnpack ──
if [ -f "$ROOT/dist/strata_v${VERSION}_x86.fpk" ] && [ "${STRATA_ALLOW_REBUILD:-0}" != "1" ]; then
  echo "✗ dist/strata_v${VERSION}_x86.fpk 已存在（覆盖请设 STRATA_ALLOW_REBUILD=1）"; exit 1
fi
echo "[3/3] fnpack 打包 ..."
sed -i "s/^version .*=.*/version               = $VERSION/" "$ROOT/fnpack/manifest"
cd "$ROOT/fnpack"
"$FP_BIN" build
mv -f strata.fpk "$ROOT/dist/strata_v${VERSION}_x86.fpk"
ls -la "$ROOT/dist/"
CHK="$(mktemp -d)"
tar -xzf "$ROOT/dist/strata_v${VERSION}_x86.fpk" -C "$CHK" ICON.PNG ICON_256.PNG || true
for ic in ICON.PNG ICON_256.PNG; do
  m=$(stat -c%a "$CHK/$ic" 2>/dev/null || echo missing)
  [ "$m" = "644" ] || { echo "✗ $ic 权限=$m（必须 644）"; rm -rf "$CHK"; exit 1; }
done
rm -rf "$CHK"
echo "  ✓ 图标权限 644 断言通过"
echo "═══ 完成 ═══"
