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
os.environ.setdefault("VRMIFY_DATA", tempfile.mkdtemp(prefix="m2v_data_"))
import app  # noqa: E402
import server  # noqa: E402
import logtext  # noqa: E402,I100
import version  # noqa: E402


class VersionTest(unittest.TestCase):
    def test_semver(self):
        self.assertRegex(version.__version__, r"^\d+\.\d+\.\d+$")


class DataDirTest(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict(os.environ, {"VRMIFY_DATA": "X:/somewhere"}):
            self.assertEqual(server._data_dir(), "X:/somewhere")

    def test_beside_program(self):
        env = {k: v for k, v in os.environ.items() if k != "VRMIFY_DATA"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(server, "_writable", lambda d: True):
            self.assertEqual(server._data_dir(), server.ROOT)

    def test_fallback_to_appdata(self):
        env = {k: v for k, v in os.environ.items() if k != "VRMIFY_DATA"}
        env["APPDATA"] = "Y:/AppData/Roaming"
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(server, "_writable", lambda d: False):
            self.assertEqual(server._data_dir(), os.path.join("Y:/AppData/Roaming", "VRMify"))

    def test_appdata_renamed_from_old_name(self):
        """改名前的 %APPDATA%\\Model-To-VRM 还在、新的没有时改名过去，里面的设置跟着走。"""
        base = tempfile.mkdtemp(prefix="m2v_appdata_")
        os.makedirs(os.path.join(base, "Model-To-VRM"))
        open(os.path.join(base, "Model-To-VRM", "settings.json"), "w").close()
        env = {k: v for k, v in os.environ.items() if k != "VRMIFY_DATA"}
        env["APPDATA"] = base
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(server, "_writable", lambda d: False):
            self.assertEqual(server._data_dir(), os.path.join(base, "VRMify"))
        self.assertTrue(os.path.isfile(os.path.join(base, "VRMify", "settings.json")))
        self.assertFalse(os.path.exists(os.path.join(base, "Model-To-VRM")))

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
                b.set_options(dict(b.opts, vrm=dict(b.opts["vrm"], avatar_only=False)))
                self.assertEqual(b.files[fid]["status"], "pending")
            finally:
                b.close()


class PreviewCacheTest(unittest.TestCase):
    """还没转换的文件（压缩包、FBX …）预览时先转一份临时 VRM：换了转换设置要重新转，同样的设置用缓存，
    连点不重复转，转的途中又换了设置时旧的那份不推给页面，每个文件只留最近几份。"""

    def setUp(self):
        import threading
        self.tmp = tempfile.mkdtemp(prefix="m2v_pv_")
        src = os.path.join(self.tmp, "a.fbx")
        with open(src, "wb") as f:
            f.write(b"Kaydara FBX Binary")
        self.patches = [mock.patch.object(server, "SETTINGS", os.path.join(self.tmp, "settings.json")),
                        mock.patch.object(server, "EDITS", os.path.join(self.tmp, "edits.json")),
                        mock.patch.object(server.pipeline, "convert_file", self.fake)]
        for p in self.patches:
            p.start()
        self.calls, self.gate = [], threading.Event()
        self.gate.set()
        self.b = server.Backend()
        self.b.add_paths([src])
        self.fid = next(iter(self.b.files))

    def tearDown(self):
        self.gate.set()
        self.b.close()
        for p in self.patches:
            p.stop()

    def fake(self, src, opts, log=None, out_dir=None, edits=None):
        self.gate.wait(10)
        self.calls.append((opts["vrm"]["spec"], opts["vrm"]["spring_stiffness"]))
        p = os.path.join(out_dir, "a.vrm")
        with open(p, "wb") as f:
            f.write(b"glTF")
        return [p]

    def wait(self):
        import time
        for _ in range(500):
            with self.b.lock:
                if not self.b.preview_jobs:
                    return
            time.sleep(0.01)
        self.fail("preview conversion did not finish")

    def setopt(self, **vrm):
        self.b.set_options(dict(self.b.opts, vrm=dict(self.b.opts["vrm"], **vrm)))

    def shown(self):
        """推给页面的预览事件个数。"""
        return len([e for _, e in self.b.events if e["type"] == "preview" and e.get("url")])

    def test_settings_change_reconverts(self):
        self.assertEqual(self.b.preview(self.fid), {"pending": True})
        self.wait()
        self.assertIn("url", self.b.preview(self.fid))                  # 同样的设置：直接用缓存
        self.assertEqual(self.calls, [("1.0", 1.0)])
        self.setopt(spec="0.x")
        self.assertEqual(self.b.preview(self.fid), {"pending": True})  # 换了版本：重新转
        self.wait()
        self.assertEqual(self.calls[-1], ("0.x", 1.0))
        self.setopt(spec="1.0")
        self.assertIn("url", self.b.preview(self.fid))                  # 改回来：之前那份还在
        self.assertEqual(len(self.calls), 2)
        # 只改输出位置不算换设置
        self.b.set_options(dict(self.b.opts, output={"mode": "folder", "folder": self.tmp, "subfolder": False}))
        self.assertIn("url", self.b.preview(self.fid))

    def test_no_duplicate_job(self):
        self.gate.clear()
        self.assertEqual(self.b.preview(self.fid), {"pending": True})
        self.assertEqual(self.b.preview(self.fid), {"pending": True})
        self.gate.set()
        self.wait()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.shown(), 1)

    def test_superseded_job_not_shown(self):
        self.gate.clear()
        self.b.preview(self.fid)                          # 1.0，还在转
        self.setopt(spec="0.x")
        self.b.preview(self.fid)                          # 用户换成 0.x 又点了预览
        self.gate.set()
        self.wait()
        self.assertEqual(sorted(c[0] for c in self.calls), ["0.x", "1.0"])
        self.assertEqual(self.shown(), 1)                 # 只推 0.x 那份
        f = self.b.files[self.fid]
        want = f["previews"][f["preview_want"]]
        self.assertEqual(f["preview_path"], want[1])

    def test_old_previews_removed(self):
        dirs = []
        for k in range(5):
            self.setopt(spring_stiffness=1.0 + k)
            self.b.preview(self.fid)
            self.wait()
            dirs.append(os.path.dirname(self.b.files[self.fid]["preview_path"]))
        cache = self.b.files[self.fid]["previews"]
        self.assertEqual(len(cache), server._PREVIEW_KEEP)
        self.assertEqual([os.path.isdir(d) for d in dirs], [False, False, True, True, True])


class AutoThumbTest(unittest.TestCase):
    """转换后自动生成的缩略图：记成 auto；手动设置过的不覆盖；不算「调整过」；关掉选项后转换不再带上。"""

    def test_auto_vs_manual(self):
        import base64
        sys.path[:0] = [os.path.join(os.path.dirname(HERE), p) for p in ("convert", "formats")] + [HERE]
        import pmx2vrm
        import synth
        import vrmio
        tmp = tempfile.mkdtemp(prefix="m2v_thumb_")
        pmx = synth.build(os.path.join(tmp, "src"))
        vrm = os.path.join(tmp, "out.vrm")
        pmx2vrm.convert(pmx, vrm)
        png = "data:image/png;base64," + base64.b64encode(vrmio.encode_png(2, 2, [b"\xff" * 8] * 2)).decode()
        with mock.patch.object(server, "SETTINGS", os.path.join(tmp, "settings.json")), \
                mock.patch.object(server, "EDITS", os.path.join(tmp, "edits.json")), \
                mock.patch.object(server, "EDITS_DIR", os.path.join(tmp, "edits")):
            b = server.Backend()
            try:
                b.add_paths([pmx])
                fid = next(iter(b.files))
                f = b.files[fid]
                f.update(status="done", outputs=b._outputs([vrm]))

                def meta_thumb():
                    return vrmio.read_glb(vrm)[0]["extensions"]["VRMC_vrm"]["meta"].get("thumbnailImage")

                self.assertTrue(b.tune_thumb(fid, png, auto=True)["ok"])
                self.assertTrue(b.edits_for(f)["thumbnails"]["out"]["auto"])     # 按模型（VRM 文件名）存
                self.assertIsNotNone(meta_thumb())
                self.assertFalse(b._entry(f)["tuned"])               # 只有自动缩略图：不算调整过
                b.tune_save(fid, {"title": "名前"})                   # 调整面板保存：缩略图和标记都留着
                self.assertTrue(b.edits_for(f)["thumbnails"]["out"]["auto"])
                self.assertIsNotNone(meta_thumb())
                b.set_options(dict(b.opts, vrm=dict(b.opts["vrm"], auto_thumb=False)))
                self.assertNotIn("thumbnails", b._convert_edits(f))   # 关掉后转换不再套用自动截的
                self.assertEqual(b._convert_edits(f)["title"], "名前")
                self.assertTrue(b.tune_thumb(fid, png)["ok"])         # 手动设置：去掉标记
                self.assertFalse(b.edits_for(f)["thumbnails"]["out"]["auto"])
                self.assertTrue(b.tune_thumb(fid, png, auto=True)["skipped"])
                self.assertIn("thumbnails", b._convert_edits(f))
            finally:
                b.close()

    def test_per_model(self):
        """一个包里几个模型：缩略图各存各的、只写进自己的 VRM；旧版按文件存的那张分给各个模型。"""
        import base64
        sys.path[:0] = [os.path.join(os.path.dirname(HERE), p) for p in ("convert", "formats")] + [HERE]
        import pmx2vrm
        import synth
        import vrm_edit
        import vrmio
        tmp = tempfile.mkdtemp(prefix="m2v_thumbs_")
        pmx = synth.build(os.path.join(tmp, "src"))
        vrms = [os.path.join(tmp, n + ".vrm") for n in ("a", "b")]
        for v in vrms:
            pmx2vrm.convert(pmx, v)

        def png(c):
            return "data:image/png;base64," + base64.b64encode(vrmio.encode_png(2, 2, [bytes([c]) * 8] * 2)).decode()

        def thumb(v):
            g, bin_data = vrmio.read_glb(v)
            i = g["extensions"]["VRMC_vrm"]["meta"].get("thumbnailImage")
            if i is None:
                return None
            bv = g["bufferViews"][g["images"][i]["bufferView"]]
            return bin_data[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]

        with mock.patch.object(server, "SETTINGS", os.path.join(tmp, "settings.json")),                 mock.patch.object(server, "EDITS", os.path.join(tmp, "edits.json")),                 mock.patch.object(server, "EDITS_DIR", os.path.join(tmp, "edits")):
            b = server.Backend()
            try:
                b.add_paths([pmx])
                fid = next(iter(b.files))
                f = b.files[fid]
                f.update(status="done", outputs=b._outputs(vrms))
                self.assertTrue(b.tune_thumb(fid, png(0x10), auto=True, model="a")["ok"])
                self.assertIsNotNone(thumb(vrms[0]))
                self.assertIsNone(thumb(vrms[1]))                      # 只写进 a
                self.assertTrue(b.tune_thumb(fid, png(0x20), auto=True, model="b")["ok"])
                self.assertNotEqual(thumb(vrms[0]), thumb(vrms[1]))
                self.assertEqual(b.tune_info(fid, "b")["model"], "b")
                self.assertTrue(b.tune_info(fid, "b")["thumbnail_auto"])
                b.tune_thumb(fid, png(0x30), model="a")                 # 手动设置 a：a 不再自动更新，b 照常
                self.assertTrue(b.tune_thumb(fid, png(0x40), auto=True, model="a")["skipped"])
                self.assertTrue(b.tune_thumb(fid, png(0x50), auto=True, model="b")["ok"])
                b.tune_thumb(fid, "", model="b")                        # 去掉 b 的，a 的留着
                self.assertIsNone(thumb(vrms[1]))
                self.assertIsNotNone(thumb(vrms[0]))
                # 旧版：一张对所有模型；给 a 单独设置后 b 仍用旧的那张
                old = os.path.join(tmp, "old.png")
                with open(old, "wb") as fh:
                    fh.write(base64.b64decode(png(0x60).split(",", 1)[1]))
                b.edits[b._key(f)] = {"thumbnail": old}
                self.assertEqual(vrm_edit.thumb_for(b.edits_for(f), vrms[1]), old)
                b.tune_thumb(fid, png(0x70), model="a")
                e = b.edits_for(f)
                self.assertNotIn("thumbnail", e)
                self.assertEqual(e["thumbnails"]["b"]["path"], old)
                self.assertNotEqual(e["thumbnails"]["a"]["path"], old)
            finally:
                b.close()


class LanguageTest(unittest.TestCase):
    """界面语言：没选过时跟系统；日志事件带英文版（convert/logtext.py）。"""

    def test_default_follows_the_system(self):
        tmp = tempfile.mkdtemp(prefix="m2v_lang_")
        settings = os.path.join(tmp, "settings.json")
        with mock.patch.object(server, "SETTINGS", settings), \
                mock.patch.object(server, "EDITS", os.path.join(tmp, "edits.json")):
            for sys_lang in ("en", "zh_CN"):
                with mock.patch.object(server, "system_lang", return_value=sys_lang):
                    b = server.Backend()
                    try:
                        self.assertEqual(b.lang, sys_lang)
                    finally:
                        b.close()
            with open(settings, "w", encoding="utf-8") as f:
                json.dump({"v": 2, "lang": "zh_CN"}, f)
            with mock.patch.object(server, "system_lang", return_value="en"):
                b = server.Backend()
                try:
                    self.assertEqual(b.lang, "zh_CN")              # 选过的不变
                finally:
                    b.close()
        self.assertIn(server.system_lang(), server.LANGS)

    def test_log_events_carry_english(self):
        tmp = tempfile.mkdtemp(prefix="m2v_lang_")
        with mock.patch.object(server, "SETTINGS", os.path.join(tmp, "settings.json")), \
                mock.patch.object(server, "EDITS", os.path.join(tmp, "edits.json")):
            b = server.Backend()
            try:
                b.log("完成，用时 1.5 秒", "ok")
                b.log("没有对照的一句")
                ev = [e for _, e in b.events if e["type"] == "log"][-2:]
                self.assertEqual(ev[0]["en"], "Done in 1.5 s")
                self.assertIsNone(ev[1]["en"])
            finally:
                b.close()


class StalePreviewTest(unittest.TestCase):
    def test_sweep(self):
        """启动时删一天以前遗留的预览临时目录，新的不动。"""
        root = tempfile.mkdtemp(prefix="m2v_tmproot_")
        old, new, other = (os.path.join(root, n) for n in ("model2vrm_preview_old", "vrmify_preview_new", "keep_me"))
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

    def run_main(self, webview2, start_error=None, loader=None):
        """webview2：注册表检测的结果；loader：WebView2 加载器报的版本（None = 没有）。→ (_browser_loop 的调用, backend, 假模块)"""
        fake = types.ModuleType("webview")
        fake.settings = {"WEBVIEW2_RUNTIME_PATH": None}
        fake.create_window = lambda *a, **k: types.SimpleNamespace()

        def start(cb=None, **kw):
            if start_error:
                raise start_error
        fake.start = start
        calls, made = [], []
        real_serve = server.serve

        def serve(port=0):
            r = real_serve(port)
            made.append(r[0])
            return r
        with mock.patch.dict(sys.modules, {"webview": fake}),                 mock.patch.object(app, "_webview2_ok", lambda: webview2),                 mock.patch.object(app, "_webview2_loader_version", lambda: loader),                 mock.patch.object(server, "serve", serve),                 mock.patch.object(app, "_browser_loop", lambda *a: calls.append(a)):
            app.main(["--port", "0"])
        return calls, made[0], fake

    def test_window(self):
        calls, b, fake = self.run_main(webview2=True)
        self.assertEqual(calls, [])
        self.assertIsNone(b.mode_note)
        self.assertIsNone(fake.settings["WEBVIEW2_RUNTIME_PATH"])

    def test_no_webview2(self):
        calls, b, _ = self.run_main(webview2=False)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][2])               # 打开浏览器
        self.assertEqual(b.mode_note["kind"], "no_webview2")
        self.assertEqual(b.state()["mode_note"]["kind"], "no_webview2")
        with open(b.mode_note["log"], encoding="utf-8") as f:
            self.assertIn("WebView2", f.read())     # 原因也写进 startup.log

    def test_registry_misses_but_loader_finds(self):
        """注册表里没有记录、加载器却查到运行时（精简过的系统）：照常开窗口，告诉 pywebview 用 WebView2。"""
        calls, b, fake = self.run_main(webview2=False, loader="120.0.1.2")
        self.assertEqual(calls, [])
        self.assertIsNone(b.mode_note)
        self.assertTrue(fake.settings["WEBVIEW2_RUNTIME_PATH"])

    def test_window_fails(self):
        calls, b, _ = self.run_main(webview2=True, start_error=RuntimeError("boom"))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][2])
        self.assertEqual(b.mode_note["kind"], "window_failed")
        self.assertIn("boom", b.mode_note["zh"])
        with open(b.mode_note["log"], encoding="utf-8") as f:
            self.assertIn("Traceback", f.read())

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

    def test_moved_texture(self):
        """PMX 记的位置没有、文件夹里别处有同名贴图：和转换时一样按文件名找到。"""
        deep = os.path.join(self.folder, "sub", "deep")
        os.makedirs(deep, exist_ok=True)
        with open(os.path.join(deep, "moved.png"), "wb") as f:
            f.write(bytes([0x89]) + b"PNG\r\n\x1a\n")
        self.assertEqual(self.get("moved.png")[:2], (200, "image/png"))
        self.assertEqual(self.get("textures/moved.png")[0], 200)
        self.assertEqual(self.get("nothere.png")[0], 404)

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


