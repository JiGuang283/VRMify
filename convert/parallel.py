# -*- coding: utf-8 -*-
"""多进程并行转换：几个文件（或一个包里的几个模型）同时转。

转换是纯 Python、吃 CPU 的活，线程受 GIL 限制快不了，所以开子进程（spawn）。每个任务的日志经队列送回主进程，
交给这个任务自己的 log 回调（界面按文件分开显示）；任务结束的回调在主进程里、并且在它的日志都送完之后调用。

    results = parallel.run([(key, func, args, kwargs), ...], jobs, log_for, start=None, done=None, should_stop=None)

func 必须是模块顶层函数（子进程要能按名字导入），调用方式是 func(*args, log=..., **kwargs)。
子进程里不再开并行（in_worker()），免得「4 个文件 × 每个包 4 个模型」叠出 16 个进程。
打包的 exe 要在入口调用 multiprocessing.freeze_support()（ui/app.py），否则子进程会再开一个程序。
"""
import concurrent.futures as cf
import multiprocessing as mp
import os
import queue
import threading
import traceback

WORKER_ENV = "MODEL2VRM_WORKER"
GB_PER_JOB = 1.5          # 一个大模型（十几万顶点）转换时进程峰值约 0.6 GB，多留余量


def in_worker():
    return os.environ.get(WORKER_ENV) == "1"


def free_memory_gb():
    """可用物理内存（GB）；拿不到时按 8 GB 算。"""
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullAvailPhys / 1024 ** 3
        else:
            return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024 ** 3
    except (OSError, ValueError, AttributeError):
        pass
    return 8.0


def jobs_for(n_tasks, setting=0):
    """同时跑几个：setting > 0 时按设置；0 = 自动（CPU 核数 − 1、最多 4 个、按可用内存每个约 1.5 GB）。"""
    if n_tasks <= 1 or in_worker():
        return 1
    try:
        setting = int(setting or 0)
    except (TypeError, ValueError):
        setting = 0
    if setting > 0:
        return min(setting, n_tasks)
    cpu = os.cpu_count() or 2
    return max(1, min(4, cpu - 1, int(free_memory_gb() // GB_PER_JOB), n_tasks))


_QUEUE = None


def _init(q):
    global _QUEUE
    _QUEUE = q
    os.environ[WORKER_ENV] = "1"


def _call(key, func, args, kwargs):
    def log(msg, tag=None):
        _QUEUE.put((key, str(msg), tag))
    try:
        out = (True, func(*args, log=log, **kwargs))
    except Exception as e:  # noqa: BLE001  一个任务失败不影响其它
        out = (False, ("%s: %s" % (type(e).__name__, e), traceback.format_exc()))
    _QUEUE.put((key, None, "__done__"))         # 这个任务的日志到此为止
    return out


def run(tasks, jobs, log_for, start=None, done=None, should_stop=None):
    """→ {key: (成功?, 结果 或 (错误摘要, 完整 traceback))}。should_stop() 为真后不再开始新任务。"""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    finished = {key: threading.Event() for key, *_ in tasks}
    stop_pump = threading.Event()

    def pump():
        while not (stop_pump.is_set() and q.empty()):
            try:
                key, msg, tag = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if tag == "__done__":
                finished[key].set()
            else:
                log_for(key)(msg, tag)

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    by_key = {t[0]: t for t in tasks}
    results, pending, running, started = {}, list(tasks), {}, set()
    broken = False
    try:
        with cf.ProcessPoolExecutor(max_workers=jobs, mp_context=ctx, initializer=_init, initargs=(q,)) as ex:
            while (pending or running) and not broken:
                while pending and len(running) < jobs and not (should_stop and should_stop()):
                    key, func, args, kwargs = pending.pop(0)
                    if start and key not in started:
                        start(key)
                    started.add(key)
                    try:
                        running[ex.submit(_call, key, func, args, kwargs)] = key
                    except cf.process.BrokenProcessPool:
                        pending.insert(0, by_key[key])
                        broken = True
                        break
                if broken or not running:
                    break                                 # 停止了：剩下的不开始
                fin, _ = cf.wait(running, return_when=cf.FIRST_COMPLETED)
                for f in fin:
                    key = running.pop(f)
                    try:
                        res = f.result()
                    except cf.process.BrokenProcessPool:
                        # 子进程开不起来 / 被杀（杀毒软件拦截、内存不够）：这个任务和剩下的都在本进程里转
                        pending.insert(0, by_key[key])
                        broken = True
                        continue
                    finished[key].wait(10)               # 先把它的日志送完
                    results[key] = res
                    if done:
                        done(key, *res)
            if broken:
                pending = [by_key[k] for k in running.values()] + pending
                running.clear()
    except cf.process.BrokenProcessPool:
        broken = True
    finally:
        stop_pump.set()
        th.join(5)
    if broken:
        pending = [t for t in pending if t[0] not in results]
        for key, func, args, kwargs in pending:
            if should_stop and should_stop():
                break
            log = log_for(key)
            if key == pending[0][0]:
                log("没法同时转换（子进程开不起来），改成一个一个转", "warn")
            if start and key not in started:
                start(key)
            try:
                res = (True, func(*args, log=log, **kwargs))
            except Exception as e:  # noqa: BLE001
                res = (False, ("%s: %s" % (type(e).__name__, e), traceback.format_exc()))
            results[key] = res
            if done:
                done(key, *res)
    return results
