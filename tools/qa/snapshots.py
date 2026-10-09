# -*- coding: utf-8 -*-
"""表情截图对照表：每个模型一行——全身、脸、各个表情预设、口型、眨眼、视线四个方向。

    python tools/qa/snapshots.py a.vrm -o a.png
    python tools/qa/snapshots.py 旧版/a.vrm 新版/a.vrm -o 对比.png        一个输入一行，上下对照
    python tools/qa/snapshots.py model/角色/角色.pmx -o a.png              非 VRM 先用当前代码转一份（临时目录）
    python tools/qa/snapshots.py a.vrm --custom -o a.png                   再加上模型自己的自定义表情

用无界面的 Edge / Chrome 渲染，渲染部分就是界面预览用的 ui/viewer/viewer-core.js（three-vrm + MToon），
和程序里看到的一样。改表情相关代码前后各拍一张，比对哪里变了；也可以把 tools/regress.py 的样本拍下来看。
模型自己没有的表情格子标「无」，不会拿中性脸冒充。

需要本机装了 Edge 或 Chrome（--browser 指定路径）。WebGL 走软件渲染（SwiftShader），不依赖显卡。
"""
import argparse
import base64
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
VIEWER = os.path.join(ROOT, "ui", "viewer")
for _p in ("convert", "formats"):
    _d = os.path.join(ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

# 列：[名字, 机位, 表情 {名字: 值}, 视线 (yaw, pitch) 或 None]
SHOTS = [["全身", "body", {}, None], ["脸", "face", {}, None]]
SHOTS += [[n, "face", {n: 1.0}, None] for n in ("happy", "angry", "sad", "relaxed", "surprised",
                                                  "aa", "ih", "ou", "ee", "oh", "blink", "blinkLeft", "blinkRight")]
SHOTS += [["look" + k, "face", {}, v] for k, v in
          (("Up", (0, 90)), ("Down", (0, -90)), ("Left", (90, 0)), ("Right", (-90, 0)))]

PAGE = r"""<!doctype html><meta charset="utf-8">
<script type="importmap">{ "imports": {
  "three": "/viewer/vendor/three/three.module.min.js",
  "three/addons/": "/viewer/vendor/three/addons/",
  "@pixiv/three-vrm": "/viewer/vendor/three-vrm/three-vrm.module.min.js" } }</script>
<body style="margin:0;background:#222">
<script type="module">
import { Viewer } from '/viewer/viewer-core.js';
const job = await (await fetch('/job')).json();
const send = (o) => fetch('/result', { method: 'POST', body: JSON.stringify(o) });
try {
  const S = job.size, PAD = 6, LEFT = 150;
  const box = document.createElement('div');
  box.style.cssText = `width:${S}px;height:${S}px`;
  document.body.append(box);
  const v = new Viewer(box, { background: '#8a8f98' });
  v.paused = true;
  const rows = [];
  for (const m of job.models) {
    await v.loadUrl(m.url, m.name);
    const em = v.vrm.expressionManager, la = v.vrm.lookAt;
    const PRESET = new Set(['happy', 'angry', 'sad', 'relaxed', 'surprised', 'aa', 'ih', 'ou', 'ee', 'oh', 'blink',
      'blinkLeft', 'blinkRight', 'lookUp', 'lookDown', 'lookLeft', 'lookRight', 'neutral']);
    const custom = job.custom && em ? em.expressions.map((e) => e.expressionName).filter((n) => !PRESET.has(n)) : [];
    const shots = job.shots.concat(custom.map((n) => [n, 'face', { [n]: 1 }, null]));
    v.motion = 'idle'; v._applyMotion(0);
    v.vrm.update(1 / 60);
    const cells = [];
    for (const [label, cam, expr, look] of shots) {
      const missing = Object.keys(expr).find((n) => !(em && em.getExpression(n))) || (look && !(la && la.applier));
      v.resetExpressions();
      for (const [n, w] of Object.entries(expr)) if (!missing) v.setExpression(n, w);
      if (look && !missing) v.setLook(look[0], look[1]); else v.setLook(null);
      v.vrm.update(0);
      // 比界面预览的机位更紧：脸占满格子、全身刚好放下（竖直视角 30°）
      const H = v.height, h = v.headY, cy = v.centerY ?? H * 0.52;
      const [pos, tgt] = cam === 'face' ? [[0, h - H * 0.02, H * 0.34], [0, h - H * 0.024, 0]]
        : [[0, cy, H * 2.1], [0, cy, 0]];
      v.camera.position.set(...pos); v.controls.target.set(...tgt); v.controls.update();
      const c = document.createElement('canvas'); c.width = c.height = S;
      const g = c.getContext('2d');
      v.renderer.render(v.scene, v.camera);
      g.drawImage(v.renderer.domElement, 0, 0, S, S);
      if (missing) { g.fillStyle = 'rgba(0,0,0,.55)'; g.fillRect(0, 0, S, S); g.fillStyle = '#fff'; g.font = `bold ${S / 6}px sans-serif`; g.textAlign = 'center'; g.fillText('无', S / 2, S / 2 + S / 16); }
      cells.push([label, c]);
    }
    v.setLook(null);
    rows.push([m.label, cells]);
  }
  // 每 job.cols 列一段；每段里每个模型一行。每格自带标题（各模型的自定义表情不一样，不能共用表头）
  const CAP = 20, ncol = Math.max(...rows.map((r) => r[1].length)), cols = Math.min(job.cols, ncol);
  const bands = Math.ceil(ncol / cols), rowH = S + CAP + PAD;
  const W = LEFT + cols * (S + PAD) + PAD, H = PAD + bands * (rows.length * rowH + PAD);
  const out = document.createElement('canvas'); out.width = W; out.height = H;
  const g = out.getContext('2d');
  g.fillStyle = '#222'; g.fillRect(0, 0, W, H);
  g.font = '14px sans-serif'; g.textBaseline = 'middle';
  for (let b = 0; b < bands; b++) {
    rows.forEach(([label, cells], r) => {
      const y = PAD + b * (rows.length * rowH + PAD) + r * rowH;
      g.textAlign = 'left'; g.fillStyle = '#ddd';
      const lines = [''];                                 // 按实际宽度折行
      for (const ch of label) {
        if (g.measureText(lines[lines.length - 1] + ch).width > LEFT - 14) lines.push('');
        lines[lines.length - 1] += ch;
      }
      lines.slice(0, Math.floor(S / 18)).forEach((w, k) => g.fillText(w, 8, y + CAP + 12 + k * 18));
      cells.slice(b * cols, (b + 1) * cols).forEach(([name, c], i) => {
        const x = LEFT + PAD + i * (S + PAD);
        g.fillStyle = '#ddd'; g.textAlign = 'center';
        let t = name;
        while (t.length > 1 && g.measureText(t).width > S - 4) t = t.slice(0, -1);
        g.fillText(t === name ? t : t.slice(0, -1) + '…', x + S / 2, y + CAP / 2);
        g.drawImage(c, x, y + CAP);
      });
    });
  }
  await send({ png: out.toDataURL('image/png') });
} catch (e) { await send({ error: String(e && e.stack || e) }); }
</script>
"""


def find_browser(path=None):
    cands = [path] if path else []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    cands += [shutil.which(n) for n in ("msedge", "chrome", "google-chrome", "chromium")]
    return next((c for c in cands if c and os.path.isfile(c)), None)


# ---- 浏览器进程的清理
# 只结束浏览器主进程是不够的：Windows 上它的子进程（GPU、渲染进程）会留下来，软件渲染的 GPU 进程
# 一直占满一个核。所以结束时连同整棵进程树一起结束；每次开始前再按临时配置目录名找出以前遗留的进程。
PROFILE_PREFIX = "vrmify_snap_profile_"
OWNER_FILE = "owner.pid"            # 配置目录里记着是哪个进程开的浏览器，用来判断是不是遗留的


def browser_procs(marker=PROFILE_PREFIX):
    """命令行里带 marker 的进程 → [(进程号, 命令行)]（截图浏览器的命令行里有它的临时配置目录）。"""
    try:
        if os.name == "nt":
            ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and "
                  "$_.CommandLine -like '*%s*' } | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }" % marker)
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60).stdout
            rows = [ln.split("\t", 1) for ln in out.splitlines() if "\t" in ln]
        else:
            out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=60).stdout
            rows = [ln.strip().split(None, 1) for ln in out.splitlines() if marker in ln]
        return [(int(p), cmd) for p, cmd in rows if p.strip().isdigit() and int(p) != os.getpid()]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _kill_pid(pid):
    """结束进程和它的子进程。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=60)
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass


def _alive(pid):
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"],
                                 capture_output=True, text=True, errors="replace", timeout=60).stdout
            return '"%d"' % pid in out
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def stop_browser(proc, prof):
    """结束这次截图的浏览器：整棵进程树；主进程先退出了的、树已经断开的，按配置目录名再找一遍。"""
    if os.name == "nt":
        _kill_pid(proc.pid)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)       # 浏览器单独一个进程组（start_new_session）
        except OSError:
            pass
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    for pid, _ in browser_procs(os.path.basename(prof)):
        _kill_pid(pid)


def cleanup_stale(tmp=None):
    """以前遗留的截图浏览器和临时配置目录（异常退出、或者旧版本没关干净的）。正在跑的别的截图
    （配置目录里记的进程还活着）不动。"""
    tmp = tmp or tempfile.gettempdir()
    live = set()
    try:
        names = [n for n in os.listdir(tmp) if n.startswith(PROFILE_PREFIX)]
    except OSError:
        names = []
    for n in names:
        try:
            with open(os.path.join(tmp, n, OWNER_FILE)) as f:
                owner = int(f.read().strip() or 0)
        except (OSError, ValueError):
            owner = 0
        if owner and owner != os.getpid() and _alive(owner):
            live.add(n)
    for pid, cmd in browser_procs():
        m = re.search(re.escape(PROFILE_PREFIX) + r"[^\s\"'\\/]*", cmd)
        if m and m.group(0) not in live:
            _kill_pid(pid)
    for n in names:
        if n not in live:
            shutil.rmtree(os.path.join(tmp, n), ignore_errors=True)


def row_label(path, all_paths):
    """行标签：文件名（不带扩展名）；有同名的行时前面加上所在文件夹名。"""
    def short(p):
        return os.path.splitext(os.path.basename(p.split(" · ")[0]))[0] +             (" · " + p.split(" · ")[1] if " · " in p else "")
    if sum(short(q) == short(path) for q in all_paths) > 1:
        return os.path.basename(os.path.dirname(os.path.abspath(path.split(" · ")[0]))) + "/" + short(path)
    return short(path)


def to_vrms(paths, tmp, log):
    """→ [(行标签, VRM 路径)]；不是 VRM 的先用当前代码转（临时目录，按 VRM 1.0 的默认选项）。"""
    import pipeline
    out = []
    for i, p in enumerate(paths):
        if p.lower().endswith(".vrm"):
            out.append((p, p))
            continue
        d = os.path.join(tmp, "conv%d" % i)
        log("转换 %s …" % os.path.basename(p))
        vrms = [v for v in pipeline.convert_file(p, {}, out_dir=d) if v.lower().endswith(".vrm")]
        if not vrms:
            raise SystemExit("%s 没有转出 VRM" % p)
        out += [(p if len(vrms) == 1 else "%s · %s" % (p, os.path.basename(v)), v) for v in vrms]
    return out


def shoot(models, out_png, size=224, custom=False, browser=None, timeout=300, cols=len(SHOTS)):
    exe = find_browser(browser)
    if not exe:
        raise SystemExit("找不到 Edge / Chrome（用 --browser 指定路径）")
    files = {"/model/%d.vrm" % i: p for i, (_, p) in enumerate(models)}
    models = [(row_label(label, [m[0] for m in models]), p) for label, p in models]
    job = {"size": size, "custom": custom, "shots": SHOTS, "cols": cols,
           "models": [{"url": "/model/%d.vrm" % i, "name": os.path.basename(p), "label": label}
                      for i, (label, p) in enumerate(models)]}
    result, done = {}, threading.Event()

    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, data, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            p = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            if p == "/":
                return self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if p == "/job":
                return self._send(json.dumps(job, ensure_ascii=False).encode("utf-8"), "application/json")
            if p in files:
                with open(files[p], "rb") as f:
                    return self._send(f.read(), "model/gltf-binary")
            return super().do_GET()

        def translate_path(self, path):
            p = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
            parts = [x for x in p[len("/viewer/"):].split("/") if x not in ("", ".", "..")] if p.startswith("/viewer/") else []
            return os.path.join(VIEWER, *parts) if parts else os.path.join(VIEWER, "__none__")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            result.update(json.loads(self.rfile.read(n).decode("utf-8")))
            self._send(b"{}", "application/json")
            done.set()

    cleanup_stale()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    prof = tempfile.mkdtemp(prefix=PROFILE_PREFIX)
    with open(os.path.join(prof, OWNER_FILE), "w") as f:
        f.write(str(os.getpid()))
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    proc = None
    try:
        proc = subprocess.Popen([exe, "--headless=new", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                                 "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                                 "--user-data-dir=" + prof]
                                # Linux 的 CI（Ubuntu 24.04 限制了非特权用户命名空间）里 Chrome 的沙箱起不来；只加载本地页面
                                + ([] if os.name == "nt" else ["--no-sandbox"]) + [url],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=os.name != "nt")
        if not done.wait(timeout):
            raise SystemExit("%d 秒内浏览器没有交回截图" % timeout)
    finally:
        if proc is not None:
            stop_browser(proc, prof)
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(prof, ignore_errors=True)
    if "error" in result:
        raise SystemExit("页面出错：\n" + result["error"])
    data = base64.b64decode(result["png"].split(",", 1)[1])
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    with open(out_png, "wb") as f:
        f.write(data)
    return out_png


def main(argv=None):
    ap = argparse.ArgumentParser(description="表情截图对照表（每个输入一行）")
    ap.add_argument("inputs", nargs="+", help=".vrm，或任何能转换的模型（先用当前代码转）")
    ap.add_argument("-o", "--out", default="snapshots.png", help="输出 PNG")
    ap.add_argument("--size", type=int, default=224, help="每格边长（像素）")
    ap.add_argument("--custom", action="store_true", help="再加上模型自己的自定义表情")
    ap.add_argument("--cols", type=int, default=len(SHOTS), help="每段多少列，超出的换到下一段（默认 %d，正好放下标准的一组）" % len(SHOTS))
    ap.add_argument("--browser", help="Edge / Chrome 的路径（默认自动找）")
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    tmp = tempfile.mkdtemp(prefix="vrmify_snap_")
    try:
        models = to_vrms(a.inputs, tmp, print)
        print("截图 %d 个模型 …" % len(models))
        shoot(models, a.out, a.size, a.custom, a.browser, a.timeout, a.cols)
        print("已写出 %s" % os.path.abspath(a.out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
