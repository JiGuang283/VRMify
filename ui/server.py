# -*- coding: utf-8 -*-
"""图形界面的后端：文件列表、选项、转换任务、事件推送，和一个只监听 127.0.0.1 的 HTTP 服务。

页面（ui/web/）只通过 HTTP 接口和这里打交道，所以 pywebview 窗口和普通浏览器用的是同一套：

  GET  /api/state                     文件列表、选项、语言、是否在转换
  GET  /api/events?after=N            长轮询：日志 / 文件状态 / 进度 / 预览就绪
  POST /api/add      {paths}          加文件（文件夹会递归展开）
  POST /api/pick     {folder}         弹系统对话框选文件 / 文件夹再加入
  POST /api/pick_output               选输出文件夹，返回路径
  POST /api/pick_ue_ref               选 uemodel 的整身对齐参考，返回路径（参考像部件时带 warn）
  POST /api/remove   {ids} · /api/clear
  POST /api/options  {opts, lang}     保存选项（写 settings.json）
  POST /api/convert  {ids}            开始转换（ids 为空 = 全部待转的）
  POST /api/stop                      当前文件转完后停止
  POST /api/preview  {id}             给某个文件准备预览（PMX 等会先快速转一份临时 VRM）
  POST /api/open     {path}           在资源管理器里显示文件
  POST /api/snapshot {name, data}     把预览截图存到模型旁边
  POST /api/tune     {id}             调整面板要的信息（可选表情、物理链、当前调整）
  POST /api/tune/save  {id, edits}    保存某个文件的调整，并套到它已有的输出 / 预览 VRM 上
  POST /api/tune/thumb {id, data}     把预览截图存成这个文件的 VRM 缩略图（data 为空 = 去掉）
  GET  /file/<token>                  预览要读的 VRM / 缩略图（只提供登记过的文件）

调整（表情指定、物理、标题、缩略图，见 convert/vrm_edit.py）按源文件路径存在 edits.json，
缩略图在 edits/ 里；以后再转这个文件会自动套上。

静态文件：/ → ui/web/，/viewer/ → ui/viewer/（three-vrm 预览组件和离线库）。
"""
import base64
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):                  # PyInstaller：资源在 _MEIPASS 下
    ROOT = sys._MEIPASS
else:
    ROOT = os.path.dirname(HERE)


