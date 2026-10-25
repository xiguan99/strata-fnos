#!/bin/bash
# strata-fnos 发布脚本（GitHub API 版，照 freellmapi-fnos）
# 创建 Release 并上传本次版本对应的 dist fpk。依赖：GITHUB_TOKEN 环境变量
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REPO="${GITHUB_REPOSITORY:-xiguan99/strata-fnos}"
GITHUB_TOKEN="${GITHUB_TOKEN:?需要设置 GITHUB_TOKEN 环境变量}"
CUR_VERSION="$(cat VERSION | tr -d '[:space:]')"
TAG="v$CUR_VERSION"
DIST="${DIST_DIR:-$ROOT/dist}"
FPK="$DIST/strata_v${CUR_VERSION}_x86.fpk"
echo "═══ 发布 $CUR_VERSION 到 $REPO ═══"
[ -f "$FPK" ] || { echo "✗ 找不到 $FPK，先跑 scripts/build.sh"; exit 1; }

EXISTING_ID="$(curl -sS -H "Authorization: token $GITHUB_TOKEN" "https://api.github.com/repos/$REPO/releases/tags/$TAG" 2>/dev/null | grep -m1 '"id"' | grep -oE '[0-9]+' || true)"
if [ -n "$EXISTING_ID" ]; then
  echo "Release 已存在（id=$EXISTING_ID），删除重建"
  curl -sS -X DELETE -H "Authorization: token $GITHUB_TOKEN" "https://api.github.com/repos/$REPO/releases/$EXISTING_ID" >/dev/null 2>&1 || true
fi

NOTES_DEFAULT="## qwenpaw-fnos $CUR_VERSION"$'\n\n'"上游版本 $(echo "$CUR_VERSION" | cut -d. -f1-3)，fnOS 原生打包。"
BODY_JSON="$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "${NOTES:-$NOTES_DEFAULT}")"
RELEASE_JSON="$(curl -sS -X POST -H "Authorization: token $GITHUB_TOKEN" -H "Accept: application/vnd.github+json" -H "Content-Type: application/json" "https://api.github.com/repos/$REPO/releases" -d "{\"tag_name\":\"$TAG\",\"name\":\"QwenPaw $CUR_VERSION\",\"body\":$BODY_JSON,\"draft\":false,\"prerelease\":false}")"
RELEASE_ID="$(echo "$RELEASE_JSON" | grep -m1 '"id"' | grep -oE '[0-9]+')"
[ -n "$RELEASE_ID" ] || { echo "✗ 创建 Release 失败: $RELEASE_JSON"; exit 1; }
echo "上传 $(basename "$FPK") ..."
# 用 -T 流式上传：--data-binary 会把整包读进内存，1.1GB 直接 curl: out of memory
UP="$(curl -sS -X POST -T "$FPK" -H "Authorization: token $GITHUB_TOKEN" -H "Content-Type: application/octet-stream" --max-time 3600 "https://uploads.github.com/repos/$REPO/releases/$RELEASE_ID/assets?name=$(basename "$FPK")")"
echo "$UP" | grep -q '"id"' && echo "  ✓ $(echo "$UP" | grep -m1 '"browser_download_url"' || true)" || { echo "  ✗ $UP"; exit 1; }
echo "═══ 发布完成: https://github.com/$REPO/releases/tag/$TAG ═══"