class UploadTest(unittest.TestCase):
    """浏览器模式：拖入 / 选择的文件传到临时文件夹再加入列表。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m2v_up_")
        self.p = mock.patch.object(server, "UPLOADS", os.path.join(self.tmp, "uploads"))
        self.p.start()
        self.backend, self.srv, self.url = server.serve(0)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.backend.close()
        self.p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, key, path, data):
        import urllib.parse
        import urllib.request
        q = urllib.parse.urlencode({"key": key, "path": path})
        with urllib.request.urlopen(urllib.request.Request(self.url + "api/upload?" + q, data=data, method="POST")) as r:
            return json.loads(r.read())

    def done(self, items):
        import urllib.request
        req = urllib.request.Request(self.url + "api/upload/done", data=json.dumps({"items": items}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_folder(self):
        self.assertEqual(self.post("k1", "模型/a.pmx", b"PMX data"), {"ok": True})
        self.assertEqual(self.post("k1", "模型/tex/b.png", b"png"), {"ok": True})
        added = self.done([{"key": "k1", "roots": ["模型"]}])["added"]
        self.assertEqual(len(added), 1)
        f = self.backend.files[added[0]]
        self.assertTrue(f["path"].startswith(server.UPLOADS))
        self.assertEqual(os.path.relpath(f["path"], server.UPLOADS).split(os.sep)[1:], ["模型", "a.pmx"])
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(f["path"]), "tex", "b.png")))
        # 同样的东西再传一次：落在同一个位置（调整按路径保存），已经在列表里就不重复加
        self.assertEqual(self.post("k1", "模型/a.pmx", b"PMX data"), {"ok": True})
        self.assertEqual(self.done([{"key": "k1", "roots": ["模型"]}])["added"], [])
        # 「保存在源文件旁边」：传进来的文件改存到程序目录
        opts = {"output": {"mode": "beside"}}
        self.assertEqual(self.backend._opts_for(f["path"], opts)["output"]["mode"], "app")
        self.assertIs(self.backend._opts_for(os.path.join(self.tmp, "x.pmx"), opts), opts)

    def test_paths_stay_inside(self):
        self.post("k2", "../../evil.pmx", b"x")
        self.post("k2", "a:b?.pmx", b"x")
        names = sorted(os.listdir(self.backend._upload_dir("k2")))
        self.assertEqual(names, ["a_b_.pmx", "evil.pmx"])

    def test_lone_pmx_warns(self):
        self.post("k3", "单独.pmx", b"x")
        after = self.backend.events[-1][0] if self.backend.events else 0
        self.assertEqual(len(self.done([{"key": "k3", "roots": ["单独.pmx"]}])["added"]), 1)
        logs = [e["msg"] for s, e in self.backend.events if s > after and e["type"] == "log"]
        self.assertTrue(any("单独.pmx" in m and "贴图" in m for m in logs), logs)
        self.assertTrue(all(logtext.to_en(m) for m in logs))

