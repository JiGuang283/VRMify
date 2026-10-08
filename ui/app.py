# -*- coding: utf-8 -*-
"""Model-To-VRM 图形界面入口。

    python ui/app.py [文件或文件夹 ...] [--browser] [--port N]

装了 pywebview 时开一个原生窗口（Windows 用系统自带的 WebView2，支持把文件直接拖进窗口）；
没装、加 --browser、或者系统缺 WebView2 / 窗口打不开时用系统浏览器打开，页面关掉一分钟后自动退出。
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
            ("Models", "*.pmx *.vrm *.zip *.7z *.rar *.tar *.tgz *.gz *.bz2 *.xz *.fbx *.unitypackage *.uemodel *.psk *.pskx *.xps"),
            ("All files", "*.*")]))
    root.destroy()
    print(json.dumps(paths, ensure_ascii=False))


def _webview2_ok():
    """Windows 上有没有 WebView2 运行时（和 pywebview 的判断一致：看 EdgeUpdate 的注册表）。

    没有的话 pywebview 会退回 IE 内核（MSHTML），界面用的 ES 模块 / WebGL 在 IE 里跑不起来，
    窗口一片空白——不如直接用浏览器打开。
    """
    if os.name != "nt":
        return True
    import platform
    import winreg
    keys = ("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
            "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}", "{65C35B14-6C1D-4122-AC46-7148CC9D6497}")
    for key in keys:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            wow = "" if (platform.machine() == "x86" or root == winreg.HKEY_CURRENT_USER) else "WOW6432Node\\"
            try:
                with winreg.OpenKey(root, "SOFTWARE\\%sMicrosoft\\EdgeUpdate\\Clients\\%s" % (wow, key)) as k:
                    pv = str(winreg.QueryValueEx(k, "pv")[0])
            except OSError:
                continue
            try:
                if tuple(int(x) for x in pv.split(".")[:3]) >= (86, 0, 622):
                    return True
            except ValueError:
                pass
    return False


def _browser_loop(backend, url, open_page, keep):
    print("Model-To-VRM：%s（关闭页面一分钟后自动退出，Ctrl+C 立即退出）" % url, flush=True)
    if open_page:
        webbrowser.open(url)
    backend.last_seen = time.time()
    while keep or time.time() - backend.last_seen < 60 or backend.busy:
        time.sleep(1)


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
        if webview is not None and not _webview2_ok():
            webview = None
            backend.log("系统里没有 WebView2 运行时，改用浏览器打开（装上 WebView2 后就会用独立窗口："
                        "https://developer.microsoft.com/microsoft-edge/webview2/）", "warn")
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

            try:
                webview.start(on_start)
            except Exception as ex:  # noqa: BLE001  窗口起不来：退回浏览器
                backend.window = None
                backend.log("独立窗口打不开（%s: %s），改用浏览器" % (type(ex).__name__, ex), "warn")
                _browser_loop(backend, url, True, False)
        else:
            _browser_loop(backend, url, not a.no_open, a.no_open)
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        backend.close()


if __name__ == "__main__":
    main()
