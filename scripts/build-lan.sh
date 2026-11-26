#!/bin/bash
# strata 原版直通构建脚本（0.2.22 起不再需要自打 0004）
# 树内容 = 官方 AppImage 解包（node/host/control/harness）+ 官方 engine bundle，一个字不改；
# 局域网可用靠两件事：cmd/main 原生绑 0.0.0.0（上游 request-guard 于是跳过 Host 校验），
# 且浏览器同源请求的 Origin 与 Host 头一致（上游 #7130 判为 self）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FP_BIN="$ROOT/scripts/fnpack-1.2.3-linux-amd64"
VERSION="$(cat "$ROOT/VERSION" | tr -d '[:space:]')"
echo "═══ 构建 strata(原版+局域网放行) v$VERSION ═══"

APP="$ROOT/fnpack/app"
export PYTHONDONTWRITEBYTECODE=1

# ── 0) 变体校验：原版直通（上游自带来源校验，无自打补丁） ──
echo "[0/3] 变体校验（原版直通，无 0004）..."
if [ ! -f "$APP/host/server/request-guard.mjs" ]; then
  echo "✗ 缺 host/server/request-guard.mjs（上游 #7130 的来源校验）——这不是可直通的树"; exit 1
fi
if ! grep -q 'STRATA_ALLOWED_HOSTS' "$APP/host/server/request-guard.mjs"; then
  echo "✗ request-guard.mjs 不是 #7130 版本（无 STRATA_ALLOWED_HOSTS）"; exit 1
fi
if ! grep -q 'createRequestGuard' "$APP/host/server/index.mjs"; then
  echo "✗ index.mjs 没接上 request-guard，来源校验不生效"; exit 1
fi
if grep -q 'isLocalOrigin' "$APP/host/server/index.mjs"; then
  echo "✗ 仍有旧 0004 痕迹（isLocalOrigin）——0.2.22 起该补丁已退役，混用会绕开新守卫"; exit 1
fi
if ! grep -q '^export STRATA_BIND="0.0.0.0"' "$ROOT/fnpack/cmd/main"; then
  echo "✗ cmd/main 未绑 0.0.0.0：本包靠原生绑定 + 同源 Origin 放行局域网"; exit 1
fi
# 引擎必须随包：engine-bundle 下恰好一个版本目录，且 strata 本体与 BUILD.json 都在
eb_dirs=$(find "$APP/engine-bundle" -mindepth 1 -maxdepth 1 -type d 2>/dev/null)
eb_n=$(printf '%s' "$eb_dirs" | grep -c . )
if [ "$eb_n" != "1" ]; then
  echo "✗ engine-bundle 下应恰好一个引擎目录，实际 $eb_n 个（四个组分 rsync --delete 会把它清空，必须补回）"; exit 1
fi
if [ ! -x "$eb_dirs/strata" ] || [ ! -f "$eb_dirs/BUILD.json" ]; then
  echo "✗ $eb_dirs 里缺 strata 本体或 BUILD.json"; exit 1
fi
echo "  ✓ 引擎随包：$(basename "$eb_dirs")（$(du -sh "$eb_dirs" | cut -f1)）"
for pair in \
  "host/server/native-control.mjs:rolesToDevices" \
  "control/serve/server.py:def gpu_names_by_index" \
  "control/setup.py:reuse_local_mtp" \
  "control/tools/strata_mcp.py:def _reference" \
  "host/server/native-control.mjs:engineProof"; do
  f="${pair%%:*}"; pat="${pair#*:}"
  if grep -q "$pat" "$APP/$f" 2>/dev/null; then
    echo "✗ $f 里出现其它补丁痕迹「$pat」——这个包是原版直通，不该带任何自打补丁"; exit 1
  fi
done
echo "  ✓ 原版直通（上游来源校验在位，无自打补丁）"

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
