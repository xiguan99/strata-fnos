# 0004 局域网 Origin 补丁（host/server/index.mjs）

来源：v0.2.2.4 的 `538dc27d`，2026-10-29 因同步上游丢失导致回归，此处固化存档。
判定口径：回环 + 本机主机名（含 `.local`）+ 私有网段；公网域名/公网 IP 仍 403。

适用版本：strata app 0.2.7.x / host control 0.1.52（官方原版硬编码白名单的任意版本）。

## 补丁全文（unified diff，`git apply -p1` 或手工照改）

```diff
diff --git a/fnpack/app/host/server/index.mjs b/fnpack/app/host/server/index.mjs
index e059a657..70e443c5 100644
--- a/fnpack/app/host/server/index.mjs
+++ b/fnpack/app/host/server/index.mjs
@@ -31,7 +31,7 @@ import { findBrowser, findPlaywrightMcp } from './agent/browser-probe.mjs';
  * live metrics and the local inference API. No runtime dependencies.
  */
 import http from 'node:http';
-import { homedir } from 'node:os';
+import { homedir, hostname as osHostname } from 'node:os';
 import { statfsSync, cpSync, createReadStream, createWriteStream, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
 import { pipeline } from 'node:stream/promises';
 import { spawn } from 'node:child_process';
@@ -952,6 +952,28 @@ await cloudflare.inspect();
 
 /* --------------------------------------------------------------- routing --- */
 
+/* host controls 的来源判定：官方只放行 localhost / 127.0.0.1 / [::1]。fnOS 装机版没有桌面宿主外壳，
+   用户是浏览器直接开 http://<NAS 局域网 IP>:8791 —— 那种 Origin 会被判成"不是本机"，所有 /api 一律 403，
+   界面就冒出「打开本地 Strata 应用以使用主机控件」。
+   fnOS 补丁（对应 538dc27d）：放行回环、本机主机名与私有网段（10/8、172.16/12、192.168/16、169.254/16，
+   IPv6 的 ::1、fe80::/10、fc00::/7）。公网域名/公网 IP 仍然 403。 */
+const localHostNames = (() => {
+  const names = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);
+  const host = String(osHostname() ?? '').toLowerCase();
+  if (host) { names.add(host); names.add(`${host}.local`); }
+  return names;
+})();
+const privateV4 = [/^10\./, /^127\./, /^169\.254\./, /^192\.168\./, /^172\.(1[6-9]|2[0-9]|3[01])\./];
+function isLocalOrigin(hostname) {
+  const name = String(hostname ?? '').toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
+  if (!name) return true;
+  if (localHostNames.has(name)) return true;
+  if (name === '::1' || name === '0:0:0:0:0:0:0:1') return true;
+  if (/^fe[89ab][0-9a-f]:/.test(name) || /^f[cd][0-9a-f]{2}:/.test(name)) return true;
+  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(name)) return privateV4.some((re) => re.test(name));
+  return false;
+}
+
 const server = http.createServer(async (req, res) => {
   const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
   const path = url.pathname;
@@ -969,7 +991,7 @@ const server = http.createServer(async (req, res) => {
   try {
     if (path.startsWith('/api/') && req.headers.origin) {
       const origin = new URL(req.headers.origin);
-      if (!['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname)) return error(res, 403, 'Open the local Strata app to use host controls.');
+      if (!isLocalOrigin(origin.hostname)) return error(res, 403, 'Open the local Strata app to use host controls.');
     }
     if (path.startsWith('/api/')) {
       const modelOperation = !['GET', 'OPTIONS'].includes(method) && /^\/api\/(models|local-models|runtime|image-models|engine|native)(\/|$)/.test(path) && !['/api/runtime/plan', '/api/engine/validate', '/api/local-models/inspect'].includes(path);
```

## 自测判据

```bash
grep -c isLocalOrigin host/server/index.mjs          # 2
node --check host/server/index.mjs                    # 无输出
# 期望：192.168.31.211 → true；172.32.0.1 / 8.8.8.8 / example.com → false
```
