# -*- coding: utf-8 -*-
"""VRM 三维预览：本地起一个只监听 127.0.0.1 的小服务器，用 three-vrm 页面（gfx/web/）显示模型。

    python gfx/web_preview.py [model.vrm] [--browser | --no-open] [--port 0]

* 装了 pywebview 时开一个独立窗口（Windows 用系统自带的 WebView2）；没装或加 --browser
  时用系统默认浏览器打开，直到 Ctrl+C / 关闭窗口才退出。
* 页面用的 three.js / three-vrm 都在 gfx/web/vendor/ 里，离线可用。和 tools/qa 的动态测试是
  同一套 SpringBone / MToon 实现，看到的摆动和测出来的一致。
* 服务器只提供页面文件、启动时指定的那一个模型（/model.vrm），以及把截图存到模型旁边。
"""
import argparse
import base64
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# PyInstaller 打包后资源在 sys._MEIPASS/gfx/web（需要在 main.spec 的 datas 里带上）
if getattr(sys, "frozen", False):
    WEB = os.path.join(sys._MEIPASS, "gfx", "web")
else:
    WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class _Handler(SimpleHTTPRequestHandler):
    model = None                       # 由 serve() 设置：当前模型的绝对路径
    last_seen = [time.time()]          # 页面最后一次访问的时间（浏览器模式靠它判断页面关了没有）

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def log_message(self, fmt, *args):  # 不往控制台刷访问日志
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def guess_type(self, path):
        if path.endswith((".js", ".mjs")):
            return "text/javascript"   # 某些 Windows 注册表把 .js 映射成 text/plain，模块会拒绝加载
        return super().guess_type(path)

    def do_GET(self):
        self.last_seen[0] = time.time()
        if urllib.parse.urlsplit(self.path).path == "/api/ping":
            self.send_response(204)
            self.end_headers()
            return
        if urllib.parse.urlsplit(self.path).path == "/model.vrm":
            m = self.model
            if not m or not os.path.isfile(m):
                self.send_error(404, "no model")
                return
            size = os.path.getsize(m)
            self.send_response(200)
            self.send_header("Content-Type", "model/gltf-binary")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(m, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        super().do_GET()

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path != "/api/snapshot":
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("ascii", "replace")
        if not body.startswith("data:image/png;base64,"):
            self.send_error(400)
            return
        name = urllib.parse.parse_qs(u.query).get("name", ["vrm"])[0]
        name = "".join(c for c in name if c not in '\\/:*?"<>|') or "vrm"
        folder = os.path.dirname(self.model) if self.model else os.path.expanduser("~")
        n = 1
        while True:
            out = os.path.join(folder, "%s_preview_%02d.png" % (name, n))
            if not os.path.exists(out):
                break
            n += 1
        with open(out, "wb") as f:
            f.write(base64.b64decode(body.split(",", 1)[1]))
        data = out.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(model=None, port=0):
    """启动服务器（后台线程），返回 (server, 页面 URL)。"""
    handler = type("Handler", (_Handler,), {"model": os.path.abspath(model) if model else None,
                                            "last_seen": [time.time()]})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % srv.server_address[1]
    if model:
        url += "?model=/model.vrm&name=" + urllib.parse.quote(os.path.basename(model))
    return srv, url


def main(argv=None):
    ap = argparse.ArgumentParser(description="VRM 三维预览（three-vrm）")
    ap.add_argument("model", nargs="?", help=".vrm 文件（也可以打开后拖进窗口）")
    ap.add_argument("--browser", action="store_true", help="用系统浏览器，不用 pywebview 窗口")
    ap.add_argument("--no-open", action="store_true", help="只启动服务器、打印地址，不开窗口")
    ap.add_argument("--port", type=int, default=0, help="端口，默认随机")
    a = ap.parse_args(argv)
    if a.model and not os.path.isfile(a.model):
        sys.exit("找不到文件：%s" % a.model)
    srv, url = serve(a.model, a.port)
    last_seen = srv.RequestHandlerClass.last_seen
    title = "VRM 预览" + (" - " + os.path.basename(a.model) if a.model else "")
    webview = None
    if not (a.browser or a.no_open):
        try:
            import webview  # pywebview
        except ImportError:
            webview = None
    if webview is not None:
        webview.create_window(title, url, width=1360, height=860, min_size=(800, 520))
        webview.start()
    else:
        print("预览地址：%s（Ctrl+C 退出）" % url, flush=True)
        if not a.no_open:
            webbrowser.open(url)
        # 页面每 10 秒 ping 一次；浏览器标签关掉 1 分钟后自动退出（从 GUI 启动时没有控制台可按 Ctrl+C）
        try:
            while a.no_open or time.time() - last_seen[0] < 60:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    srv.shutdown()


if __name__ == "__main__":
    main()
