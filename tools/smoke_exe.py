# -*- coding: utf-8 -*-
"""发布前的冒烟测试：启动打包好的 exe，通过它自己的 HTTP 接口转换几个合成模型，检查结果。

    python tools/smoke_exe.py dist/Model-To-VRM-v0.1.2-win64.zip     发布用的 zip
    python tools/smoke_exe.py dist/Model-To-VRM                      解开的程序文件夹（或 exe 本身）

单元测试测的是源码；这里测的是**打包以后**还能不能用：PyInstaller 漏打的模块（某种输入格式
用到的 fbx2pmx / unitypackage_unpack / archive …）、漏打的页面和预览库、exe 起不来，都只在这里暴露。

步骤（数据目录用临时文件夹，不碰本机的 settings.json / edits.json）：
  1. 用 tests/synth.py 合成 PMX，再做出 zip、FBX、unitypackage 三种输入；
  2. `Model-To-VRM.exe --no-open --port N` 启动，等 /api/ping；
  3. 版本号要和 ui/version.py 一致；页面、预览组件、three.js / three-vrm / ammo.js 都能取到；
  4. 加入四个文件 → 转换 → 等转换结束：每个都成功、各出一个 VRM，日志里没有错误；
  5. 每个 VRM 过 tools/qa/check_vrm（装了 jsonschema 时含官方 Schema）；
  6. 预览（PMX 原生预览、VRM 文件）和调整面板的接口能用。
有任何一项失败退出码为 1，exe 的输出打印在最后。GitHub Actions 的 release.yml 打包后会跑它。
"""
import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("tests", "convert", "formats", os.path.join("tools", "qa"), "ui"):
    _d = os.path.join(ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)
APP = "Model-To-VRM"
STATIC = ["/", "/app.js", "/core.js", "/files.js", "/options.js", "/run.js", "/preview.js", "/tune.js", "/layout.js", "/i18n.js", "/viewer/viewer-core.js", "/viewer/vendor/three/three.module.min.js",
          "/viewer/vendor/three-vrm/three-vrm.module.min.js", "/viewer/vendor/three/addons/libs/ammo.wasm.js",
          "/viewer/vendor/three/addons/loaders/MMDLoader.js"]


class Failed(Exception):
    pass


def make_inputs(folder):
    """合成模型 → {名字: 路径}：PMX、zip（PMX + 贴图）、FBX、unitypackage（里面一个 FBX）。"""
    import fbxout
    import synth
    pmx = synth.build(os.path.join(folder, "pmx"), "合成")
    src = os.path.dirname(synth.build(os.path.join(folder, "zip_src"), "合成zip"))     # 输出名不和 PMX 的撞
    zp = os.path.join(folder, "合成包.zip")
    with zipfile.ZipFile(zp, "w") as z:
        for d, _, fs in os.walk(src):
            for fn in fs:
                p = os.path.join(d, fn)
                z.write(p, os.path.join("合成包", os.path.relpath(p, src)))
    fbx = os.path.join(folder, "fbx", "Synth.fbx")
    os.makedirs(os.path.dirname(fbx))
    fbxout.export_pmx(pmx, fbx, log=lambda *a, **k: None)
    pkg = os.path.join(folder, "Synth.unitypackage")
    with tarfile.open(pkg, "w:gz") as tf:
        guid = "%032x" % 1
        tf.add(fbx, guid + "/asset")
        info = tarfile.TarInfo(guid + "/pathname")
        data = b"Assets/Synth/SynthPkg.fbx"                 # 输出名不和单独的 FBX 撞
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return {"pmx": pmx, "zip": zp, "fbx": fbx, "unitypackage": pkg}


def find_exe(target, tmp):
    if os.path.isfile(target) and target.lower().endswith(".zip"):
        with zipfile.ZipFile(target) as z:
            z.extractall(os.path.join(tmp, "app"))
        target = os.path.join(tmp, "app")
    if os.path.isdir(target):
        for d, _, fs in os.walk(target):
            if APP + ".exe" in fs:
                return os.path.join(d, APP + ".exe")
        raise Failed("在 %s 里没找到 %s.exe" % (target, APP))
    return target


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Client:
    def __init__(self, port):
        self.base = "http://127.0.0.1:%d" % port

    def get(self, path, timeout=30):
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""

    def json(self, path, body=None, timeout=60):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))