def _writable(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write_test_%d" % os.getpid())
        with open(probe, "w") as f:
            f.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def _data_dir():
    """settings.json / edits.json 放哪：

    * 环境变量 MODEL2VRM_DATA（测试 / 调试用，不动用户自己的设置）；
    * 否则放程序旁边（exe 所在文件夹 / 源码根目录）——绿色版解压到哪都能带着设置走；
    * 程序旁边写不了（比如放进了 C:\\Program Files）时改放 %APPDATA%\\Model-To-VRM。
    """
    env = os.environ.get("MODEL2VRM_DATA")
    if env:
        return env
    beside = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ROOT
    if _writable(beside):
        return beside
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Model-To-VRM")


DATA_DIR = _data_dir()
for _p in ("convert", "formats"):
    _d = os.path.join(ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)
# Windows 超长路径（MAX_PATH 260）兼容层：要在其它模块 import 之前装上（导入即生效），
# Unity 资源包解出的深层目录树超过 260 字符时 open / makedirs 才不会报 FileNotFoundError
import longpath  # noqa: E402,F401
import pipeline  # noqa: E402
import vrm_edit  # noqa: E402
from version import __version__  # noqa: E402
from vrmconv import textures  # noqa: E402

WEB = os.path.join(ROOT, "ui", "web")
VIEWER = os.path.join(ROOT, "ui", "viewer")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
EDITS = os.path.join(DATA_DIR, "edits.json")
EDITS_DIR = os.path.join(DATA_DIR, "edits")
LANGS = ("zh_CN", "en")


class Backend:
    def __init__(self):
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.files = {}                 # id → 文件条目（插入顺序即列表顺序）
        self.events = []                # [(seq, event)]
        self.seq = itertools.count(1)
        self.tokens = {}                # token → 路径（预览可读的文件）
        self.busy = False
        self.stop_flag = False
        self.window = None              # pywebview 窗口（有的话用它的原生对话框）
        self.last_seen = time.time()
        self.tmp = tempfile.mkdtemp(prefix="model2vrm_preview_")
        self.opts, self.lang = self._load_settings()
        self.edits = self._load_edits()

    # --------------------------------------------------------- settings --
    def _load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            s = {}
        lang = s.get("lang") if s.get("lang") in LANGS else "zh_CN"
        return pipeline.merged(s.get("opts")), lang

    def _save_settings(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump({"lang": self.lang, "opts": self.opts}, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # ------------------------------------------------------------ edits --
    def _load_edits(self):
        try:
            with open(EDITS, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_edits(self):
        try:
            with open(EDITS, "w", encoding="utf-8") as f:
                json.dump(self.edits, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    @staticmethod
    def _key(f):
        return os.path.normcase(os.path.abspath(f["path"]))

    def edits_for(self, f):
        with self.lock:
            return json.loads(json.dumps(self.edits.get(self._key(f)) or {}))

    # ----------------------------------------------------------- events --
    def emit(self, **ev):
        with self.cond:
            self.events.append((next(self.seq), ev))
            if len(self.events) > 5000:
                del self.events[:1000]
            self.cond.notify_all()

    def wait_events(self, after, timeout=20.0):
        end = time.time() + timeout
        with self.cond:
            while True:
                new = [{"seq": s, **e} for s, e in self.events if s > after]
                left = end - time.time()
                if new or left <= 0:
                    return new
                self.cond.wait(left)

    def log(self, msg, tag=None, fid=None):
        self.emit(type="log", msg=str(msg), tag=tag, file=fid)

    # ------------------------------------------------------------ files --
    def _entry(self, f):
        e = {k: f[k] for k in ("id", "path", "name", "kind", "size", "status", "outputs", "error")}
        e["tuned"] = not vrm_edit.is_empty(self.edits.get(self._key(f)))
        return e

    def state(self):
        with self.lock:
            return {"files": [self._entry(f) for f in self.files.values()], "opts": self.opts,
                    "lang": self.lang, "busy": self.busy,
                    "seq": self.events[-1][0] if self.events else 0,
                    "native": self.window is not None, "version": __version__,
                    "features": {"pillow": textures.available()}}

    def add_paths(self, paths):
        added = []
        with self.lock:
            known = {os.path.normcase(os.path.abspath(f["path"])) for f in self.files.values()}
            todo = []
            for p in paths:
                if not p:
                    continue
                todo.extend(pipeline.find_inputs(p) if os.path.isdir(p) else [p])
            for p in todo:
                kind = pipeline.classify(p)
                key = os.path.normcase(os.path.abspath(p))
                if not kind or key in known or not os.path.isfile(p):
                    continue
                known.add(key)
                fid = uuid.uuid4().hex[:10]
                self.files[fid] = {"id": fid, "path": os.path.abspath(p), "name": os.path.basename(p),
                                   "kind": kind, "size": os.path.getsize(p),
                                   "status": "preview" if kind == "vrm" else "pending",
                                   "outputs": [], "error": None, "preview": None, "preview_path": None}
                added.append(fid)
        for fid in added:
            self.emit(type="file", file=self._entry(self.files[fid]))
        return added

    def remove(self, ids):
        with self.lock:
            for i in ids:
                f = self.files.get(i)
                if f and f["status"] != "running":
                    del self.files[i]
        self.emit(type="files")

    def clear(self):
        with self.lock:
            for i in [k for k, f in self.files.items() if f["status"] != "running"]:
                del self.files[i]
        self.emit(type="files")

    def set_options(self, opts=None, lang=None):
        with self.lock:
            if opts is not None:
                self.opts = pipeline.merged(opts)
            if lang in LANGS:
                self.lang = lang
            self._save_settings()

    def _update(self, fid, **kw):
        with self.lock:
            f = self.files.get(fid)
            if not f:
                return
            f.update(kw)
            entry = self._entry(f)
        self.emit(type="file", file=entry)

    def token_for(self, path):
        with self.lock:
            for t, p in self.tokens.items():
                if p == path:
                    return t
            t = uuid.uuid4().hex
            self.tokens[t] = path
            return t

    def _outputs(self, paths):
        return [{"path": p, "name": os.path.basename(p), "kind": pipeline.classify(p),
                 "url": "/file/%s/%s" % (self.token_for(p), urllib.parse.quote(os.path.basename(p)))
                 if p.lower().endswith(".vrm") else None} for p in paths]

    # ------------------------------------------------------- conversion --
    def convert(self, ids=None):
        with self.lock:
            if self.busy:
                return False
            cand = [f for f in self.files.values()
                    if (not ids or f["id"] in ids) and f["kind"] in pipeline.CONVERTIBLE
                    and f["status"] != "running"]
            # 默认只转还没转 / 转失败的；都转过了就全部重转
            todo = [f["id"] for f in cand if f["status"] in ("pending", "error")] or                 [f["id"] for f in cand]
            if not todo:
                return False
            self.busy, self.stop_flag = True, False
            opts = json.loads(json.dumps(self.opts))
        threading.Thread(target=self._run, args=(todo, opts), daemon=True).start()
        return True

    def _run(self, ids, opts):
        self.emit(type="busy", busy=True, total=len(ids))
        done = failed = 0
        try:
            for n, fid in enumerate(ids):
                with self.lock:
                    f = self.files.get(fid)
                    stop = self.stop_flag
                if stop:
                    self.log("已停止", "warn")
                    break
                if not f:
                    continue
                self._update(fid, status="running", error=None)
                self.emit(type="progress", index=n, total=len(ids), name=f["name"])
                t0 = time.time()
                try:
                    outs = pipeline.convert_file(f["path"], opts, log=lambda m, t=None: self.log(m, t, fid),
                                                 edits=self.edits_for(f))
                    self._update(fid, status="done", outputs=self._outputs(outs))
                    self.log("完成（%.1f 秒）" % (time.time() - t0), "ok", fid)
                    done += 1
                except Exception as e:  # noqa: BLE001  一个文件失败不影响其它
                    self.log(traceback.format_exc(), "err", fid)
                    self._update(fid, status="error", error="%s: %s" % (type(e).__name__, e))
                    failed += 1
        finally:
            with self.lock:
                self.busy = False
            self.emit(type="busy", busy=False, done=done, failed=failed)

    def stop(self):
        with self.lock:
            self.stop_flag = True

    # ---------------------------------------------------------- preview --
    def preview(self, fid):
        """→ {"url": …} 立即可看；或 {"pending": True}（后台快速转换完发 preview 事件）。"""
        with self.lock:
            f = self.files.get(fid)
            if not f:
                return {"error": "no such file"}
            if f["kind"] == "vrm":
                return {"url": self._outputs([f["path"]])[0]["url"], "name": f["name"]}
            vrms = [o for o in f["outputs"] if o.get("url")]
            if vrms:
                return {"url": vrms[-1]["url"], "name": vrms[-1]["name"]}
            if f.get("preview"):
                return {"url": f["preview"], "name": f["name"]}
            opts = json.loads(json.dumps(self.opts))
        opts["target"] = "vrm"
        out_dir = os.path.join(self.tmp, fid)

        def work():
            self.emit(type="preview_busy", file=fid)
            try:
                os.makedirs(out_dir, exist_ok=True)
                outs = pipeline.convert_file(f["path"], opts, log=lambda m, t=None: self.log(m, t, fid),
                                             out_dir=out_dir, edits=self.edits_for(f))
                vrm = [p for p in outs if p.lower().endswith(".vrm")][-1]
                url = self._outputs([vrm])[0]["url"]
                with self.lock:
                    if fid in self.files:
                        self.files[fid]["preview"] = url
                        self.files[fid]["preview_path"] = vrm
                self.emit(type="preview", file=fid, url=url, name=os.path.basename(vrm))
            except Exception as e:  # noqa: BLE001
                self.log(traceback.format_exc(), "err", fid)
                self.emit(type="preview", file=fid, error="%s: %s" % (type(e).__name__, e))

        threading.Thread(target=work, daemon=True).start()
        return {"pending": True}

    # ------------------------------------------------------------- tune --
    def _tune_targets(self, f):
        """这个文件已有的 VRM：输出的 + 预览用的临时文件。"""
        out = [o["path"] for o in f["outputs"] if o["path"].lower().endswith(".vrm")]
        if f.get("preview_path"):
            out.append(f["preview_path"])
        return [p for p in out if os.path.isfile(p)]

    def tune_info(self, fid):
        with self.lock:
            f = self.files.get(fid)
            if not f:
                return {"error": "no such file"}
            if f["kind"] not in pipeline.CONVERTIBLE:
                return {"editable": False}
            targets = self._tune_targets(f)
            edits = self.edits_for(f)
        if not targets:
            return {"editable": True, "ready": False, "edits": edits}
        try:
            info = vrm_edit.describe(targets[0])
        except (OSError, ValueError) as e:
            return {"error": str(e)}
        th = edits.get("thumbnail")
        info.update(editable=True, ready=True, edits=edits,
                    thumbnail=("/file/%s/thumb.png" % self.token_for(th)) if th and os.path.isfile(th) else None)
        return info

    def tune_save(self, fid, edits):
        with self.lock:
            f = self.files.get(fid)
            if not f or f["kind"] not in pipeline.CONVERTIBLE:
                return {"error": "no such file"}
            key = self._key(f)
            old = self.edits.get(key) or {}
            edits = dict(edits or {})
            if "thumbnail" not in edits and old.get("thumbnail"):
                edits["thumbnail"] = old["thumbnail"]      # 缩略图单独由 tune_thumb 设置
            if vrm_edit.is_empty(edits):
                self.edits.pop(key, None)
            else:
                self.edits[key] = edits
            self._save_edits()
            targets = self._tune_targets(f)
        applied = []
        for p in targets:
            try:
                vrm_edit.apply(p, edits)
                applied.append(p)
            except Exception as e:  # noqa: BLE001
                self.log("调整没能写进 %s：%s" % (os.path.basename(p), e), "err", fid)
        if applied:
            self.log("已把调整写进 %s" % "、".join(os.path.basename(p) for p in applied), "ok", fid)
        self._update(fid)
        return {"ok": True, "applied": len(applied)}

    def tune_thumb(self, fid, data):
        """data：PNG data URL；空 = 去掉缩略图。"""
        with self.lock:
            f = self.files.get(fid)
            if not f or f["kind"] not in pipeline.CONVERTIBLE:
                return {"error": "no such file"}
            edits = self.edits_for(f)
            path = None
            old = edits.get("thumbnail")
            if not data and old and os.path.dirname(os.path.abspath(old)) == os.path.abspath(EDITS_DIR):
                try:
                    os.remove(old)
                except OSError:
                    pass
            if data:
                os.makedirs(EDITS_DIR, exist_ok=True)
                path = os.path.join(EDITS_DIR, hashlib.md5(self._key(f).encode("utf-8")).hexdigest()[:16] + ".png")
                with open(path, "wb") as fh:
                    fh.write(base64.b64decode(data.split(",", 1)[1]))
            edits["thumbnail"] = path or ""
        r = self.tune_save(fid, edits)
        if path:
            r["thumbnail"] = "/file/%s/thumb.png?t=%d" % (self.token_for(path), int(time.time() * 1000))
        return r

    # ---------------------------------------------------------- dialogs --
    def pick(self, folder=False, output=False, uemodel=False):
        """系统对话框。pywebview 窗口用它自己的；浏览器模式起一个 tkinter 子进程。"""
        if self.window is not None:
            import webview
            if folder or output:
                r = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            elif uemodel:
                r = self.window.create_file_dialog(
                    webview.OPEN_DIALOG, file_types=("UEFormat (*.uemodel)", "All files (*.*)"))
            else:
                r = self.window.create_file_dialog(
                    webview.OPEN_DIALOG, allow_multiple=True,
                    file_types=("Models (*.pmx;*.vrm;*.zip;*.fbx;*.unitypackage;*.uemodel;*.psk;*.pskx;*.xps)",
                                "All files (*.*)"))
            return list(r or [])
        mode = "folder" if (folder or output) else ("uemodel" if uemodel else "files")
        cmd = ([sys.executable, "--dialog", mode] if getattr(sys, "frozen", False)
               else [sys.executable, os.path.join(HERE, "app.py"), "--dialog", mode])
        try:
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8", timeout=600)
            return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else []
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []

    def pick_ue_ref(self):
        """uemodel 对齐参考：拆开导出的部件按这个整身模型的骨架对齐。

        对齐是单向的（把部件搬进参考的空间），选反了（拿部件当参考）整个模型会掉下去十几单位，
        所以先看参考的骨架铺开有多大：整身通常 >120 cm，局部空间的部件只有几十 cm。
        """
        r = self.pick(uemodel=True)
        if not r:
            return {"path": ""}
        out = {"path": r[0]}
        try:
            import uemodel2pmx
            import uemodelio
            spread = uemodel2pmx._bone_spread(uemodelio.read_uemodel(r[0]))
            if spread and spread < 120.0:
                out["warn"] = "%.0f" % spread
        except Exception:  # noqa: BLE001  读不了就交给转换阶段处理
            pass
        return out

    def reveal(self, path):
        if not path or not os.path.exists(path):
            return False
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
            return True
        except OSError:
            return False

    def snapshot(self, name, data, model_url=None):
        folder = None
        with self.lock:
            if model_url:
                tok = model_url.split("/")[2] if model_url.startswith("/file/") else None
                p = self.tokens.get(tok)
                if p and not p.startswith(self.tmp):
                    folder = os.path.dirname(p)
        folder = folder or os.path.join(os.path.expanduser("~"), "Pictures")
        os.makedirs(folder, exist_ok=True)
        name = "".join(c for c in (name or "vrm") if c not in '\\/:*?"<>|') or "vrm"
        for n in range(1, 1000):
            out = os.path.join(folder, "%s_preview_%02d.png" % (name, n))
            if not os.path.exists(out):
                break
        with open(out, "wb") as f:
            f.write(base64.b64decode(data.split(",", 1)[1]))
        return out

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Handler(SimpleHTTPRequestHandler):
    backend = None

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def guess_type(self, path):
        if path.endswith((".js", ".mjs")):
            return "text/javascript"
        return super().guess_type(path)

    def translate_path(self, path):
        p = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        root, rel = (VIEWER, p[len("/viewer/"):]) if p.startswith("/viewer/") else (WEB, p.lstrip("/"))
        parts = [x for x in rel.split("/") if x not in ("", ".", "..")]
        return os.path.join(root, *parts) if parts else os.path.join(root, "index.html")

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        b = self.backend
        b.last_seen = time.time()
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/api/state":
            return self._json(b.state())
        if u.path == "/api/events":
            after = int(urllib.parse.parse_qs(u.query).get("after", ["0"])[0] or 0)
            return self._json({"events": b.wait_events(after)})
        if u.path == "/api/ping":
            return self._json({})
        if u.path.startswith("/file/"):
            tok = u.path.split("/")[2]
            p = b.tokens.get(tok)
            if not p or not os.path.isfile(p):
                return self.send_error(404)
            size = os.path.getsize(p)
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if p.lower().endswith(".png") else "model/gltf-binary")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(p, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            return
        return super().do_GET()

    def do_POST(self):
        b = self.backend
        b.last_seen = time.time()
        u = urllib.parse.urlsplit(self.path)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        route = u.path
        if route == "/api/add":
            return self._json({"added": b.add_paths(body.get("paths") or [])})
        if route == "/api/pick":
            return self._json({"added": b.add_paths(b.pick(folder=bool(body.get("folder"))))})
        if route == "/api/pick_ue_ref":
            return self._json(b.pick_ue_ref())
        if route == "/api/pick_output":
            r = b.pick(output=True)
            return self._json({"folder": r[0] if r else ""})
        if route == "/api/remove":
            b.remove(body.get("ids") or [])
            return self._json({})
        if route == "/api/clear":
            b.clear()
            return self._json({})
        if route == "/api/options":
            b.set_options(body.get("opts"), body.get("lang"))
            return self._json({"opts": b.opts, "lang": b.lang})
        if route == "/api/convert":
            return self._json({"started": b.convert(body.get("ids"))})
        if route == "/api/stop":
            b.stop()
            return self._json({})
        if route == "/api/preview":
            return self._json(b.preview(body.get("id")))
        if route == "/api/open":
            return self._json({"ok": b.reveal(body.get("path"))})
        if route == "/api/tune":
            return self._json(b.tune_info(body.get("id")))
        if route == "/api/tune/save":
            return self._json(b.tune_save(body.get("id"), body.get("edits")))
        if route == "/api/tune/thumb":
            return self._json(b.tune_thumb(body.get("id"), body.get("data")))
        if route == "/api/snapshot":
            return self._json({"path": b.snapshot(body.get("name"), body.get("data", ""), body.get("model"))})
        return self._json({"error": "unknown"}, 404)


def serve(port=0):
    """启动后端和 HTTP 服务（后台线程），返回 (backend, server, url)。"""
    backend = Backend()
    handler = type("BoundHandler", (Handler,), {"backend": backend})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return backend, srv, "http://127.0.0.1:%d/" % srv.server_address[1]
