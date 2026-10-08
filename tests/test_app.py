# -*- coding: utf-8 -*-
"""界面启动的兜底逻辑：数据目录写不了时改放 %APPDATA%，没有 WebView2 / 窗口起不来时改用浏览器。"""
import os
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(os.path.dirname(HERE), "ui")
if UI not in sys.path:
    sys.path.insert(0, UI)
os.environ.setdefault("MODEL2VRM_DATA", tempfile.mkdtemp(prefix="m2v_data_"))
import app  # noqa: E402
import server  # noqa: E402
import version  # noqa: E402


class VersionTest(unittest.TestCase):
    def test_semver(self):
        self.assertRegex(version.__version__, r"^\d+\.\d+\.\d+$")


class DataDirTest(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict(os.environ, {"MODEL2VRM_DATA": "X:/somewhere"}):
            self.assertEqual(server._data_dir(), "X:/somewhere")

    def test_beside_program(self):
        env = {k: v for k, v in os.environ.items() if k != "MODEL2VRM_DATA"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(server, "_writable", lambda d: True):
            self.assertEqual(server._data_dir(), server.ROOT)

    def test_fallback_to_appdata(self):
        env = {k: v for k, v in os.environ.items() if k != "MODEL2VRM_DATA"}
        env["APPDATA"] = "Y:/AppData/Roaming"
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(server, "_writable", lambda d: False):
            self.assertEqual(server._data_dir(), os.path.join("Y:/AppData/Roaming", "Model-To-VRM"))

    def test_writable(self):
        self.assertTrue(server._writable(tempfile.mkdtemp(prefix="m2v_w_")))


class WindowFallbackTest(unittest.TestCase):
    """pywebview 换成假模块，_browser_loop 换成记录调用，看 main() 走哪条路。"""

    def run_main(self, webview2, start_error=None):
        fake = types.ModuleType("webview")
        fake.create_window = lambda *a, **k: types.SimpleNamespace()

        def start(cb=None):
            if start_error:
                raise start_error
        fake.start = start
        calls = []
        with mock.patch.dict(sys.modules, {"webview": fake}), \
                mock.patch.object(app, "_webview2_ok", lambda: webview2), \
                mock.patch.object(app, "_browser_loop", lambda *a: calls.append(a)):
            app.main(["--port", "0"])
        return calls

    def test_window(self):
        self.assertEqual(self.run_main(webview2=True), [])

    def test_no_webview2(self):
        calls = self.run_main(webview2=False)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][2])               # 打开浏览器

    def test_window_fails(self):
        calls = self.run_main(webview2=True, start_error=RuntimeError("boom"))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][2])

    def test_webview2_check_runs(self):
        self.assertIsInstance(app._webview2_ok(), bool)


class ModelRouteTest(unittest.TestCase):
    """/model/<token>/<相对路径>：PMX 原生预览读模型和贴图的接口。"""

    @classmethod
    def setUpClass(cls):
        cls.backend, cls.srv, cls.url = server.serve(0)
        cls.tmp = tempfile.mkdtemp(prefix="m2v_route_")
        cls.folder = os.path.join(cls.tmp, "模型")
        os.makedirs(os.path.join(cls.folder, "tex"))
        with open(os.path.join(cls.folder, "a.pmx"), "wb") as f:
            f.write(b"PMX " + bytes([0, 0, 0, 64]))
        with open(os.path.join(cls.folder, "tex", "s.spa"), "wb") as f:
            f.write(b"BM" + bytes(60))
        with open(os.path.join(cls.tmp, "secret.txt"), "w") as f:
            f.write("outside")
        try:
            from PIL import Image
            Image.new("RGB", (4, 4), (255, 0, 0)).save(os.path.join(cls.folder, "tex", "c.dds"))
            cls.dds = True
        except Exception:  # noqa: BLE001
            cls.dds = False
        cls.base = cls.url.rstrip("/") + cls.backend.dir_url(os.path.join(cls.folder, "a.pmx")).rsplit("/", 1)[0]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.backend.close()

    def get(self, path):
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(self.base + "/" + path) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            e.close()
            return e.code, None, b""

    def test_files(self):
        st, ct, data = self.get("a.pmx")
        self.assertEqual((st, data[:4]), (200, b"PMX "))
        st, ct, _ = self.get("tex/s.spa")
        self.assertEqual((st, ct), (200, "image/bmp"))           # 球面贴图其实是 BMP
        st, ct, _ = self.get("tex%5Cs.spa")                       # PMX 里写的是反斜杠路径
        self.assertEqual(st, 200)
        if self.dds:
            st, ct, data = self.get("tex/c.dds")
            self.assertEqual((st, ct, data[:4]), (200, "image/png", bytes([0x89]) + b"PNG"))

    def test_outside_refused(self):
        self.assertEqual(self.get("../secret.txt")[0], 404)
        self.assertEqual(self.get("%2E%2E/secret.txt")[0], 404)
        self.assertEqual(self.get("missing.png")[0], 404)
        import urllib.error
        import urllib.request
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url + "model/nosuchtoken/a.pmx")
        cm.exception.close()


if __name__ == "__main__":
    unittest.main()
