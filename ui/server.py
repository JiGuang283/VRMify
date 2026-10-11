# -*- coding: utf-8 -*-
"""图形界面的后端：文件列表、选项、转换任务、事件推送，和一个只监听 127.0.0.1 的 HTTP 服务。

页面（ui/web/）只通过 HTTP 接口和这里打交道，所以 pywebview 窗口和普通浏览器用的是同一套：

  GET  /api/state                     文件列表、选项、语言、是否在转换
  GET  /api/events?after=N            长轮询：日志 / 文件状态 / 进度 / 预览就绪
  POST /api/add      {paths}          加文件（文件夹会递归展开）
  POST /api/pick     {folder}         弹系统对话框选文件 / 文件夹再加入
  POST /api/upload?key=&path=         浏览器模式：上传一个文件（请求体是文件内容），存进临时文件夹
  POST /api/upload/done {items}       浏览器模式：一批传完，把 [{key, roots}] 里的顶层文件 / 文件夹加入列表
  POST /api/pick_output               选输出文件夹，返回路径
  POST /api/pick_ue_ref               选 uemodel 的整身对齐参考，返回路径（参考像部件时带 warn）
  POST /api/remove   {ids} · /api/clear
  POST /api/options  {opts, lang, ui} 保存选项、界面语言、栏宽（写 settings.json）
  POST /api/convert  {ids}            开始转换（ids 为空 = 全部待转的）
  POST /api/stop                      当前文件转完后停止
  POST /api/preview  {id, output}     给某个文件准备预览（PMX 等会先快速转一份临时 VRM）；
                                      output = 输出列表里的序号时预览那个输出（PMX 输出同样先转临时 VRM）
  POST /api/open     {path}           在资源管理器里显示文件
  POST /api/snapshot {name, data}     把预览截图存到模型旁边
  POST /api/tune     {id, model}      调整面板要的信息（可选表情、物理链、当前调整；model = 预览中的模型）
  POST /api/tune/save  {id, edits}    保存某个文件的调整，并套到它已有的输出 / 预览 VRM 上
  POST /api/tune/thumb {id, data, auto, model} 把预览截图存成某个模型的 VRM 缩略图（data 为空 = 去掉）；
                                      auto：转换后自动截的，用户自己设过缩略图时不覆盖
  GET  /file/<token>                  预览要读的 VRM / 缩略图（只提供登记过的文件）
  GET  /model/<token>/<相对路径>       PMX 原生预览：PMX 和它旁边的贴图（只限登记过的模型文件夹；
                                      浏览器显示不了的 DDS 等现转 PNG）

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

    * 环境变量 VRMIFY_DATA（测试 / 调试用，不动用户自己的设置）；
    * 否则放程序旁边（exe 所在文件夹 / 源码根目录）——绿色版解压到哪都能带着设置走；
    * 程序旁边写不了（比如放进了 C:\\Program Files）时改放 %APPDATA%\\VRMify。以前叫 Model-To-VRM：
      旧文件夹还在、新的还没有时改名过去（设置、调整、输出都在里面），改不了名就接着用旧的。
    """
    env = os.environ.get("VRMIFY_DATA")
    if env:
        return env
    beside = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ROOT
    if _writable(beside):
        return beside
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    new, old = os.path.join(base, "VRMify"), os.path.join(base, "Model-To-VRM")
    if not os.path.exists(new) and os.path.isdir(old):
        try:
            os.rename(old, new)
        except OSError:
            return old
    return new


DATA_DIR = _data_dir()
for _p in ("convert", "formats"):
    _d = os.path.join(ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)
# Windows 超长路径（MAX_PATH 260）兼容层：要在其它模块 import 之前装上（导入即生效），
# Unity 资源包解出的深层目录树超过 260 字符时 open / makedirs 才不会报 FileNotFoundError
import longpath  # noqa: E402,F401
import logtext  # noqa: E402
import pipeline  # noqa: E402
import parallel  # noqa: E402
import vrm_edit  # noqa: E402
from version import __version__  # noqa: E402
import vrmio  # noqa: E402
from vrmconv import textures  # noqa: E402

