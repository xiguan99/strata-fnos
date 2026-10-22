#!/usr/bin/env python3
"""
Strata 单端口壳（基于 yuexps/FnDepot fngateway.py 扩展）：
UDS（飞牛网关 socket）+ TCP 对外端口双监听，统一路由：
  /shell/api/*  → 壳管理 API（状态/日志 SSE/服务控制/配置/登录）
  /strata[/..] → 反向代理 Strata 本体（含 WebSocket 直通）
  其余          → 控制面板静态站（Vue3 SPA，相对路径自洽，无需注入）
鉴权：shell_config.json 无 access_password=全放行；有则 LAN/私网段免登，其他需登录 cookie。
"""

import os
import re
import sys
import gzip
import json
import time
import select
import atexit
import signal
import socket
import hashlib
import secrets
import shutil
import logging
import argparse
import threading
import ipaddress
import subprocess
import http.client
import socketserver
import queue
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("fnshell")

HTML_ATTR_RE = re.compile(r'(?i)\b(src|href|action|poster)\s*=\s*(["\'])(/[^"\']*)')
JS_ROUTE_RE = re.compile(
    rb'function\s+([a-zA-Z0-9_$]+)\s*\(\s*([a-zA-Z0-9_$]+)\s*\)\s*\{\s*return\s*/\^\\/([a-zA-Z0-9_-]+)\(\?:\\/\|\$\)/\.test\(\s*\2\s*\)\s*\?\s*([a-zA-Z0-9_$]+)\s*:\s*(?:void 0|undefined)\s*\}'
)
CHUNK_SIZE = 64 * 1024


