# -*- coding: utf-8 -*-
"""界面启动的兜底逻辑：数据目录写不了时改放 %APPDATA%，没有 WebView2 / 窗口起不来时改用浏览器。"""
import json
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


class OutputDefaultTest(unittest.TestCase):
    """界面默认保存到程序目录下的 vrm；旧设置文件里是旧默认值（源文件旁）的换成新默认。"""

    def load(self, saved):
        path = os.path.join(tempfile.mkdtemp(prefix="m2v_set_"), "settings.json")
        if saved is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(saved, f)
        with mock.patch.object(server, "SETTINGS", path):
            return server.Backend._load_settings(None)[0]["output"]

    def test_fresh(self):
        self.assertEqual(self.load(None)["mode"], "app")

    def test_old_default_migrates(self):
        old = {"opts": {"output": {"mode": "beside", "folder": "", "subfolder": True}}}
        self.assertEqual(self.load(old)["mode"], "app")

    def test_own_choice_kept(self):
        mine = {"opts": {"output": {"mode": "folder", "folder": "D:/out", "subfolder": True}}}
        self.assertEqual(self.load(mine)["mode"], "folder")
        # 新版存过的「源文件旁」是自己选的
        self.assertEqual(self.load({"v": 2, "opts": {"output": {"mode": "beside", "folder": "", "subfolder": True}}})["mode"],
                         "beside")

    def test_app_folder_used(self):
        import pipeline
        out = tempfile.mkdtemp(prefix="m2v_app_")
        opts = pipeline.merged({"output": {"mode": "app", "app_folder": out, "subfolder": False}})
        self.assertEqual(pipeline.output_dir("X:/models/a.pmx", opts), out)
        opts["output"].pop("app_folder")                      # 没给路径（命令行）时同「源文件旁」
        self.assertEqual(pipeline.output_dir(os.path.join(out, "a.pmx"), opts), out)


class LayoutTest(unittest.TestCase):
    """拖动调的栏宽存进 settings.json：只收认识的键、夹在范围里；传 None 是恢复默认。"""

    def test_widths_saved_and_clamped(self):
        path = os.path.join(tempfile.mkdtemp(prefix="m2v_ui_"), "settings.json")
        with mock.patch.object(server, "SETTINGS", path):
            b = server.Backend.__new__(server.Backend)
            b.lock = __import__("threading").RLock()
            b.opts, b.lang, b.ui = server.Backend._load_settings(None)
            self.assertEqual(b.ui, {})
            b.set_options(ui={"left": 520.6, "side": 50, "bogus": 3, "sideWide": "x"})
            self.assertEqual(b.ui, {"left": 520, "side": 220})
            b.set_options(ui={"sideWide": 400})
            b.set_options(ui={"left": None})
            self.assertEqual(server.Backend._load_settings(None)[2], {"side": 220, "sideWide": 400})


class SkippedStatusTest(unittest.TestCase):
    """文件里的模型全被「只转换人物」跳过：状态是「已跳过」不是「完成」；关掉这个选项后回到「待转换」。"""

    def test_skipped(self):
        tmp = tempfile.mkdtemp(prefix="m2v_skip_")
        prop = os.path.join(tmp, "剑.pmx")
        with open(prop, "wb") as f:
            f.write(b"PMX ")
        with mock.patch.object(server, "SETTINGS", os.path.join(tmp, "settings.json")), \
                mock.patch.object(server, "EDITS", os.path.join(tmp, "edits.json")):
            b = server.Backend()
            try:
                b.add_paths([prop])
                fid = next(iter(b.files))
                with mock.patch.object(server.pipeline, "convert_file", return_value=[]):
                    b._run([fid], b.opts)
                self.assertEqual(b.files[fid]["status"], "skipped")
                end = [e for _, e in b.events if e["type"] == "busy" and not e["busy"]][-1]
                self.assertEqual((end["done"], end["skipped"], end["failed"]), (0, 1, 0))
                b.set_options(dict(b.opts, avatar_only=False))
                self.assertEqual(b.files[fid]["status"], "pending")
            finally:
                b.close()


class StalePreviewTest(unittest.TestCase):
    def test_sweep(self):
        """启动时删一天以前遗留的预览临时目录，新的不动。"""
        root = tempfile.mkdtemp(prefix="m2v_tmproot_")
        old, new, other = (os.path.join(root, n) for n in ("model2vrm_preview_old", "model2vrm_preview_new", "keep_me"))
        for d in (old, new, other):
            os.makedirs(os.path.join(d, "sub"))
        past = 1_000_000_000
        for d in (old, other):
            os.utime(os.path.join(d, "sub"), (past, past))
            os.utime(d, (past, past))
        with mock.patch.object(server.tempfile, "gettempdir", lambda: root):
            server._sweep_stale_previews()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertTrue(os.path.exists(other))


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
