# -*- coding: utf-8 -*-
"""Model-To-VRM 图形界面入口。

    python ui/app.py [文件或文件夹 ...] [--browser] [--port N]

装了 pywebview 时开一个原生窗口（Windows 用系统自带的 WebView2，支持把文件直接拖进窗口）；
没装或加 --browser 时用系统浏览器打开，页面关掉一分钟后自动退出。
界面本身在 ui/web/，后端和接口在 ui/server.py。
"""
import argparse
import json
import os
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _dialog(mode):
    """浏览器模式下由后端以子进程调用：弹 tkinter 对话框，把选中的路径以 JSON 打到 stdout。"""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if mode == "folder":
        r = filedialog.askdirectory()
        paths = [r] if r else []
    elif mode == "uemodel":
        r = filedialog.askopenfilename(filetypes=[("UEFormat", "*.uemodel"), ("All files", "*.*")])
        paths = [r] if r else []
    else:
        paths = list(filedialog.askopenfilenames(filetypes=[
            ("Models", "*.pmx *.vrm *.zip *.fbx *.unitypackage *.uemodel *.psk *.pskx *.xps"),
            ("All files", "*.*")]))
    root.destroy()
    print(json.dumps(paths, ensure_ascii=False))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) >= 2 and argv[0] == "--dialog":
        return _dialog(argv[1])
    ap = argparse.ArgumentParser(description="Model-To-VRM")
    ap.add_argument("paths", nargs="*", help="启动时加入的文件或文件夹")
    ap.add_argument("--browser", action="store_true", help="用系统浏览器，不用 pywebview 窗口")
    ap.add_argument("--no-open", action="store_true", help="只启动服务并打印地址（调试用）")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import server
    backend, srv, url = server.serve(a.port)
    if a.paths:
        backend.add_paths(a.paths)

    webview = None
    if not (a.browser or a.no_open):
        try:
            import webview
        except ImportError:
            webview = None
    try:
        if webview is not None:
            win = webview.create_window("Model-To-VRM", url, width=1440, height=900, min_size=(1000, 640))
            backend.window = win

            def on_start():
                # 拖进窗口的文件：pywebview 5+ 在 drop 事件里给出完整路径
                try:
                    from webview.dom import DOMEventHandler

                    def on_drop(e):
                        files = (e.get("dataTransfer") or {}).get("files") or []
                        backend.add_paths([f.get("pywebviewFullPath") for f in files])
                    win.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
                except Exception as ex:  # noqa: BLE001  旧版 pywebview：只能用按钮添加
                    backend.log("拖放不可用（%s），请用「添加文件」" % ex, "warn")

            webview.start(on_start)
        else:
            print("Model-To-VRM：%s（关闭页面一分钟后自动退出，Ctrl+C 立即退出）" % url, flush=True)
            if not a.no_open:
                webbrowser.open(url)
            while a.no_open or time.time() - backend.last_seen < 60 or backend.busy:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        backend.close()


if __name__ == "__main__":
    main()
