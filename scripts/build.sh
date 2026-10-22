#!/bin/bash
# strata-fnos 构建脚本（照 qwenpaw-fnos 版式）
# 流程: 树内容=官方 AppImage 解包（node/host/control/harness/engine-bundle，已入库）→ 注入 fnshell/图标 → 隐私扫描 → fnpack 出 .fpk
# 运行环境: NAS 本机（ffmpeg + scripts/fnpack 二进制）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FP_BIN="$ROOT/scripts/fnpack-1.2.3-linux-amd64"
VERSION="$(cat "$ROOT/VERSION" | tr -d '[:space:]')"
echo "═══ 构建 strata v$VERSION ═══"

APP="$ROOT/fnpack/app"

# ── 1) 运行时树注入（壳脚本；内容主体已入库）──
echo "[1/3] 注入 fnshell + 图标 ..."
cp scripts/shell/fnshell.py "$APP/fnshell.py"
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
if grep -rn -e 'sk-[A-Za-z0-9]\{20,\}' -e 'token=[A-Za-z0-9]' "$ROOT/fnpack/cmd" "$ROOT/fnpack/config" "$ROOT/fnpack/app/ui" "$ROOT/fnpack/app/fnshell.py" "$ROOT/README.md" "$ROOT/scripts/build.sh" 2>/dev/null | head -5 | grep -q .; then
  echo "✗ 自产文件疑似泄漏密钥，中止（自行 grep 处理）"; exit 1
fi

# ── 3) fnpack ──
echo "[3/3] fnpack 打包 ..."
sed -i "s/^version .*=.*/version               = $VERSION/" "$ROOT/fnpack/manifest"
cd "$ROOT/fnpack"
"$FP_BIN" build
mv -f strata.fpk "$ROOT/dist/strata_v${VERSION}_x86.fpk"
ls -la "$ROOT/dist/"
echo "═══ 完成 ═══"