WEB = os.path.join(ROOT, "ui", "web")
VIEWER = os.path.join(ROOT, "ui", "viewer")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
# 「程序目录」保存位置：程序旁边的 vrm 文件夹（和设置放一起：程序旁边写不了时在 %APPDATA%\VRMify 下）
APP_OUT = os.path.normpath(os.path.join(DATA_DIR, "vrm"))
_OLD_DEFAULT_OUTPUT = {"mode": "beside", "folder": "", "subfolder": True}
EDITS = os.path.join(DATA_DIR, "edits.json")
EDITS_DIR = os.path.join(DATA_DIR, "edits")
LANGS = ("zh_CN", "en")


def system_lang():
    """第一次启动（还没选过语言）时的界面语言：系统是中文就用中文，否则英文。"""
    try:
        if os.name == "nt":
            import ctypes
            return "zh_CN" if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x04 else "en"
        import locale
        loc = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG")             or (locale.getlocale()[0] or "")
        return "zh_CN" if loc.lower().startswith("zh") else "en"
    except Exception:  # noqa: BLE001
        return "zh_CN"


TMP_PREFIXES = ("vrmify_", "model2vrm_")     # 后一个是改名前（Model-To-VRM）的版本留下的
# 浏览器模式下拖入 / 选择的文件：网页拿不到磁盘路径，内容传到这里再加入列表（一天没用会被 _sweep_stale_previews 删掉）
UPLOADS = os.path.join(tempfile.gettempdir(), "vrmify_uploads")
_BAD_CHARS = set('\\/:*?"<>|') | {chr(i) for i in range(32)}


def _safe_name(s):
    s = "".join("_" if c in _BAD_CHARS else c for c in s).strip().rstrip(". ")
    return s if s not in ("", ".", "..") else "_"



def _sweep_stale_previews(max_age=86400):
    """删掉以前遗留的预览临时目录：正常退出时会删，程序崩溃或被强制结束时会留下来（每个几十 MB）。
    只删一天以前的——同时开着的另一个窗口的目录还在用。"""
    root = tempfile.gettempdir()
    now = time.time()
    try:
        names = os.listdir(root)
    except OSError:
        return
    for n in names:
        p = os.path.join(root, n)
        try:
            if not (n.startswith(TMP_PREFIXES) and os.path.isdir(p)):
                continue
            # 看目录本身和下一层的最近修改时间（预览文件在下一层的子目录里）
            newest = max([os.path.getmtime(p)] + [os.path.getmtime(os.path.join(p, c)) for c in os.listdir(p)])
            if now - newest > max_age:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


# 界面布局（拖动分隔条调的栏宽，像素）：left 左栏、side 右侧面板栏（调整面板 sideWide）
_UI_LIMITS = {"left": (300, 900), "side": (220, 900), "sideWide": (280, 900)}