def generate_bridge_script(prefix: str) -> str:
    """前端运行时拦截与路由适配脚本"""
    return f"""<script>
(function (prefix) {{
  if (typeof window === "undefined" || !window.location || !prefix) return;
  if (window.location.pathname.indexOf(prefix) !== 0 && window.location.pathname !== prefix) return;

  var isAlreadyPrefixed = function (pathname) {{
    return pathname === prefix || pathname.indexOf(prefix + "/") === 0;
  }};

  var toGatewayUrl = function (value) {{
    if (!value) return value;
    var str = String(value).trim();
    if (/^(blob:|data:|javascript:|about:|#)/i.test(str)) return value;
    var url;
    try {{ url = new URL(str, window.location.href); }} catch (_) {{ return value; }}
    if (url.origin !== window.location.origin) return value;
    if (isAlreadyPrefixed(url.pathname)) return value;
    var rawPath = url.pathname.indexOf('/') === 0 ? url.pathname : '/' + url.pathname;
    var newPath = prefix + rawPath;
    if (str.indexOf('/') === 0 && str.indexOf('//') !== 0) {{
      return newPath + (url.search || '') + (url.hash || '');
    }}
    url.pathname = newPath;
    return url.toString();
  }};

  var toGatewaySrcset = function (srcsetStr) {{
    if (!srcsetStr || typeof srcsetStr !== "string") return srcsetStr;
    return srcsetStr.split(",").map(function (part) {{
      var item = part.trim();
      if (!item) return item;
      var segs = item.split(/\\s+/);
      var mapped = toGatewayUrl(segs[0]);
      if (mapped) segs[0] = mapped;
      return segs.join(" ");
    }}).join(", ");
  }};

  var rewriteHtmlString = function (html) {{
    if (typeof html !== "string" || html.indexOf("/") === -1) return html;
    var htmlAttrRe = new RegExp("\\\\b(src|href|action|poster)=([\\"'])(/[^\\"']*)\\\\2", "gi");
    return html.replace(htmlAttrRe, function (match, attr, quote, path) {{
      if (isAlreadyPrefixed(path) || path.indexOf("//") === 0) return match;
      return attr + "=" + quote + prefix + path + quote;
    }});
  }};

  var installBridge = function (targetWindow) {{
    if (!targetWindow || targetWindow.__fnGatewayBridgeReady) return;
    targetWindow.__fnGatewayBridgeReady = true;

    // 拦截 Location 原型（pathname、assign、replace）
    if (targetWindow.Location && targetWindow.Location.prototype) {{
      var locProto = targetWindow.Location.prototype;
      var locPathDesc = Object.getOwnPropertyDescriptor(locProto, "pathname");
      if (locPathDesc && locPathDesc.get && locPathDesc.configurable) {{
        var nativeLocPathGet = locPathDesc.get;
        var nativeLocPathSet = locPathDesc.set;
        try {{
          Object.defineProperty(locProto, "pathname", {{
            get: function () {{
              var p = nativeLocPathGet.call(this);
              if (isAlreadyPrefixed(p)) {{
                var stripped = p.slice(prefix.length);
                return stripped.indexOf("/") === 0 ? stripped : "/" + stripped;
              }}
              return p;
            }},
            set: function (val) {{
              if (nativeLocPathSet) {{
                if (typeof val === "string" && val.indexOf("/") === 0 && !isAlreadyPrefixed(val)) {{
                  val = prefix + val;
                }}
                return nativeLocPathSet.call(this, val);
              }}
            }},
            configurable: true,
            enumerable: true
          }});
        }} catch (_) {{}}
      }}

      if (locProto.assign) {{
        var nativeAssign = locProto.assign;
        locProto.assign = function (url) {{
          return nativeAssign.call(this, toGatewayUrl(url) || url);
        }};
      }}
      if (locProto.replace) {{
        var nativeReplace = locProto.replace;
        locProto.replace = function (url) {{
          return nativeReplace.call(this, toGatewayUrl(url) || url);
        }};
      }}
    }}

    // 拦截 History API
    if (targetWindow.history) {{
      var wrapHistory = function (orig) {{
        if (!orig) return orig;
        return function (state, unused, url) {{
          if (url) url = toGatewayUrl(url);
          return orig.call(this, state, unused, url);
        }};
      }};
      targetWindow.history.pushState = wrapHistory(targetWindow.history.pushState);
      targetWindow.history.replaceState = wrapHistory(targetWindow.history.replaceState);
    }}

    // 改写 Authorization 请求头
    var rewriteAuthHeaders = function (headers) {{
      if (!headers) return;
      if (typeof Headers !== "undefined" && headers instanceof Headers) {{
        if (headers.has("Authorization")) {{
          headers.set("X-FnProxy-Authorization", headers.get("Authorization"));
          headers.delete("Authorization");
        }}
      }} else if (Array.isArray(headers)) {{
        for (var i = 0; i < headers.length; i++) {{
          if (headers[i] && String(headers[i][0]).toLowerCase() === "authorization") {{
            headers[i][0] = "X-FnProxy-Authorization";
          }}
        }}
      }} else if (typeof headers === "object") {{
        for (var k in headers) {{
          if (k.toLowerCase() === "authorization") {{
            headers["X-FnProxy-Authorization"] = headers[k];
            delete headers[k];
          }}
        }}
      }}
    }};

    // 拦截 Fetch
    if (targetWindow.fetch) {{
      var nativeFetch = targetWindow.fetch.bind(targetWindow);
      targetWindow.fetch = function (input, init) {{
        init = init || {{}};
        if (init.headers) {{
          rewriteAuthHeaders(init.headers);
        }}
        if (typeof Request !== "undefined" && input instanceof Request) {{
          var mapped = toGatewayUrl(input.url);
          var hasAuth = false;
          try {{ hasAuth = input.headers && input.headers.has("Authorization"); }} catch (_) {{}}
          if (mapped !== input.url || hasAuth) {{
            try {{
              var reqHeaders = new Headers(input.headers);
              rewriteAuthHeaders(reqHeaders);
              input = new Request(mapped, Object.assign({{}}, init, {{ headers: reqHeaders }}));
            }} catch (_) {{
              try {{ input = new Request(mapped, input); }} catch (_) {{}}
            }}
          }}
        }} else {{
          input = toGatewayUrl(input);
        }}
        return nativeFetch(input, init);
      }};
    }}

    // 拦截 XMLHttpRequest
    if (targetWindow.XMLHttpRequest) {{
      var nativeXHROpen = targetWindow.XMLHttpRequest.prototype.open;
      targetWindow.XMLHttpRequest.prototype.open = function (method, url) {{
        arguments[1] = toGatewayUrl(url);
        return nativeXHROpen.apply(this, arguments);
      }};
      var nativeXHRSetHeader = targetWindow.XMLHttpRequest.prototype.setRequestHeader;
      targetWindow.XMLHttpRequest.prototype.setRequestHeader = function (header, value) {{
        if (header && String(header).toLowerCase() === "authorization") {{
          header = "X-FnProxy-Authorization";
        }}
        return nativeXHRSetHeader.call(this, header, value);
      }};
    }}

    // 拦截 DOM 元素属性
    var hookProperty = function (proto, prop, isSrcset) {{
      if (!proto) return;
      var desc = Object.getOwnPropertyDescriptor(proto, prop);
      if (!desc || !desc.set || !desc.configurable) return;
      var nativeSet = desc.set;
      Object.defineProperty(proto, prop, {{
        set: function (val) {{
          return nativeSet.call(this, isSrcset ? toGatewaySrcset(val) : toGatewayUrl(val));
        }},
        get: desc.get,
        configurable: true,
        enumerable: true
      }});
    }};

    if (targetWindow.HTMLImageElement) {{
      hookProperty(targetWindow.HTMLImageElement.prototype, "src", false);
      hookProperty(targetWindow.HTMLImageElement.prototype, "srcset", true);
    }}
    if (targetWindow.HTMLLinkElement) hookProperty(targetWindow.HTMLLinkElement.prototype, "href", false);
    if (targetWindow.HTMLAnchorElement) hookProperty(targetWindow.HTMLAnchorElement.prototype, "href", false);
    if (targetWindow.HTMLIFrameElement) hookProperty(targetWindow.HTMLIFrameElement.prototype, "src", false);
    if (targetWindow.HTMLScriptElement) hookProperty(targetWindow.HTMLScriptElement.prototype, "src", false);
    if (targetWindow.HTMLMediaElement) hookProperty(targetWindow.HTMLMediaElement.prototype, "src", false);
    if (targetWindow.HTMLSourceElement) {{
      hookProperty(targetWindow.HTMLSourceElement.prototype, "src", false);
      hookProperty(targetWindow.HTMLSourceElement.prototype, "srcset", true);
    }}

    // 拦截 setAttribute 与 innerHTML
    if (targetWindow.Element) {{
      var nativeSetAttr = targetWindow.Element.prototype.setAttribute;
      targetWindow.Element.prototype.setAttribute = function (name, value) {{
        var n = String(name).toLowerCase();
        if (n === "src" || n === "href" || n === "action") {{
          value = toGatewayUrl(value);
        }} else if (n === "srcset") {{
          value = toGatewaySrcset(value);
        }}
        return nativeSetAttr.call(this, name, value);
      }};

      var innerDesc = Object.getOwnPropertyDescriptor(targetWindow.Element.prototype, "innerHTML");
      if (innerDesc && innerDesc.set && innerDesc.configurable) {{
        var nativeInnerSet = innerDesc.set;
        Object.defineProperty(targetWindow.Element.prototype, "innerHTML", {{
          set: function (val) {{
            return nativeInnerSet.call(this, rewriteHtmlString(val));
          }},
          get: innerDesc.get,
          configurable: true,
          enumerable: true
        }});
      }}
    }}

    // 拦截超链接点击
    targetWindow.addEventListener("click", function (e) {{
      var target = e.target;
      while (target && target.tagName !== "A") target = target.parentElement;
      if (target && target.tagName === "A") {{
        var href = target.getAttribute("href") || target.href;
        var mapped = toGatewayUrl(href);
        if (mapped) {{
          target.setAttribute("href", mapped);
          if (target.href) target.href = mapped;
        }}
      }}
    }}, true);

    // 拦截 window.open 与 sendBeacon
    if (typeof targetWindow.open === "function") {{
      var nativeOpen = targetWindow.open.bind(targetWindow);
      targetWindow.open = function (url, target, features) {{
        return nativeOpen(toGatewayUrl(url), target, features);
      }};
    }}
    if (targetWindow.navigator && typeof targetWindow.navigator.sendBeacon === "function") {{
      var nativeBeacon = targetWindow.navigator.sendBeacon.bind(targetWindow.navigator);
      targetWindow.navigator.sendBeacon = function (url, data) {{
        return nativeBeacon(toGatewayUrl(url), data);
      }};
    }}

    // 拦截 Worker
    if (targetWindow.Worker) {{
      var nativeWorker = targetWindow.Worker;
      targetWindow.Worker = new Proxy(nativeWorker, {{
        construct: function (target, args, newTarget) {{
          args = [toGatewayUrl(args[0])].concat(args.slice(1));
          return Reflect.construct(target, args, newTarget);
        }}
      }});
    }}
    if (targetWindow.SharedWorker) {{
      var nativeSharedWorker = targetWindow.SharedWorker;
      targetWindow.SharedWorker = new Proxy(nativeSharedWorker, {{
        construct: function (target, args, newTarget) {{
          args = [toGatewayUrl(args[0])].concat(args.slice(1));
          return Reflect.construct(target, args, newTarget);
        }}
      }});
    }}

    // 拦截 WebSocket
    if (targetWindow.WebSocket) {{
      var nativeWebSocket = targetWindow.WebSocket;
      targetWindow.WebSocket = new Proxy(nativeWebSocket, {{
        construct: function (target, args, newTarget) {{
          try {{
            var url = new URL(String(args[0]), targetWindow.location.href);
            if ((url.protocol === "ws:" || url.protocol === "wss:") && !isAlreadyPrefixed(url.pathname)) {{
              var rawPath = url.pathname.indexOf('/') === 0 ? url.pathname : '/' + url.pathname;
              url.pathname = prefix + rawPath;
              args = [url.toString()].concat(args.slice(1));
            }}
          }} catch (_) {{}}
          return Reflect.construct(target, args, newTarget);
        }}
      }});
    }}

    // 拦截 EventSource
    if (targetWindow.EventSource) {{
      var nativeEventSource = targetWindow.EventSource;
      targetWindow.EventSource = new Proxy(nativeEventSource, {{
        construct: function (target, args, newTarget) {{
          args = [toGatewayUrl(args[0])].concat(args.slice(1));
          return Reflect.construct(target, args, newTarget);
        }}
      }});
    }}

    // 注入同源 iframe
    var injectIframe = function (el) {{
      try {{
        if (!el || el.__fnHooked) return;
        el.__fnHooked = true;
        var hookWin = function () {{
          try {{
            var win = el.contentWindow;
            if (win && win !== targetWindow) installBridge(win);
          }} catch (_) {{}}
        }};
        el.addEventListener("load", hookWin);
        hookWin();
      }} catch (_) {{}}
    }};

    if (targetWindow.MutationObserver) {{
      var observer = new MutationObserver(function (mutations) {{
        for (var i = 0; i < mutations.length; i++) {{
          var nodes = mutations[i].addedNodes;
          for (var j = 0; j < nodes.length; j++) {{
            if (nodes[j].tagName === "IFRAME") injectIframe(nodes[j]);
          }}
        }}
      }});
      if (targetWindow.document && targetWindow.document.documentElement) {{
        observer.observe(targetWindow.document.documentElement, {{ childList: true, subtree: true }});
      }}
    }}
  }};

  installBridge(window);
}})("{prefix}");
</script>"""