def run(exe, inputs, tmp, report, timeout):
    import check_vrm
    from version import __version__

    port = free_port()
    env = dict(os.environ, MODEL2VRM_DATA=os.path.join(tmp, "data"))
    out_log = open(os.path.join(tmp, "exe.log"), "wb")
    proc = subprocess.Popen([exe, "--no-open", "--port", str(port)], env=env, cwd=tmp,
                            stdout=out_log, stderr=subprocess.STDOUT)
    c = Client(port)
    try:
        t0 = time.time()
        while True:
            if proc.poll() is not None:
                raise Failed("exe 启动后退出了（退出码 %s）" % proc.returncode)
            try:
                c.get("/api/ping", timeout=2)
                break
            except OSError:
                if time.time() - t0 > 90:
                    raise Failed("90 秒内 /api/ping 没有响应")
                time.sleep(0.5)
        report("启动 %.1f 秒" % (time.time() - t0))

        st = c.json("/api/state")
        if st.get("version") != __version__:
            raise Failed("版本号 %r，ui/version.py 是 %r" % (st.get("version"), __version__))
        app_out = st.get("app_out") or ""
        if not os.path.normcase(app_out).startswith(os.path.normcase(env["MODEL2VRM_DATA"])):
            raise Failed("默认输出目录 %r 不在数据目录里" % app_out)
        report("版本 %s · 默认输出 %s" % (__version__, app_out))

        for p in STATIC:
            code, body = c.get(p)
            if code != 200 or not body:
                raise Failed("取不到 %s（HTTP %s）" % (p, code))
        report("页面和预览库 %d 个文件都在" % len(STATIC))

        ids = c.json("/api/add", {"paths": list(inputs.values())})["added"]
        files = {f["id"]: f for f in c.json("/api/state")["files"]}
        kinds = sorted(files[i]["kind"] for i in ids)
        if len(ids) != len(inputs):
            raise Failed("加入了 %d 个文件，应为 %d（%s）" % (len(ids), len(inputs), kinds))
        report("加入 %s" % " / ".join(kinds))

        # 没转换之前，PMX 的预览是原生预览：/model/… 直接给 PMX 文件
        pmx_id = next(i for i in ids if files[i]["kind"] == "pmx")
        pv = c.json("/api/preview", {"id": pmx_id})
        code, body = c.get(pv.get("url") or "/missing")
        if code != 200 or not body.startswith(b"PMX "):
            raise Failed("PMX 原生预览取不到：%s" % pv)

        seq = c.json("/api/state")["seq"]
        if not c.json("/api/convert", {"ids": []}).get("started"):
            raise Failed("/api/convert 没有开始")
        errors, msgs, t0, finished = [], [], time.time(), None
        while finished is None:
            if time.time() - t0 > timeout:
                raise Failed("%d 秒内没转完" % timeout)
            for ev in c.json("/api/events?after=%d" % seq, timeout=30)["events"]:
                seq = max(seq, ev["seq"])
                if ev.get("type") == "log":
                    msgs.append(ev["msg"])
                    if ev.get("tag") == "err":
                        errors.append(ev["msg"])
                if ev.get("type") == "busy" and ev.get("busy") is False:
                    finished = ev
        report("转换 %.1f 秒：成功 %s、失败 %s" % (time.time() - t0, finished.get("done"), finished.get("failed")))
        if errors:
            raise Failed("日志里有错误：\n" + "\n".join(errors)[-3000:])
        # 几个文件会同时转：子进程也是这个 exe（靠 ui/app.py 的 freeze_support）；退回一个一个转说明多进程坏了
        if any("没法同时转换" in m for m in msgs):
            raise Failed("打包后的 exe 开不起转换子进程（退回了一个一个转）")
        report(next((m for m in msgs if m.startswith("同时转换")), "一个一个转（这台机器的 CPU / 内存只够一个）"))

        validators, why = check_vrm.load_validators()
        if why:
            report(why)
        files = {f["id"]: f for f in c.json("/api/state")["files"]}
        vrm_url = None
        for i in ids:
            f = files[i]
            vrms = [o for o in f["outputs"] if o["path"].lower().endswith(".vrm")]
            if f["status"] != "done" or len(vrms) != 1:
                raise Failed("%s：状态 %s，VRM 输出 %d 个（%s）" % (f["name"], f["status"], len(vrms), f.get("error")))
            path = vrms[0]["path"]
            if not os.path.isfile(path):
                raise Failed("%s：输出文件不存在 %s" % (f["name"], path))
            errs, _, summary = check_vrm.check(path, validators)
            if errs:
                raise Failed("%s 校验失败：\n  %s" % (os.path.basename(path), "\n  ".join(errs[:10])))
            report("%-14s → %s（%.2f MB）" % (f["kind"], os.path.basename(path), os.path.getsize(path) / 1048576.0))
            vrm_url = vrm_url or vrms[0]["url"]

        code, body = c.get(vrm_url)
        if code != 200 or not body.startswith(b"glTF"):
            raise Failed("VRM 文件预览取不到：%s" % vrm_url)
        tune = c.json("/api/tune", {"id": pmx_id})
        if tune.get("error") or not tune.get("expressions"):
            raise Failed("调整面板的接口异常：%s" % str(tune)[:300])
        report("预览（PMX / VRM）和调整面板接口正常")
        if proc.poll() is not None:
            raise Failed("exe 中途退出了（退出码 %s）" % proc.returncode)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        out_log.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="打包后的 exe 冒烟测试")
    ap.add_argument("target", help="发布 zip、解开的程序文件夹或 exe")
    ap.add_argument("--timeout", type=int, default=300, help="转换的最长等待秒数")
    ap.add_argument("--keep", action="store_true", help="保留临时文件夹（看输出和 exe 日志）")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    tmp = tempfile.mkdtemp(prefix="model2vrm_smoke_")
    ok = False
    try:
        exe = find_exe(os.path.abspath(a.target), tmp)
        print("exe：%s" % exe)
        inputs = make_inputs(os.path.join(tmp, "inputs"))
        run(exe, inputs, tmp, lambda m: print("  ✓ " + m, flush=True), a.timeout)
        ok = True
        print("冒烟测试通过")
    except Failed as e:
        print("✗ %s" % e)
    except Exception as e:  # noqa: BLE001  接口返回了意料之外的东西：同样算失败，照样打印 exe 输出
        print("✗ %s: %s" % (type(e).__name__, e))
    finally:
        log = os.path.join(tmp, "exe.log")
        if not ok and os.path.isfile(log):
            with open(log, "rb") as f:
                print("---- exe 输出（最后 4000 字）----\n" + f.read().decode("utf-8", "replace")[-4000:])
        if a.keep:
            print("临时文件夹：%s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