def _clean_ui(ui):
    out = {}
    for k, (lo, hi) in _UI_LIMITS.items():
        v = (ui or {}).get(k) if isinstance(ui, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = int(min(hi, max(lo, v)))
    return out


class Backend:
    def __init__(self):
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.files = {}                 # id → 文件条目（插入顺序即列表顺序）
        self.events = []                # [(seq, event)]
        self.seq = itertools.count(1)
        self.tokens = {}                # token → 路径（预览可读的文件）
        self.dirs = {}                  # token → 文件夹（PMX 原生预览：模型和贴图都从这里读）
        self.finders = {}               # token → vrmio.TextureFinder（贴图不在 PMX 记的位置时按文件名找）
        self.busy = False
        self.stop_flag = False
        self.window = None              # pywebview 窗口（有的话用它的原生对话框）
        self.mode_note = None           # 退到浏览器模式的原因 {zh, en, log}（app.py 设置，页面顶上显示）
        self.last_seen = time.time()
        _sweep_stale_previews()
        self.tmp = tempfile.mkdtemp(prefix="vrmify_preview_")
        self.opts, self.lang, self.ui = self._load_settings()
        self.edits = self._load_edits()

    # --------------------------------------------------------- settings --
    def _load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            s = {}
        lang = s.get("lang") if s.get("lang") in LANGS else system_lang()
        opts = pipeline.merged(s.get("opts"))
        # 界面的默认保存位置是程序目录下的 vrm。以前的设置文件（没有 v）里还是旧默认值（源文件旁）的，
        # 多半是没选过，换成新默认；自己选过别的（指定文件夹、关了子文件夹）的不动
        saved = (s.get("opts") or {}).get("output")
        if s.get("v", 1) < 2 and (saved is None or saved == _OLD_DEFAULT_OUTPUT):
            opts["output"]["mode"] = "app"
        return opts, lang, _clean_ui(s.get("ui"))

    def _save_settings(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump({"v": 2, "lang": self.lang, "opts": self.opts, "ui": self.ui}, f, ensure_ascii=False, indent=1)
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

    @staticmethod
    def _user_edits(e):
        """去掉自动生成的缩略图后的调整：判断「调整过」、关掉自动缩略图时转换用。"""
        e = dict(e or {})
        if e.pop("thumbnail_auto", None):
            e.pop("thumbnail", None)
        ths = {k: t for k, t in (e.get("thumbnails") or {}).items() if not (t or {}).get("auto")}
        if ths:
            e["thumbnails"] = ths
        else:
            e.pop("thumbnails", None)
        return e

    def _convert_edits(self, f):
        """转换时套用的调整：关掉「自动生成缩略图」后，以前自动截的那张不再写进去。"""
        e = self.edits_for(f)
        return e if self.opts["vrm"].get("auto_thumb", True) else self._user_edits(e)

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
        """日志按中文生成，en 是英文版（logtext；对照表里没有的为 None，界面就显示中文）。"""
        msg = str(msg)
        self.emit(type="log", msg=msg, en=logtext.to_en(msg), tag=tag, file=fid)

    # ------------------------------------------------------------ files --
    def _entry(self, f):
        e = {k: f[k] for k in ("id", "path", "name", "kind", "size", "status", "outputs", "error")}
        e["error_en"] = logtext.to_en(f["error"]) if f.get("error") else None
        e["tuned"] = not vrm_edit.is_empty(self._user_edits(self.edits.get(self._key(f))))
        return e

    def state(self):
        with self.lock:
            return {"files": [self._entry(f) for f in self.files.values()], "opts": self.opts,
                    "lang": self.lang, "busy": self.busy, "ui": self.ui,
                    "seq": self.events[-1][0] if self.events else 0,
                    "native": self.window is not None, "version": __version__,
                    "features": {"pillow": textures.available(), "auto_jobs": parallel.auto_jobs()},
                    "app_out": APP_OUT, "mode_note": self.mode_note}

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

    def set_options(self, opts=None, lang=None, ui=None):
        reopen = []
        with self.lock:
            if opts is not None:
                was = self.opts["vrm"].get("avatar_only")
                self.opts = pipeline.merged(opts)
                if was and not self.opts["vrm"]["avatar_only"]:
                    # 关掉「只转换人物」：之前因此跳过的文件回到「待转换」，点开始就会转
                    reopen = [i for i, f in self.files.items() if f["status"] == "skipped"]
            if lang in LANGS:
                self.lang = lang
            if ui is not None:
                self.ui = _clean_ui(dict(self.ui, **ui) if isinstance(ui, dict) else None)
            self._save_settings()
        for fid in reopen:
            self._update(fid, status="pending")

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

    def dir_url(self, path):
        """PMX 的原生预览地址：/model/<文件夹 token>/<文件名>，贴图按 PMX 里的相对路径从同一文件夹读。"""
        folder = os.path.dirname(os.path.abspath(path))
        with self.lock:
            tok = next((t for t, d in self.dirs.items() if d == folder), None)
            if tok is None:
                tok = uuid.uuid4().hex
                self.dirs[tok] = folder
        return "/model/%s/%s" % (tok, urllib.parse.quote(os.path.basename(path)))

    def _outputs(self, paths):
        return [{"path": p, "name": os.path.basename(p), "kind": pipeline.classify(p),
                 "url": "/file/%s/%s" % (self.token_for(p), urllib.parse.quote(os.path.basename(p)))
                 if p.lower().endswith(".vrm") else None,
                 "previewable": p.lower().endswith((".vrm", ".pmx"))} for p in paths]

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
            opts["output"]["app_folder"] = APP_OUT
        threading.Thread(target=self._run, args=(todo, opts), daemon=True).start()
        return True

    def _run(self, ids, opts):
        """转换 ids 里的文件。几个文件时按 parallel.jobs_for 同时转（子进程），一个文件就在本进程里转。
        「停止」：已经在转的转完，还没开始的不再开始。"""
        self.emit(type="busy", busy=True, total=len(ids))
        todo = []
        for fid in ids:
            with self.lock:
                f = self.files.get(fid)
            if f:
                todo.append((fid, f["path"], f["name"], self._convert_edits(f), self._opts_for(f["path"], opts)))
        total = len(todo)
        jobs = parallel.jobs_for(total, opts.get("jobs"), [pipeline.memory_estimate(p) for _, p, _, _, _ in todo])
        st = {"done": 0, "failed": 0, "skipped": 0, "finished": 0}
        running, t0 = {}, {}

        def progress():
            self.emit(type="progress", index=st["finished"], total=total, name="、".join(running.values()))

        def start(fid):
            t0[fid] = time.time()
            running[fid] = next(n for i, _, n, _, _ in todo if i == fid)
            self._update(fid, status="running", error=None)
            progress()

        def finish(fid, ok, res):
            running.pop(fid, None)
            st["finished"] += 1
            if ok and res:
                self._update(fid, status="done", outputs=self._outputs(res))
                self.log("完成，用时 %.1f 秒" % (time.time() - t0[fid]), "ok", fid)
                st["done"] += 1
            elif ok:                                 # 里面的模型都被「只转换人物」跳过了（原因在日志里）
                self._update(fid, status="skipped", outputs=[])
                st["skipped"] += 1
            else:
                summary, tb = res
                self.log(tb or summary, "err", fid)
                self._update(fid, status="error", error=summary)
                st["failed"] += 1
            progress()

        def stopped():
            with self.lock:
                return self.stop_flag

        try:
            if jobs > 1:
                self.log("并行转换 %d 个文件（共 %d 个）" % (jobs, total), "info")
                parallel.run([(fid, pipeline.convert_file, (path, fopts), {"edits": edits})
                              for fid, path, _, edits, fopts in todo], jobs,
                             lambda fid: (lambda m, t=None: self.log(m, t, fid)),
                             start=start, done=finish, should_stop=stopped)
            else:
                for fid, path, _, edits, fopts in todo:
                    if stopped():
                        break
                    start(fid)
                    try:
                        outs = pipeline.convert_file(path, fopts, log=lambda m, t=None, fid=fid: self.log(m, t, fid),
                                                     edits=edits)
                        finish(fid, True, outs)
                    except Exception as e:  # noqa: BLE001  一个文件失败不影响其它
                        finish(fid, False, ("%s: %s" % (type(e).__name__, e), traceback.format_exc()))
            if st["finished"] < total:
                self.log("已停止，%d 个文件未转换" % (total - st["finished"]), "warn")
        finally:
            with self.lock:
                self.busy = False
            self.emit(type="busy", busy=False, done=st["done"], failed=st["failed"], skipped=st["skipped"])

    def stop(self):
        with self.lock:
            self.stop_flag = True

    @staticmethod
    def _uploaded(path):
        try:
            return os.path.commonpath([UPLOADS, os.path.abspath(path)]) == UPLOADS
        except ValueError:
            return False

    def _opts_for(self, path, opts):
        """浏览器模式传进来的文件在临时文件夹里：「保存在源文件旁边」时改存到程序目录的 vrm 文件夹。"""
        if opts["output"].get("mode") == "beside" and self._uploaded(path):
            return dict(opts, output=dict(opts["output"], mode="app"))
        return opts

    # ---------------------------------------------------------- uploads --
    @staticmethod
    def _upload_dir(key):
        return os.path.join(UPLOADS, hashlib.md5(str(key).encode("utf-8")).hexdigest()[:12])

    def upload(self, key, rel, stream, size):
        """浏览器模式：页面把拖入 / 选择的文件逐个传过来（请求体就是文件内容），存到 UPLOADS/<key 的哈希>/<相对路径>。
        key 由页面按名字、大小、修改时间算：同样的东西再传一次落在同一个位置（调整按源文件路径保存，下次还能套上），
        已经在了就不重写。读满 size 字节再返回，连接才能接着用。"""
        parts = [_safe_name(x) for x in str(rel or "").replace("\\", "/").split("/") if x not in ("", ".", "..")]
        dest = os.path.join(self._upload_dir(key), *parts) if parts and key else None
        if dest and os.path.isfile(dest) and os.path.getsize(dest) == size:
            dest, keep = None, dest
        else:
            keep = None
        tmp = dest + ".part" if dest else None
        left = size
        try:
            if tmp:
                os.makedirs(os.path.dirname(tmp), exist_ok=True)
            with (open(tmp, "wb") if tmp else open(os.devnull, "wb")) as f:
                while left > 0:
                    chunk = stream.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                raise OSError("上传中断")
            if tmp:
                os.replace(tmp, dest)
        except OSError as e:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
            return {"error": str(e)}
        if not (dest or keep):
            return {"error": "bad path"}
        return {"ok": True}

    def upload_done(self, items):
        """一批文件传完：items = [{key, roots}]，roots 是这批里顶层的文件 / 文件夹名，把它们加进列表。"""
        paths, lone_pmx = [], []
        for it in items or []:
            d = self._upload_dir(it.get("key"))
            mine = [os.path.join(d, _safe_name(r)) for r in it.get("roots") or []]
            mine = [p for p in mine if os.path.exists(p)]
            paths += mine
            # 只传了 PMX 本身（没有文件夹、也没有图片跟着）：浏览器给不了它旁边的贴图
            if not any(os.path.isdir(p) or p.lower().endswith(vrmio.IMAGE_EXTS) for p in mine):
                lone_pmx += [os.path.basename(p) for p in mine if p.lower().endswith(".pmx")]
        added = self.add_paths(paths)
        if lone_pmx:
            self.log("浏览器模式下只加入了 %s 本身，旁边的贴图没有一起传过来，转出来可能没有贴图："
                     "请拖入整个模型文件夹或压缩包" % "、".join(lone_pmx), "warn")
        return added

    # ---------------------------------------------------------- preview --
    def preview(self, fid, output=None):
        """→ {"url": …} 立即可看；或 {"pending": True}（后台快速转换完发 preview 事件）。

        预览的对象：指定了 output（输出列表里的序号）就是那个输出；否则依次是输出的 VRM、
        输出的 PMX（「只转 PMX」时）、源文件本身。PMX 直接原生预览（three.js 的 MMDLoader，
        MMD 的着色、IK、物理）；FBX 等其它格式先快速转一份临时 VRM，按「文件 + 修改时间」缓存。
        """
        with self.lock:
            f = self.files.get(fid)
            if not f:
                return {"error": "no such file"}
            if f["kind"] == "vrm":
                return {"url": self._outputs([f["path"]])[0]["url"], "name": f["name"]}
            outs = f["outputs"]
            if output is not None:
                if not (0 <= int(output) < len(outs)) or not outs[int(output)].get("previewable"):
                    return {"error": "not previewable"}
                o = outs[int(output)]
                if o.get("url"):
                    return {"url": o["url"], "name": o["name"]}
                src = o["path"]
            else:
                vrms = [o for o in outs if o.get("url")]
                if vrms:
                    return {"url": vrms[-1]["url"], "name": vrms[-1]["name"]}
                pmxs = [o for o in outs if o.get("previewable") and os.path.isfile(o["path"])]
                src = pmxs[-1]["path"] if pmxs else f["path"]
            if src.lower().endswith(".pmx"):
                if not os.path.isfile(src):
                    return {"error": "file not found"}
                return {"url": self.dir_url(src), "name": os.path.basename(src)}
            try:
                key = "%s|%s" % (os.path.normcase(src), os.path.getmtime(src))
            except OSError:
                return {"error": "file not found"}
            cache = f.setdefault("previews", {})
            if key in cache and os.path.isfile(cache[key][1]):
                url, vrm = cache[key]
                f["preview"], f["preview_path"] = url, vrm
                return {"url": url, "name": os.path.basename(src)}
            opts = json.loads(json.dumps(self.opts))
        opts["target"] = "vrm"
        out_dir = os.path.join(self.tmp, fid, hashlib.md5(key.encode("utf-8")).hexdigest()[:10])
        name = os.path.basename(src)

        def work():
            self.emit(type="preview_busy", file=fid)
            try:
                os.makedirs(out_dir, exist_ok=True)
                res = pipeline.convert_file(src, opts, log=lambda m, t=None: self.log(m, t, fid),
                                            out_dir=out_dir, edits=self.edits_for(f))
                if not res and opts["vrm"].get("avatar_only"):
                    # 里面没有人物（道具、只有衣服），被「只转换人物」跳过了：预览还是转一份来看
                    self.log("预览：未找到人物模型，忽略「只转换人物」生成预览", "info", fid)
                    opts["vrm"]["avatar_only"] = False
                    res = pipeline.convert_file(src, opts, log=lambda m, t=None: self.log(m, t, fid),
                                                out_dir=out_dir, edits=self.edits_for(f))
                vrm = [p for p in res if p.lower().endswith(".vrm")][-1]
                url = self._outputs([vrm])[0]["url"]
                with self.lock:
                    if fid in self.files:
                        ff = self.files[fid]
                        ff.setdefault("previews", {})[key] = (url, vrm)
                        ff["preview"], ff["preview_path"] = url, vrm
                self.emit(type="preview", file=fid, url=url, name=name)
            except Exception as e:  # noqa: BLE001
                self.log(traceback.format_exc(), "err", fid)
                err = "%s: %s" % (type(e).__name__, e)
                self.emit(type="preview", file=fid, error=err, error_en=logtext.to_en(err))

        threading.Thread(target=work, daemon=True).start()
        return {"pending": True}

    # ------------------------------------------------------------- tune --
    def _tune_targets(self, f):
        """这个文件已有的 VRM：输出的 + 预览用的临时文件。"""
        out = [o["path"] for o in f["outputs"] if o["path"].lower().endswith(".vrm")]
        if f.get("preview_path"):
            out.append(f["preview_path"])
        return [p for p in out if os.path.isfile(p)]

    def tune_info(self, fid, model=None):
        """model：预览里显示的模型（vrm_edit.model_key），一个包里有几个模型时按它取信息和缩略图。"""
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
        if model:
            targets.sort(key=lambda p: vrm_edit.model_key(p) != model)
        try:
            info = vrm_edit.describe(targets[0])
        except (OSError, ValueError) as e:
            return {"error": str(e), "error_en": logtext.to_en(str(e))}
        key = vrm_edit.model_key(targets[0])
        th = vrm_edit.thumb_for(edits, targets[0])
        own = (edits.get("thumbnails") or {}).get(key)
        auto = own.get("auto") if own else edits.get("thumbnail_auto")
        ok = bool(th and os.path.isfile(th))
        info.update(editable=True, ready=True, edits=edits, model=key, thumbnail_auto=bool(ok and auto),
                    thumbnail=("/file/%s/thumb.png" % self.token_for(th)) if ok else None)
        return info

    def tune_save(self, fid, edits, done_msg=None, only=None, keep_thumbs=True):
        """only：只套到这些模型（model_key）的 VRM 上。keep_thumbs：edits 里没有缩略图时沿用已存的
        （缩略图单独由 tune_thumb 设置）。"""
        with self.lock:
            f = self.files.get(fid)
            if not f or f["kind"] not in pipeline.CONVERTIBLE:
                return {"error": "no such file"}
            key = self._key(f)
            old = self.edits.get(key) or {}
            edits = dict(edits or {})
            if keep_thumbs:
                for k in ("thumbnail", "thumbnail_auto", "thumbnails"):
                    if k not in edits and old.get(k):
                        edits[k] = old[k]
            if vrm_edit.is_empty(edits):
                self.edits.pop(key, None)
            else:
                self.edits[key] = edits
            self._save_edits()
            targets = [p for p in self._tune_targets(f) if only is None or vrm_edit.model_key(p) in only]
        applied = []
        for p in targets:
            try:
                vrm_edit.apply(p, edits)
                applied.append(p)
            except Exception as e:  # noqa: BLE001
                self.log("调整写入 %s 失败：%s" % (os.path.basename(p), e), "err", fid)
        if applied:
            self.log((done_msg or "调整已写入 %s") % "、".join(os.path.basename(p) for p in applied), "ok", fid)
        self._update(fid)
        return {"ok": True, "applied": len(applied)}

    def tune_thumb(self, fid, data, auto=False, model=None):
        """data：PNG data URL；空 = 去掉缩略图。model：给哪个模型（vrm_edit.model_key；不给 = 第一个）。

        缩略图按模型存（edits["thumbnails"]），一个包里几个模型各用各的，只写进那个模型的 VRM。
        auto：转换后自动截的（界面的「自动生成缩略图」）。记成 auto，下次转换后重新截；
        用户自己给这个模型设过缩略图（不是 auto）时不覆盖。
        """
        with self.lock:
            f = self.files.get(fid)
            if not f or f["kind"] not in pipeline.CONVERTIBLE:
                return {"error": "no such file"}
            edits = self.edits_for(f)
            keys = list(dict.fromkeys(vrm_edit.model_key(p) for p in self._tune_targets(f)))
            model = model or (keys[0] if keys else os.path.splitext(f["name"])[0])
            ths = edits.setdefault("thumbnails", {})
            legacy, legacy_auto = edits.pop("thumbnail", None), edits.pop("thumbnail_auto", None)
            if legacy:                      # 旧版按文件存的一张：分给各个模型，以后都按模型存
                for k in keys + [model]:
                    ths.setdefault(k, {"path": legacy, "auto": bool(legacy_auto)})
            cur = ths.get(model) or {}
            if auto and (not data or (cur.get("path") and not cur.get("auto"))):
                return {"skipped": True}
            path = None
            if data:
                os.makedirs(EDITS_DIR, exist_ok=True)
                name = hashlib.md5(("%s|%s" % (self._key(f), model)).encode("utf-8")).hexdigest()[:16]
                path = os.path.join(EDITS_DIR, name + ".png")
                with open(path, "wb") as fh:
                    fh.write(base64.b64decode(data.split(",", 1)[1]))
                ths[model] = {"path": path, "auto": bool(auto)}
            else:
                ths.pop(model, None)
            old = cur.get("path")
            if (old and old != path and os.path.dirname(os.path.abspath(old)) == os.path.abspath(EDITS_DIR)
                    and not any((t or {}).get("path") == old for t in ths.values())):
                try:
                    os.remove(old)
                except OSError:
                    pass
            if not ths:
                del edits["thumbnails"]
        r = self.tune_save(fid, edits, done_msg="缩略图已自动生成并写入 %s" if auto else None,
                           only=[model], keep_thumbs=False)
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
                    file_types=("Models (*.pmx;*.vrm;*.zip;*.7z;*.rar;*.tar;*.tgz;*.gz;*.bz2;*.xz;*.fbx;*.unitypackage;*.uemodel;*.psk;*.pskx;*.xps)",
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
        if path.endswith(".wasm"):              # ammo.js 用 instantiateStreaming，类型不对会退回慢路径
            return "application/wasm"
        return super().guess_type(path)

    def _send_bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _model_file(self, tok, rel):
        """/model/<token>/<rel>：只给登记过的模型文件夹里的文件；图片按内容给类型，DDS 等现转 PNG。"""
        folder = self.backend.dirs.get(tok)
        if not folder:
            return self.send_error(404)
        rel = urllib.parse.unquote(rel).replace("\\", "/")
        p = os.path.normpath(os.path.join(folder, *[x for x in rel.split("/") if x not in ("", ".")]))
        try:
            inside = os.path.commonpath([folder, p]) == folder
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(p):
            # 贴图不在 PMX 记的位置：和转换时一样按文件名在模型文件夹里找（vrmio.TextureFinder）
            import vrmio
            with self.backend.lock:
                finder = self.backend.finders.get(tok)
                if finder is None:
                    finder = self.backend.finders[tok] = vrmio.TextureFinder(folder)
            p = finder.find(rel.rsplit("/", 1)[-1])[0] if rel.lower().endswith(vrmio.IMAGE_EXTS) else None
            try:
                inside = bool(p) and os.path.commonpath([folder, os.path.abspath(p)]) == folder
            except ValueError:
                inside = False
            if not inside:
                return self.send_error(404)
        with open(p, "rb") as f:
            data = f.read()
        low = p.lower()
        if low.endswith(".pmx"):
            return self._send_bytes(data, "application/octet-stream")
        if low.endswith(".tga"):                # MMDLoader 自己用 TGALoader 解
            return self._send_bytes(data, "image/x-tga")
        head = data[:8]
        if head.startswith(b"\x89PNG"):
            return self._send_bytes(data, "image/png")
        if head.startswith(b"\xff\xd8\xff"):
            return self._send_bytes(data, "image/jpeg")
        if head.startswith(b"BM"):              # .bmp / .spa / .sph（球面贴图其实是 BMP）
            return self._send_bytes(data, "image/bmp")
        if head.startswith((b"GIF8", b"RIFF")):
            return self._send_bytes(data, "image/gif" if head.startswith(b"GIF8") else "image/webp")
        try:                                    # DDS 等浏览器不认的：现转 PNG
            import vrmio
            png = vrmio.decode_image(data)
        except Exception:  # noqa: BLE001
            png = None
        if png:
            return self._send_bytes(png, "image/png" if png[:4] == b"\x89PNG" else "image/jpeg")
        return self._send_bytes(data, "application/octet-stream")

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
        if u.path.startswith("/model/"):
            parts = u.path.split("/", 3)
            return self._model_file(parts[2], parts[3] if len(parts) > 3 else "")
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
        if u.path == "/api/upload":             # 请求体是文件内容，不是 JSON
            q = urllib.parse.parse_qs(u.query)
            return self._json(b.upload(q.get("key", [""])[0], q.get("path", [""])[0], self.rfile,
                                       int(self.headers.get("Content-Length", 0) or 0)))
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        route = u.path
        if route == "/api/add":
            return self._json({"added": b.add_paths(body.get("paths") or [])})
        if route == "/api/upload/done":
            return self._json({"added": b.upload_done(body.get("items"))})
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
            b.set_options(body.get("opts"), body.get("lang"), body.get("ui"))
            return self._json({"opts": b.opts, "lang": b.lang})
        if route == "/api/convert":
            return self._json({"started": b.convert(body.get("ids"))})
        if route == "/api/stop":
            b.stop()
            return self._json({})
        if route == "/api/preview":
            return self._json(b.preview(body.get("id"), body.get("output")))
        if route == "/api/open":
            return self._json({"ok": b.reveal(body.get("path"))})
        if route == "/api/tune":
            return self._json(b.tune_info(body.get("id"), body.get("model")))
        if route == "/api/tune/save":
            return self._json(b.tune_save(body.get("id"), body.get("edits")))
        if route == "/api/tune/thumb":
            return self._json(b.tune_thumb(body.get("id"), body.get("data"), bool(body.get("auto")), body.get("model")))
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