def strip_prefix(path: str, prefix: str) -> str:
    """剥除网关前缀"""
    if prefix and (path == prefix or path.startswith(prefix + "/")):
        path = path[len(prefix):]
        if not path.startswith("/"):
            path = "/" + path
    return path or "/"


class FnShellHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    STATIC_EXTS = (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".webp", ".map")

    def log_message(self, format, *args):
        """输出访问日志"""
        code = str(args[0]) if args else ""
        quiet = self.path.endswith(self.STATIC_EXTS) or "/shell/api/status" in self.path
        if code.startswith(("2", "3")) and quiet:
            return
        logger.info(f"{self.command} {self.path} - {format % args}")

    def do_HEAD(self): self.handle_proxy()
    def do_GET(self): self.handle_proxy()
    def do_POST(self): self.handle_proxy()
    def do_PUT(self): self.handle_proxy()
    def do_DELETE(self): self.handle_proxy()
    def do_PATCH(self): self.handle_proxy()
    def do_OPTIONS(self): self.handle_proxy()

    def handle_proxy(self):
        """统一路由：/shell/api → API；面板静态根；/strata 命名空间 → 剥段反代；其余 → 直传反代"""
        rt = self.server.rt
        req_path = strip_prefix(self.path, self.server.prefix)
        rp = urlparse(req_path)
        path_only = rp.path

        if (rt.panel_root or rt.direct) and (path_only == "/shell" or path_only.startswith("/shell/")):
            rt.handle_api(self, path_only, rp.query)  # 鉴权在 API 内
            return
        if rt.panel_root and rt.resolve_static(path_only):
            rt.serve_static(self, path_only)  # 面板静态公开，敏感数据在 API 后
            return
        if not rt.guard(self):
            return

        if path_only == "/strata" or path_only.startswith("/strata/"):
            backend_path = path_only[len("/strata"):] or "/"  # iframe 命名空间段，本体不认
            if rp.query:
                backend_path += "?" + rp.query
            self._proxy_backend(backend_path)
            return
        self._proxy_backend(req_path)

    def _proxy_backend(self, req_path):
        """HTTP 反向代理处理"""
        prefix = self.server.prefix
        target_host = self.server.target_host
        target_port = self.server.target_port

        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.handle_websocket_tunnel()
            return

        # 读取请求体
        content_len = int(self.headers.get("Content-Length", 0))
        is_chunked_req = self.headers.get("Transfer-Encoding", "").lower() == "chunked"
        body = self.rfile.read(content_len) if content_len > 0 else (self.rfile if is_chunked_req else None)

        # 构建转发请求头
        headers = {}
        auth_val = None
        for k, v in self.headers.items():
            k_lower = k.lower()
            if k_lower in ("host", "origin", "sec-fetch-site", "connection"):
                continue
            if k_lower == "x-fnproxy-authorization":
                auth_val = v
                continue
            if k_lower == "authorization":
                continue
            headers[k] = v

        if auth_val:
            headers["Authorization"] = auth_val
        elif "Authorization" in self.headers:
            headers["Authorization"] = self.headers["Authorization"]

        headers["Host"] = f"{target_host}:{target_port}"
        headers["Origin"] = f"http://{target_host}:{target_port}"
        headers["Sec-Fetch-Site"] = "same-origin"
        headers["Connection"] = "close"

        try:
            conn = http.client.HTTPConnection(target_host, target_port, timeout=30)
            conn.connect()
            if conn.sock:
                conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.request(self.command, req_path, body=body, headers=headers)
            resp = conn.getresponse()

            content_type = ""
            content_encoding = ""
            content_length = -1
            out_headers = []

            for k, v in resp.getheaders():
                k_lower = k.lower()
                if k_lower == "content-type":
                    content_type = v.lower()
                elif k_lower == "content-encoding":
                    content_encoding = v.lower()
                elif k_lower == "content-length":
                    try:
                        content_length = int(v)
                    except ValueError:
                        pass
                elif k_lower == "location":
                    if v.startswith("/") and not v.startswith("//") and not (prefix and (v == prefix or v.startswith(prefix + "/"))):
                        v = prefix + v
                elif k_lower == "set-cookie":
                    if "path=/;" in v.lower() or v.lower().endswith("path=/"):
                        v = re.sub(r'(?i)path=/;', f'Path={prefix}/;', v)
                        if v.lower().endswith("path=/"):
                            v = v[:-7] + f"Path={prefix}/"

                if k_lower not in ("content-length", "transfer-encoding", "connection"):
                    out_headers.append((k, v))

            # HTML 注入改写
            if "text/html" in content_type:
                resp_body = resp.read()
                if "gzip" in content_encoding:
                    try:
                        resp_body = gzip.decompress(resp_body)
                    except Exception:
                        pass

                html_text = resp_body.decode("utf-8", errors="ignore")

                def replace_attr(m):
                    attr, quote, p = m.group(1), m.group(2), m.group(3)
                    return m.group(0) if (p.startswith("//") or (prefix and p.startswith(prefix))) else f"{attr}={quote}{prefix}{p}"

                modified_html = HTML_ATTR_RE.sub(replace_attr, html_text)
                base_tag = f'<base href="{prefix}/">' if prefix else ""
                bridge_code = base_tag + self.server.bridge_code

                head_match = re.search(r"(?i)<head[^>]*>", modified_html)
                if head_match:
                    idx = head_match.end()
                    modified_html = modified_html[:idx] + bridge_code + modified_html[idx:]
                else:
                    modified_html = bridge_code + modified_html

                final_bytes = modified_html.encode("utf-8")
                filtered_headers = [(k, v) for k, v in out_headers if k.lower() not in ("content-encoding", "content-security-policy", "content-security-policy-report-only")]

                resp_lines = [f"HTTP/1.1 {resp.status} {resp.reason}"]
                for k, v in filtered_headers:
                    resp_lines.append(f"{k}: {v}")
                resp_lines.append(f"Content-Length: {len(final_bytes)}")
                resp_lines.append("Connection: close\r\n\r\n")

                self.connection.sendall("\r\n".join(resp_lines).encode("latin1") + final_bytes)
                self.close_connection = True
                conn.close()
                return

            # JS 动态路由适配改写与响应转发
            if prefix and req_path.endswith(".js"):
                resp_body = resp.read()
                is_gzip = "gzip" in content_encoding
                if is_gzip:
                    try:
                        resp_body = gzip.decompress(resp_body)
                        content_encoding = ""
                    except Exception:
                        pass

                def patch_route(m):
                    fn = m.group(1).decode("utf-8")
                    arg = m.group(2).decode("utf-8")
                    route = m.group(3).decode("utf-8")
                    val = m.group(4).decode("utf-8")
                    return (
                        f'function {fn}({arg}){{const p="{prefix}";const s={arg}.startsWith(p)?'
                        f'{arg}.slice(p.length)||"/":{arg};return/^\\/{route}(?:\\/|$)/.test(s)?'
                        f'({arg}.startsWith(p)?p+{val}:{val}):({arg}.startsWith(p)?p:void 0)}}'
                    ).encode("utf-8")

                if not content_encoding and JS_ROUTE_RE.search(resp_body):
                    resp_body = JS_ROUTE_RE.sub(patch_route, resp_body)

                filtered_headers = []
                for k, v in out_headers:
                    if not content_encoding and k.lower() == "content-encoding":
                        continue
                    filtered_headers.append((k, v))

                resp_lines = [f"HTTP/1.1 {resp.status} {resp.reason}"]
                for k, v in filtered_headers:
                    resp_lines.append(f"{k}: {v}")
                resp_lines.append(f"Content-Length: {len(resp_body)}")
                resp_lines.append("Connection: close\r\n\r\n")

                self.connection.sendall("\r\n".join(resp_lines).encode("latin1") + resp_body)
                self.close_connection = True
                conn.close()
                return

            # 通用流式直通（API / 静态资源 / SSE / 音视频流）
            resp_lines = [f"HTTP/1.1 {resp.status} {resp.reason}"]
            for k, v in out_headers:
                resp_lines.append(f"{k}: {v}")
            if content_length >= 0:
                resp_lines.append(f"Content-Length: {content_length}")
            resp_lines.append("Connection: close\r\n\r\n")

            self.connection.sendall("\r\n".join(resp_lines).encode("latin1"))
            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                self.connection.sendall(chunk)

            self.close_connection = True
            conn.close()

        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:
            logger.error(f"代理请求失败 [{self.command} {self.path}]: {e}")
            try:
                self.send_error(http.client.BAD_GATEWAY, f"Bad Gateway: {str(e)}")
            except Exception:
                pass
            self.close_connection = True

    def handle_websocket_tunnel(self):
        """WebSocket 全双工隧道"""
        prefix = self.server.prefix
        target_host = self.server.target_host
        target_port = self.server.target_port

        req_path = strip_prefix(self.path, prefix)
        rp = urlparse(req_path)
        if self.server.rt.panel_root and (rp.path == "/strata" or rp.path.startswith("/strata/")):
            ns = rp.path[len("/strata"):] or "/"  # iframe 命名空间段剥离
            req_path = ns + (("?" + rp.query) if rp.query else "")

        target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target_sock.connect((target_host, target_port))

        handshake = [f"{self.command} {req_path} HTTP/1.1"]
        auth_val = None
        for k, v in self.headers.items():
            k_lower = k.lower()
            if k_lower == "host":
                handshake.append(f"Host: {target_host}:{target_port}")
            elif k_lower == "origin":
                handshake.append(f"Origin: http://{target_host}:{target_port}")
            elif k_lower == "x-fnproxy-authorization":
                auth_val = v
            elif k_lower == "authorization":
                continue
            else:
                handshake.append(f"{k}: {v}")

        if auth_val:
            handshake.append(f"Authorization: {auth_val}")
        elif "Authorization" in self.headers:
            handshake.append(f"Authorization: {self.headers['Authorization']}")

        handshake.append("\r\n")
        target_sock.sendall("\r\n".join(handshake).encode("utf-8"))

        client_sock = self.connection
        sockets = [client_sock, target_sock]
        logger.info(f"WebSocket 隧道建立: {req_path}")
        try:
            while True:
                r_list, _, x_list = select.select(sockets, [], sockets, 60)
                if x_list:
                    break
                for s in r_list:
                    data = s.recv(CHUNK_SIZE)
                    if not data:
                        return
                    if s is client_sock:
                        target_sock.sendall(data)
                    else:
                        client_sock.sendall(data)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True
            target_sock.close()
            logger.info(f"WebSocket 隧道关闭: {req_path}")


