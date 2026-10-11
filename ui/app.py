# -*- coding: utf-8 -*-
"""VRMify 图形界面入口。

    python ui/app.py [文件或文件夹 ...] [--browser] [--port N]

装了 pywebview 时开一个原生窗口（Windows 用系统自带的 WebView2，支持把文件直接拖进窗口）；
没装、加 --browser、或者系统缺 WebView2 / 窗口打不开时用系统浏览器打开，页面关掉一分钟后自动退出。
用了哪种、为什么，写在数据文件夹的 startup.log 里；退到浏览器时页面顶上也会显示原因。
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


def _webview2_loader_version():
    """直接问 WebView2 的加载器（pywebview 自带的 WebView2Loader.dll）系统里装了哪个版本的运行时，没有为 None。

    比查注册表准：精简过的系统、删过 Edge 的机器，运行时还在，注册表里却可能没有 EdgeUpdate 的记录。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        import sysconfig
        import webview
        arch = {"win-amd64": "win-x64", "win-arm64": "win-arm64"}.get(sysconfig.get_platform(), "win-x86")
        lib = ctypes.WinDLL(os.path.join(os.path.dirname(webview.__file__), "lib", "runtimes", arch, "native",
                                         "WebView2Loader.dll"))
        out = ctypes.c_wchar_p()
        if lib.GetAvailableCoreWebView2BrowserVersionString(None, ctypes.byref(out)) != 0 or not out.value:
            return None
        ver = out.value
        ctypes.windll.ole32.CoTaskMemFree(out)
        return ver
    except Exception:  # noqa: BLE001
        return None


def _webview2_folder(ver):
    """运行时 ver 装在哪（msedgewebview2.exe 所在文件夹），找不到为 None。"""
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        d = os.path.join(base or "", "Microsoft", "EdgeWebView", "Application", ver)
        if base and os.path.isfile(os.path.join(d, "msedgewebview2.exe")):
            return d
    return None


def _diagnose(backend, mode, reason=None, detail=""):
    """启动方式和原因写进数据文件夹的 startup.log（每次启动覆盖），用户反馈问题时可以直接发过来；
    退到浏览器时把原因交给页面显示（server.Backend.mode_note）。"""
    import platform
    import server
    from version import __version__
    path = os.path.join(server.DATA_DIR, "startup.log")
    if reason:
        backend.mode_note = {"zh": reason[0], "en": reason[1], "kind": reason[2], "log": path}
        backend.log(reason[0], "warn")
    try:
        with open(path, "w", encoding="utf-8") as f:
            lines = ["VRMify %s · %s · Python %s %s" % (__version__, platform.platform(), platform.python_version(),
                                                        platform.machine()),
                     "%s · 窗口：%s" % (time.strftime("%Y-%m-%d %H:%M:%S"), mode)]
            lines += [reason[0]] if reason else []
            lines += [detail.rstrip()] if detail.strip() else []
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


# (中文, 英文, 种类)：页面按种类决定附加什么（没有 WebView2 时给安装链接）
NO_WEBVIEW2 = ("系统里没有 WebView2 运行时（Windows 的网页组件），所以用浏览器打开。安装后重新打开程序就是独立窗口。",
               "WebView2 (the Windows web component) is not installed, so VRMify opened in the browser. "
               "Install it and start VRMify again to get its own window.", "no_webview2")
NO_PYWEBVIEW = ("没有安装 pywebview，所以用浏览器打开（pip install pywebview 后就是独立窗口）。",
                "pywebview is not installed, so VRMify opened in the browser (pip install pywebview for its own window).",
                "no_pywebview")


def _window_failed(ex):
    msg = "%s: %s" % (type(ex).__name__, ex)
    return ("独立窗口启动失败（%s），所以用浏览器打开。" % msg,
            "VRMify's window failed to start (%s), so it opened in the browser." % msg, "window_failed")


def _browser_loop(backend, url, open_page, keep):
    print("VRMify：%s（关闭页面一分钟后自动退出，Ctrl+C 立即退出）" % url, flush=True)
    if open_page:
        webbrowser.open(url)
    backend.last_seen = time.time()
    while keep or time.time() - backend.last_seen < 60 or backend.busy:
        time.sleep(1)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) >= 2 and argv[0] == "--dialog":
        return _dialog(argv[1])
    ap = argparse.ArgumentParser(description="VRMify")
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

    webview, detail = None, ""
    if a.browser or a.no_open:
        _diagnose(backend, "浏览器（--browser / --no-open）")
    else:
        try:
            import webview
        except ImportError:
            webview = None
            _diagnose(backend, "浏览器", NO_PYWEBVIEW)
        if webview is not None and not _webview2_ok():
            # 注册表里没有记录（pywebview 只看注册表，会退回 IE 内核、窗口一片空白）：再问加载器
            ver = _webview2_loader_version()
            if ver:
                folder = _webview2_folder(ver)
                # 指定运行时文件夹后 pywebview 就用 WebView2；文件夹没找到时给一个不存在的路径，
                # pywebview 会认为有 WebView2，建窗口时再退回系统装的运行时（加载器刚确认过有）
                webview.settings["WEBVIEW2_RUNTIME_PATH"] = folder or os.path.join(HERE, "_system_webview2_")
                detail = "注册表里没有 WebView2 的记录，加载器检测到运行时 %s（%s）" % (ver, folder or "系统默认位置")
            else:
                webview = None
                _diagnose(backend, "浏览器", NO_WEBVIEW2)
    try:
        if webview is not None:
            win = webview.create_window("VRMify", url, width=1440, height=900, min_size=(1000, 640))
            backend.window = win

            def on_start():
                _diagnose(backend, "独立窗口（WebView2）", detail=detail)
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
                webview.start(on_start, gui="edgechromium" if os.name == "nt" else None)
            except Exception as ex:  # noqa: BLE001  窗口起不来：退回浏览器
                import traceback
                backend.window = None
                _diagnose(backend, "浏览器", _window_failed(ex), detail + "\n" + traceback.format_exc())
                _browser_loop(backend, url, True, False)
        else:
            _browser_loop(backend, url, not a.no_open, a.no_open)
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        backend.close()


if __name__ == "__main__":
    # 同时转换几个文件时开的子进程（convert/parallel.py）：打包的 exe 里它们也是这个 exe，
    # 要先在这里接住，不然每个子进程都会再开一个程序窗口
    import multiprocessing
    multiprocessing.freeze_support()
    main()