# ============================================================================
# 壳运行时：面板静态站 + 管理 API + 鉴权（UDS/TCP 双监听共享）
# ============================================================================

PANEL_PREFIX = ("/assets/", "/icons/", "/fonts/")
COOKIE_NAME = "qwp_shell"
SESSION_TTL = 30 * 24 * 3600
DEFAULT_TRUSTED = ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7"]
MIME_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2",
    ".woff": "font/woff", ".txt": "text/plain; charset=utf-8",
}


class ShellRuntime:
    """跨双监听共享：路由/鉴权/状态/日志/服务控制"""

    LOG_FILES = {"app": "info.log", "shell": "gateway.log"}
    SERVICE_ACTIONS = {"start": "start", "stop": "stop", "restart": "restart", "status": "status",
                       "start-backend": "start-backend", "stop-backend": "stop-backend",
                       "restart-backend": "restart-backend"}

    def __init__(self, panel_root=None, cmd_dir=None, state_dir=None, public_port=None, default_backend=None,
                 main_url=None, version_file=None, github_repo=None, direct=False, gateway_prefix=None):
        self.direct = direct  # 直通模式：根路径反代本体（图标/端口直达本体），不 serve 面板 UI，API 层保留
        self.gateway_prefix = gateway_prefix or None
        self.panel_root = panel_root
        self.cmd_dir = cmd_dir
        self.state_dir = state_dir
        self.public_port = public_port
        self.default_backend = default_backend
        self.sessions = {}
        self.lock = threading.Lock()
        # 面板模式（双 app）：main_url 非空时状态聚合/本体日志/动作转发到本体壳；main_url 空=本体自身行为
        self.main_url = (main_url or "").rstrip("/") or None
        self.version_file = version_file
        self.github_repo = github_repo
        self._main_cache = (0.0, None)   # (ts, json)
        self._update_cache = (0.0, None)
        # 飞牛官方技能授权（trim-cli）会话态
        self.skill_lock = threading.Lock()
        self.skill_proc = None
        self.skill_stdin = None
        self.skill_timer = None

    # ---------- 面板模式：对本体壳的取数/转发 ----------
    def _http_json(self, url, method="GET", body=None, timeout=5):
        u = urlparse(url)
        try:
            if u.scheme == "https":
                conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout)
            else:
                conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
            headers = {"Accept": "application/json", "User-Agent": "strata-fnos-shell"}
            tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PAT_PUSH") or os.environ.get("DSH_SECRET_GITHUB_PAT_PUSH")
            if u.hostname == "api.github.com" and tok:
                headers["Authorization"] = "Bearer " + tok
            path = u.path + (("?" + u.query) if u.query else "")
            data = json.dumps(body).encode("utf-8") if body is not None else None
            if data is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, data, headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", "replace")
            conn.close()
            if resp.status >= 400:
                return None
            return json.loads(raw)
        except Exception:
            return None

    def main_status_cached(self, maxage=5.0):
        if not self.main_url:
            return None
        ts, val = self._main_cache
        if time.time() - ts > maxage:
            val = self._http_json(self.main_url + "/shell/api/status", timeout=3)
            self._main_cache = (time.time(), val)
        return val

    def status_merged(self, handler):
        """面板模式 status：本体数据为主，面板本地 shell/auth 覆盖（顶部胶囊语义不变）。"""
        local = self.status_json(handler)
        main = self.main_status_cached()
        if not main:
            return {**local, "main": {"url": self.main_url, "reachable": False}}
        merged = dict(main)
        merged["shell"] = local["shell"]           # 顶部 pillShell = 面板自身进程
        merged["auth"] = local["auth"]             # 登录门禁 = 面板自身
        merged["main"] = {"url": self.main_url, "reachable": True}
        return merged

    def check_update(self, handler):
        if self.main_url:  # 面板模式：查的是本体的更新（本体 VERSION 才准）
            self.forward_main_json(handler, "check-update")
            return
        if not self.github_repo:
            self.json_out(handler, 200, {"ok": False, "error": "no_repo_configured"})
            return
        ts, cached = self._update_cache
        if cached and time.time() - ts < 60:
            self.json_out(handler, 200, cached)
            return
        cur = os.environ.get("TRIM_APPVER")  # fnOS 注入（生产主路径）；测试/开发回退 VERSION 文件
        if not cur:
            try:
                with open(self.version_file or "VERSION") as f:
                    cur = f.read().strip()
            except Exception:
                pass
        latest = self._http_json("https://api.github.com/repos/%s/releases/latest" % self.github_repo, timeout=8)
        if latest is None:
            self.json_out(handler, 200, {"ok": False, "error": "github_unreachable", "current": cur})
            return
        latest_tag = str(latest.get("tag_name") or "").lstrip("v")
        out = {"ok": True, "current": cur,
               "latest": latest_tag or None,
               "has_update": bool(latest_tag) and bool(cur) and latest_tag != cur,
               "url": latest.get("html_url"),
               "published_at": latest.get("published_at"),
               "notes": (latest.get("body") or "")[:600]}
        self._update_cache = (time.time(), out)
        self.json_out(handler, 200, out)

    def forward_main_json(self, handler, suffix, body=None):
        """面板模式 JSON 转发（service POST 体透传）。"""
        if body is None and handler.command == "POST":
            body = self.read_json(handler)
        url = self.main_url + "/shell/api/" + suffix
        r = self._http_json(url, method=handler.command, body=body)
        if r is None:
            self.json_out(handler, 502, {"ok": False, "error": "main_unreachable", "main": self.main_url})
        else:
            self.json_out(handler, 200, r)

    def relay_main_logs(self, handler, query):
        """SSE 中继：从本体壳 logs 端点逐行转发。"""
        try:
            u = urlparse(self.main_url + "/shell/api/logs")
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=120)
            conn.request("GET", u.path + (("?" + query) if query else ""), headers={"Accept": "text/event-stream"})
            resp = conn.getresponse()
            handler.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in ("content-type", "cache-control"):
                    handler.send_header(k, v)
            handler.send_header("Connection", "close")
            handler.end_headers()
            while True:
                line = resp.readline()
                if not line:
                    break
                handler.wfile.write(line)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as e:
            logger.error(f"日志中继失败: {e}")
        finally:
            self.close_connection = True
            try:
                conn.close()
            except Exception:
                pass

    # ---------- 配置与鉴权 ----------
    def config_path(self):
        return os.path.join(self.state_dir or ".", "shell_config.json")

    def load_config(self):
        try:
            with open(self.config_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def password_hash(self):
        return self.load_config().get("access_password") or None

    def trusted_networks(self):
        return self.load_config().get("trusted_networks") or DEFAULT_TRUSTED

    def client_ip(self, handler):
        peer = getattr(handler, "client_address", None)
        if isinstance(peer, tuple) and peer and isinstance(peer[0], str) and peer[0]:
            return peer[0]
        return None  # UDS peer = 本机网关，视为可信

    def is_trusted(self, handler):
        ip = self.client_ip(handler)
        if ip is None:
            return True
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for cidr in self.trusted_networks():
            try:
                if addr in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def session_ok(self, handler):
        for part in handler.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                with self.lock:
                    exp = self.sessions.get(v)
                    return bool(exp and exp > time.time())
        return False

    def guard(self, handler):
        if not self.password_hash():
            return True
        if self.is_trusted(handler):
            return True
        return self.session_ok(handler)

    # ---------- 静态站 ----------
    PANEL_INDEX_ROUTES = {"", "/", "/index.html", "/login", "/panel", "/panel/", "/panel/index.html"}

    def resolve_static(self, path):
        """面板路由：命中面板文件才拦（显式 index 路由 + 面板目录真实文件），其余透传后端（app 自己的 /assets 等经 bridge 重写会落到这里）"""
        if path in self.PANEL_INDEX_ROUTES:
            return "INDEX"
        if path.startswith(PANEL_PREFIX) or path in ("/favicon.ico", "/favicon.svg", "/manifest.webmanifest"):
            fpath = os.path.realpath(os.path.join(self.panel_root, path[1:]))
            root = os.path.realpath(self.panel_root)
            if fpath.startswith(root + os.sep) and os.path.isfile(fpath):
                return fpath
        return None

    def serve_static(self, handler, path):
        if handler.command not in ("GET", "HEAD"):
            handler.send_error(405)
            return
        fpath = self.resolve_static(path)
        if not fpath:
            handler.send_error(404)
            return
        cache = "no-cache"
        if fpath == "INDEX":
            fpath = os.path.join(self.panel_root, "index.html")
        elif path.startswith("/assets/"):
            cache = "public, max-age=31536000, immutable"
        else:
            cache = "public, max-age=300"
        try:
            with open(fpath, "rb") as f:
                data = f.read()
        except Exception:
            handler.send_error(500)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", MIME_TYPES.get(os.path.splitext(fpath)[1].lower(), "application/octet-stream"))
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", cache)
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(data)

    # ---------- 工具 ----------
    def json_out(self, handler, code, obj, headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        for k, v in (headers or []):
            handler.send_header(k, v)
        handler.end_headers()
        handler.wfile.write(data)

    def read_json(self, handler):
        try:
            n = int(handler.headers.get("Content-Length", 0))
            return json.loads(handler.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return None

    def require_auth(self, handler):
        if not self.guard(handler):
            self.json_out(handler, 401, {"ok": False, "error": "unauthorized"})
            return False
        return True

    # ---------- API 入口 ----------
    def handle_api(self, handler, path, query):
        route = path[len("/shell/api"):].lstrip("/")
        if route == "status":  # 轻端点，面板登录前即需
            self.json_out(handler, 200, self.status_merged(handler) if self.main_url else self.status_json(handler))
        elif route == "check-update":
            if self.require_auth(handler):
                self.check_update(handler)
        elif route == "logs":
            if self.require_auth(handler):
                src = (parse_qs(query).get("src") or ["app"])[0]
                if self.main_url and src == "app":
                    self.relay_main_logs(handler, query)  # 本体日志 = 本体的 state 日志，面板读不到
                else:
                    self.sse_logs(handler, query)
        elif route == "service":
            if self.require_auth(handler):
                body = None
                if self.main_url:
                    body = self.read_json(handler)
                    if not str((body or {}).get("action", "")).endswith("-panel"):
                        self.forward_main_json(handler, "service", body)  # 本体动作转发；-panel 动作本地执行
                        return
                self.exec_service(handler, body)
        elif route == "config":
            if not self.require_auth(handler):
                return
            if handler.command == "POST":
                self.post_config(handler)
            else:
                cfg = self.load_config()
                self.json_out(handler, 200, {"ok": True, "config": {
                    "access_password_set": bool(cfg.get("access_password")),
                    "trusted_networks": cfg.get("trusted_networks") or DEFAULT_TRUSTED}})
        elif route in ("skill-auth", "skill-auth/start", "skill-auth/confirm", "skill-auth/logout"):
            if self.require_auth(handler):
                if route == "skill-auth":
                    self.json_out(handler, 200, dict(ok=True, **self.skill_auth_status()))
                elif handler.command == "POST" and route == "skill-auth/start":
                    self.skill_auth_start(handler)
                elif handler.command == "POST" and route == "skill-auth/confirm":
                    self.skill_auth_confirm(handler)
                elif handler.command == "POST" and route == "skill-auth/logout":
                    self.skill_auth_logout(handler)
                else:
                    handler.send_error(405)
        elif route == "login":
            self.post_login(handler)  # 登录本身永远开放
        elif route == "logout":
            if self.require_auth(handler):
                body = self.read_json(handler) or {}
                with self.lock:
                    self.sessions.pop(str(body.get("token", "")), None)
                self.json_out(handler, 200, {"ok": True})
        else:
            handler.send_error(404)

    # ---------- 状态 ----------
    def pid_alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def read_pidfile(self, name):
        try:
            with open(os.path.join(self.state_dir or ".", name)) as f:
                return int(f.read().strip())
        except Exception:
            return None

    def proc_uptime(self, pid):
        try:
            with open(f"/proc/{pid}/stat") as f:
                starttime = int(f.read().rsplit(")", 1)[1].split()[19])
            with open("/proc/uptime") as f:
                boot = float(f.read().split()[0])
            return int(boot - starttime / 100)
        except Exception:
            return None

    def proc_cpu_mem(self, pid):
        # (cpu_pct, mem_kb) — cpu = utime+stime 平均值相对运行时长；mem = RSS
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            ticks = int(fields[11]) + int(fields[12])  # utime+stime, clock ticks
            up = self.proc_uptime(pid)
            cpu = round(ticks / (up * os.sysconf("SC_CLK_TCK")) * 100, 1) if up else None
            mem = None
            with open(f"/proc/{pid}/statm") as f:
                mem = int(f.read().split()[1]) * 4  # resident pages -> KB
            return cpu, mem
        except Exception:
            return None, None

    def dir_stats(self, path):
        files = size = 0
        try:
            for root, _dirs, fnames in os.walk(path):
                for fn in fnames:
                    files += 1
                    try:
                        size += os.path.getsize(os.path.join(root, fn))
                    except Exception:
                        pass
                    if files >= 20000:
                        return files, size, True
        except Exception:
            return None, None, False
        return files, size, False

    def venv_python_version(self):
        try:
            with open(os.path.join(self.state_dir or ".", "venv", "pyvenv.cfg")) as f:
                for line in f:
                    if line.strip().startswith("version"):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass
        return None

    def strata_stats(self):
        out = {"channels": None, "skills": None}
        base = os.path.join(os.environ.get("HOME", ""), "Documents", "Strata")
        try:
            with open(os.path.join(base, "config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            ch = cfg.get("channels")
            if isinstance(ch, (dict, list)):
                out["channels"] = len(ch)
        except Exception:
            pass
        try:
            out["skills"] = sum(1 for _ in os.scandir(os.path.join(base, "skill_pool")))
        except Exception:
            pass
        return out

    def log_size(self, name):
        try:
            return os.path.getsize(os.path.join(self.state_dir or ".", name))
        except Exception:
            return None

    def status_json(self, handler):
        backend_pid = self.read_pidfile("app.pid")
        backend_up = bool(backend_pid) and self.pid_alive(backend_pid)
        shell_pid = os.getpid()
        data_dir = os.environ.get("HOME") or ""
        df, db, dcapped = self.dir_stats(data_dir) if data_dir else (None, None, False)
        pw_hash = self.password_hash()
        bcpu, bmem = self.proc_cpu_mem(backend_pid) if backend_up else (None, None)
        return {
            "ok": True,
            "shell": {"pid": shell_pid, "uptime_s": self.proc_uptime(shell_pid)},
            "backend": {"running": backend_up, "pid": backend_pid,
                        "uptime_s": self.proc_uptime(backend_pid) if backend_up else None,
                        "cpu_pct": bcpu, "mem_kb": bmem},
            "python": {"venv": self.venv_python_version()},
            "app": {"version": os.environ.get("TRIM_APPVER"),
                    "port_public": self.public_port,
                    "app_url": self.main_url or (self.gateway_prefix or None),
                    "backend": "%s:%d" % self.default_backend if self.default_backend else None,
                    "data_capped": dcapped},
            "data": {"path_basename": os.path.basename(data_dir) if data_dir else None,
                     "files": df, "bytes": db},
            "strata": self.strata_stats(),
            "logs": {"app_bytes": self.log_size("info.log"), "shell_bytes": self.log_size("gateway.log")},
            "auth": {"required": bool(pw_hash),
                     "authorized": (not pw_hash) or self.is_trusted(handler) or self.session_ok(handler),
                     "trusted": self.is_trusted(handler)},
        }

    # ---------- 日志 SSE ----------
    def sse_logs(self, handler, query):
        src = (parse_qs(query).get("src") or ["app"])[0]
        fname = self.LOG_FILES.get(src)
        if not fname:
            handler.send_error(404)
            return
        fpath = os.path.join(self.state_dir or ".", fname)
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()

        def emit(line):
            payload = json.dumps({"t": time.strftime("%H:%M:%S"), "source": src, "line": line}, ensure_ascii=False)
            handler.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))

        try:
            pos = 0
            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()[-200:]
                    pos = os.path.getsize(fpath)
                for ln in lines:
                    emit(ln)
            beat = 0
            while True:
                time.sleep(1)
                if os.path.exists(fpath):
                    size = os.path.getsize(fpath)
                    if size > pos:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(pos)
                            new = f.read()
                            pos = f.tell()
                        for ln in new.splitlines():
                            if ln:
                                emit(ln)
                    elif size < pos:
                        pos = size  # 日志轮转截断
                    beat += 1
                    if beat >= 15:
                        beat = 0
                        handler.wfile.write(b": hb\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ---------- 服务控制 ----------
    def exec_service(self, handler, body=None):
        body = body if body is not None else self.read_json(handler) or {}
        action = self.SERVICE_ACTIONS.get(str(body.get("action", "")))
        if not action or not self.cmd_dir or not os.path.isfile(os.path.join(self.cmd_dir, "main")):
            self.json_out(handler, 400, {"ok": False, "error": "bad_action_or_no_cmd"})
            return
        try:
            proc = subprocess.run([os.path.join(self.cmd_dir, "main"), action],
                                  capture_output=True, text=True, timeout=120)
            self.json_out(handler, 200, {"ok": True, "action": action, "rc": proc.returncode,
                                         "out": (proc.stdout or "").strip()[-4000:],
                                         "err": (proc.stderr or "").strip()[-2000:]})
        except Exception as e:
            self.json_out(handler, 500, {"ok": False, "error": str(e)})

    # ---------- 飞牛官方技能授权（trim-cli） ----------
    def trim_cli_path(self):
        for cand in (os.environ.get("TRIM_CLI_PATH"),
                     shutil.which("trim-cli"),
                     os.path.join(os.environ.get("HOME") or "", "skills", "fnos", "scripts", "trim-cli")):
            if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return None

    def trim_cli_env(self):
        env = dict(os.environ)
        home = os.environ.get("HOME") or ""
        env["HOME"] = home
        env["TRIM_CLI_CONFIG_DIR"] = os.path.join(home, ".config", "trim-cli")
        return env

    def skill_auth_status(self):
        cli = self.trim_cli_path()
        if not cli:
            return {"authorized": False, "no_cli": True}
        active = os.path.join(os.environ.get("HOME") or "", ".config", "trim-cli", "secure", "active.enc")
        if not os.path.exists(active):
            return {"authorized": False, "username": None}
        try:
            out = subprocess.run([cli, "user", "info"], env=self.trim_cli_env(),
                                 capture_output=True, text=True, timeout=10)
            u = json.loads(out.stdout) if out.returncode == 0 else {}
            return {"authorized": True, "username": u.get("username")}
        except Exception:
            return {"authorized": False, "username": None}

    def _skill_stop_locked(self):
        if self.skill_timer:
            self.skill_timer.cancel()
            self.skill_timer = None
        if self.skill_stdin:
            try:
                self.skill_stdin.close()
            except Exception:
                pass
            self.skill_stdin = None
        if self.skill_proc and self.skill_proc.poll() is None:
            try:
                self.skill_proc.kill()
            except Exception:
                pass
        self.skill_proc = None

    def skill_auth_start(self, handler):
        cli = self.trim_cli_path()
        if not cli:
            self.json_out(handler, 200, {"ok": False, "error": "no_cli", "url": None})
            return
        with self.skill_lock:
            self._skill_stop_locked()
        script = shutil.which("script")
        cmd = [script, "-qec", cli + " login --no-open", "/dev/null"] if script else [cli, "login", "--no-open"]
        try:
            p = subprocess.Popen(cmd, env=self.trim_cli_env(), stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        except Exception as e:
            self.json_out(handler, 500, {"ok": False, "error": str(e), "url": None})
            return
        found = queue.Queue()

        def scan():
            try:
                for line in p.stdout:
                    if "/signin?" in line:
                        hit = next((f for f in line.split() if "/signin?" in f), line.strip())
                        i = hit.find("/signin?")
                        found.put(hit[i:] if i != -1 else hit)
                        break
            except Exception:
                pass

        threading.Thread(target=scan, daemon=True).start()
        try:
            url = found.get(timeout=8)
        except queue.Empty:
            with self.skill_lock:
                self._skill_stop_locked()
            self.json_out(handler, 504, {"ok": False, "error": "wait_url_timeout", "url": None})
            return
        with self.skill_lock:
            self.skill_proc, self.skill_stdin = p, p.stdin
            self.skill_timer = threading.Timer(300, self._skill_stop_locked)
            self.skill_timer.daemon = True
            self.skill_timer.start()
        self.json_out(handler, 200, {"ok": True, "url": url})

    def skill_auth_confirm(self, handler):
        code = str((self.read_json(handler) or {}).get("code") or "").strip()
        if not code:
            self.json_out(handler, 400, {"ok": False, "error": "code_required"})
            return
        with self.skill_lock:
            p, stdin = self.skill_proc, self.skill_stdin
            if not p or not stdin:
                self.json_out(handler, 400, {"ok": False, "error": "no_pending_session"})
                return
            try:
                stdin.write(code + "\n")
                stdin.flush()
            except Exception as e:
                self._skill_stop_locked()
                self.json_out(handler, 500, {"ok": False, "error": str(e)})
                return
            try:
                stdin.close()
            except Exception:
                pass
            self.skill_stdin = None
        try:
            rc = p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._skill_stop_locked()
            self.json_out(handler, 504, {"ok": False, "error": "verify_timeout"})
            return
        with self.skill_lock:
            if self.skill_proc is p:
                self._skill_stop_locked()
        if rc != 0:
            self.json_out(handler, 400, {"ok": False, "error": "verify_failed"})
            return
        self.json_out(handler, 200, dict(ok=True, **self.skill_auth_status()))

    def skill_auth_logout(self, handler):
        cli = self.trim_cli_path()
        if cli:
            try:
                subprocess.run([cli, "logout"], env=self.trim_cli_env(), capture_output=True, timeout=15)
            except Exception:
                pass
        cfgdir = os.path.join(os.environ.get("HOME") or "", ".config", "trim-cli")
        for victim in (os.path.join(cfgdir, "secure"), os.path.join(cfgdir, "session.json")):
            try:
                if os.path.isdir(victim):
                    shutil.rmtree(victim)
                elif os.path.isfile(victim):
                    os.remove(victim)
            except Exception:
                pass
        self.json_out(handler, 200, {"ok": True, "authorized": False, "username": None})

    # ---------- 配置与登录 ----------
    def post_config(self, handler):
        body = self.read_json(handler)
        if body is None:
            self.json_out(handler, 400, {"ok": False, "error": "bad_json"})
            return
        cfg = self.load_config()
        if "access_password" in body:
            pw = str(body.get("access_password") or "")
            cfg["access_password"] = hashlib.sha256(pw.encode("utf-8")).hexdigest() if pw else ""
        if "trusted_networks" in body:
            tn = body.get("trusted_networks")
            tn = [str(x) for x in tn] if isinstance(tn, list) else None
            if tn:
                cfg["trusted_networks"] = tn
            else:
                cfg.pop("trusted_networks", None)
        try:
            with open(self.config_path(), "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(self.config_path(), 0o600)
            except Exception:
                pass
            self.json_out(handler, 200, {"ok": True, "access_password_set": bool(cfg.get("access_password"))})
        except Exception as e:
            self.json_out(handler, 500, {"ok": False, "error": str(e)})

    def post_login(self, handler):
        if handler.command != "POST":
            handler.send_error(405)
            return
        body = self.read_json(handler) or {}
        pw_hash = self.password_hash()
        if not pw_hash:
            self.json_out(handler, 200, {"ok": True, "no_password": True})
            return
        if hashlib.sha256(str(body.get("password", "")).encode("utf-8")).hexdigest() != pw_hash:
            self.json_out(handler, 401, {"ok": False, "error": "bad_password"})
            return
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.sessions[token] = time.time() + SESSION_TTL
        self.json_out(handler, 200, {"ok": True, "token": token},
                      headers=[("Set-Cookie", COOKIE_NAME + "=" + token + "; Path=/; HttpOnly; SameSite=Lax; Max-Age=" + str(SESSION_TTL))])


if hasattr(socketserver, "UnixStreamServer"):
    BaseUnixServer = socketserver.UnixStreamServer
else:
    class BaseUnixServer(socketserver.TCPServer):
        address_family = getattr(socket, "AF_UNIX", socket.AF_INET)

        def server_bind(self):
            self.socket.bind(self.server_address)
            self.server_address = self.socket.getsockname()


class ThreadingUnixHTTPServer(socketserver.ThreadingMixIn, BaseUnixServer):
    """Unix Domain Socket HTTP 服务"""
    request_queue_size = 128
    daemon_threads = True

    def __init__(self, socket_path: str, target_host: str, target_port: int, prefix: str):
        self.socket_path = socket_path
        self.target_host = target_host
        self.target_port = target_port
        self.prefix = prefix.rstrip("/")
        self.bridge_code = generate_bridge_script(self.prefix)

        if os.path.exists(socket_path):
            try:
                os.unlink(socket_path)
            except Exception:
                pass

        super().__init__(socket_path, FnGatewayHandler)

        try:
            os.chmod(socket_path, 0o666)
        except Exception:
            pass


class ShellTCPServer(socketserver.ThreadingMixIn, HTTPServer):
    """对外 TCP 监听（属性由 main 挂载：rt/target_host/target_port/prefix/bridge_code）"""
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_address = True


FnGatewayHandler = FnShellHandler  # 类改名后的向后别名（UDS 服务器构造引用）


def parse_listen_address(addr: str) -> tuple[str, int]:
    """解析监听地址为 host 与 port"""
    clean_addr = addr.replace("http://", "").replace("https://", "").strip().rstrip("/")
    if ":" in clean_addr:
        parts = clean_addr.split(":")
        host = parts[0] if parts[0] else "127.0.0.1"
        port = int(parts[1])
        return host, port
    return "127.0.0.1", int(clean_addr)


def main():
    parser = argparse.ArgumentParser(description="Strata 单端口壳（fngateway 扩展）")
    parser.add_argument("--listen", type=str, default=None, help="目标后端地址 (格式: 127.0.0.1:2298 或 2298)")
    parser.add_argument("--backend", type=str, default=None, help="目标后端地址（--listen 别名）")
    parser.add_argument("--socket", type=str, default=None, help="Unix Domain Socket 监听路径")
    parser.add_argument("--prefix", type=str, default="", help="飞牛网关反向代理路由前缀")
    parser.add_argument("--tcp", type=int, default=None, help="对外 TCP 端口")
    parser.add_argument("--tcp-bind", type=str, default="0.0.0.0", help="TCP 绑定地址")
    parser.add_argument("--panel", type=str, default=None, help="面板静态站目录")
    parser.add_argument("--cmd-dir", type=str, default=None, help="服务控制 cmd 脚本目录")
    parser.add_argument("--state", type=str, default=None, help="状态目录（pidfile/日志/配置），默认 TRIM_PKGVAR")
    parser.add_argument("--main-url", type=str, default=None, help="面板模式：本体壳地址 http://127.0.0.1:PORT（状态聚合/日志中继/动作转发）")
    parser.add_argument("--version-file", type=str, default=None, help="检查更新用的 VERSION 文件路径")
    parser.add_argument("--github-repo", type=str, default=None, help="检查更新的 GitHub 仓库 owner/name")
    parser.add_argument("--direct", action="store_true", help="直通模式：根路径反代本体（图标/端口直达），不 serve 面板 UI，API 层保留")
    args = parser.parse_args()

    backend = args.backend or args.listen
    if not backend:
        parser.error("必须提供 --listen/--backend 之一")
    if not args.socket and not args.tcp:
        parser.error("必须提供 --socket/--tcp 之一")

    target_host, target_port = parse_listen_address(backend)
    state_dir = args.state or os.environ.get("TRIM_PKGVAR") or None
    rt = ShellRuntime(panel_root=args.panel, cmd_dir=args.cmd_dir, state_dir=state_dir,
                      public_port=args.tcp, default_backend=(target_host, target_port),
                      main_url=args.main_url, version_file=args.version_file, github_repo=args.github_repo,
                      direct=args.direct, gateway_prefix=args.prefix)

    def cleanup():
        if args.socket and os.path.exists(args.socket):
            try:
                os.unlink(args.socket)
            except Exception:
                pass

    def sig_handler(*_):
        sys.exit(0)

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    servers = []
    if args.socket:
        uds = ThreadingUnixHTTPServer(args.socket, target_host, target_port, args.prefix)
        uds.rt = rt
        servers.append((f"Unix套接字 [{args.socket}]", uds))
    if args.tcp:
        tcp = ShellTCPServer((args.tcp_bind, args.tcp), FnShellHandler)
        tcp.rt = rt
        tcp.target_host, tcp.target_port = target_host, target_port
        tcp.prefix = ""
        tcp.bridge_code = generate_bridge_script("")
        servers.append((f"TCP [{args.tcp_bind}:{args.tcp}]", tcp))

    logger.info(f"壳已就绪: {' + '.join(d for d, _ in servers)} -> 后端 [{target_host}:{target_port}]"
                + (f" (网关前缀: {args.prefix})" if args.prefix else "")
                + (f" 面板 [{args.panel}]" if args.panel else ""))

    for desc, srv in servers[1:]:
        t = threading.Thread(target=srv.serve_forever, name="srv-" + desc, daemon=True)
        t.start()
    try:
        servers[0][1].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()
        logger.info("服务已停止并清理套接字")


if __name__ == "__main__":
    main()
