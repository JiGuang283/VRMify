# -*- coding: utf-8 -*-
"""模型转换器图形界面 - FBX / unitypackage / VRM / PMX / PSK / XPS 互转。

把 .fbx、.unitypackage、.vrm、.pmx、.uemodel、.psk、.pskx、.xps 拖进窗口即可
自动转换，不需要 Blender、不需要 FBX SDK、不需要 Unity，全部是纯 Python。

支持的方向：
    FBX / unitypackage → PMX
    VRM（0.x / 1.0）    → PMX
    PMX                → VRM（0.x / 1.0）
    PMX                → 仅校验 + 预览
    uemodel → PMX
    PMX → uemodel
    PSK / PSKX（Unreal ActorX）→ PMX
    XPS / XNALara      → PMX

依赖：仅 Python 标准库（tkinter + ctypes）。
拖放走 Windows 原生 WM_DROPFILES，不需要安装 tkinterdnd2 之类的第三方库。
界面会按系统 DPI 自动缩放（高分屏上不再是小字）。右上角「界面缩放」可以
手动指定倍率，选择会记进同目录的「config.json」。

用法：
    双击同目录的「启动PMX转换器.cmd」
    或  python main.py
"""
import json
import os
import queue
import re
import struct
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import tkinter as tk
from tkinter import filedialog, messagebox
import tkinter.ttk as ttk

# 引擎模块已归类到 formats / convert / gfx 子目录，加进 sys.path 后仍能按裸名 import
for _sub in ("formats", "convert", "gfx"):
    _p = os.path.join(BASE, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# Windows 超长路径（MAX_PATH 260）兼容层。必须在其他模块 import 之前装上：
# 它会替换 builtins.open / os.* / shutil.*，让超过 260 字符的路径自动走
# \\?\ 扩展长度形式。短路径原样透传，对现有用户零影响。
# 没有这行的话，Unity 资源包解出的深层目录树会报
# FileNotFoundError: [Errno 2]（路径看着完全正常，极难排查）。
import longpath  # noqa: F401  (导入即生效)

import fbx2pmx
import fbxout
import pmx_check
import pmx2vrm
import psk2pmx
import uemodel2pmx
import uemodelio
import xps2pmx
import preview as model_preview
from unitypackage_unpack import unpack as unpack_unitypackage

try:
    import ctypes
    from ctypes import wintypes
    HAS_CTYPES = True
except Exception:                                   # pragma: no cover
    HAS_CTYPES = False

# ------------------------------------------------------------------- 版本 --
# 版本号的唯一来源：界面显示、检查更新、Windows exe 的版本资源都从这里取。
# 发版时把 tag 打成 v<__version__>（例如 v1.1.6），「检查更新」就是拿 tag 来比大小。
# 当前值 = 已发布的最新 tag（v1.1.5）；下次发版前记得把它 +1。
__version__ = "1.1.6"

# 检查更新：主用 GitHub，连不上时自动改用 Gitee（国内直连 GitHub 常常不通）
GH_REPO = "SaraKale/Model-To-PMX"
GITEE_REPO = "sarakale/Model-To-PMX"
GH_RELEASES_API = "https://api.github.com/repos/%s/releases" % GH_REPO
GITEE_RELEASES_API = "https://gitee.com/api/v5/repos/%s/releases" % GITEE_REPO
GITEE_TAGS_API = "https://gitee.com/api/v5/repos/%s/tags" % GITEE_REPO

# 下载地址（写死在这里；第三项是提取码，没有就留空字符串）
DOWNLOAD_SOURCES = (
    ("bowlroll", "https://bowlroll.net/file/360556", ""),
    ("aplaybox", "https://www.aplaybox.com/details/model/VVjaRGrpQxHO", ""),
    ("lanzouu", "https://wwavg.lanzouu.com/b0rbforrc", "4onu"),
    ("github", "https://github.com/SaraKale/Model-To-PMX/releases", ""),
    ("gitee", "https://gitee.com/sarakale/Model-To-PMX/releases", ""),	
)

# 检测来源的显示名（品牌名不翻译）
SOURCE_LABEL = {"github": "GitHub", "gitee": "Gitee"}


def parse_version(text):
    """把 v1.2.3 / 1.2 / 1.2.3-beta 之类解析成可比较的数字元组。"""
    nums = re.findall(r"\d+", str(text or ""))
    if not nums:
        return (0,)
    return tuple(int(n) for n in nums[:4])


def version_label(tag):
    """统一显示成 vX.Y.Z —— tag 本身可能已经带 v，别拼成 vv1.2.3。"""
    s = str(tag or "").strip()
    if not s:
        return ""
    mt = re.match(r"^[vV]?(\d.*)$", s)
    return ("v" + mt.group(1)) if mt else s


def _http_json(url, timeout):
    req = urllib.request.Request(url, headers={
        "User-Agent": "ModelConvert/%s" % __version__,
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _tags_github(data):
    """GitHub /releases：优先正式版；一个都没有就放宽到「非 draft」。"""
    out = [r.get("tag_name") for r in data
           if isinstance(r, dict) and r.get("tag_name")
           and not r.get("draft") and not r.get("prerelease")]
    if out:
        return out
    return [r.get("tag_name") for r in data
            if isinstance(r, dict) and r.get("tag_name")
            and not r.get("draft")]


def _tags_gitee_releases(data):
    return [r.get("tag_name") for r in data
            if isinstance(r, dict) and r.get("tag_name")
            and not r.get("prerelease")]


def _tags_gitee_tags(data):
    return [t.get("name") for t in data
            if isinstance(t, dict) and t.get("name")]


def fetch_latest_version(timeout=12):
    """依次尝试 GitHub → Gitee(releases) → Gitee(tags)。

    返回 (最新 tag, 来源名)；任一源取到 tag 就返回。
    全部失败抛最后一个异常；全都连上但都没有 tag 时返回 (None, None)。
    """
    last = None
    for name, url, extract in (
            ("github", GH_RELEASES_API, _tags_github),
            ("gitee", GITEE_RELEASES_API, _tags_gitee_releases),
            ("gitee", GITEE_TAGS_API, _tags_gitee_tags)):
        try:
            tags = [x for x in extract(_http_json(url, timeout)) if x]
            if tags:
                return max(tags, key=parse_version), name
        except Exception as e:                      # 换下一个源继续试
            last = e
    if last is not None:
        raise last
    return None, None


# ----------------------------------------------------------------- palette --
BG = "#f4f6f9"
CARD = "#ffffff"
BORDER = "#dee3ea"
TXT = "#1f2430"
MUTED = "#6b7280"
FAINT = "#9aa3b0"
ACCENT = "#2f6fed"
ACCENT_HOVER = "#2559c8"
ACCENT_SOFT = "#e8f0ff"
OK = "#149c5c"
WARN = "#c47f12"
ERR = "#d93b3b"

FONT = "Microsoft YaHei UI"
F_TITLE = (FONT, 16, "bold")
F_SUB = (FONT, 9)
F_BODY = (FONT, 10)
F_BOLD = (FONT, 10, "bold")
F_SMALL = (FONT, 9)
F_DROP = (FONT, 13, "bold")
F_DROP2 = (FONT, 9)
F_ICON = (FONT, 26)
F_ICON2 = (FONT, 15, "bold")
# 日志字体：跟界面正文用同一字体族（原来用 Consolas，中文要靠字体回退，
# 同一行里中英文像两种字体，看着不协调）
F_LOG = (FONT, 9)

SETTINGS_PATH = os.path.join(BASE, "config.json")
PREVIEW_BASE = 480

# 拖放区的两种高度：空的时候要放得下「图标 + 主提示 + 格式行」（文案窄窗口会折行，
# 所以留了余量），拖入文件之后压扁成一条窄带，把竖向空间让给文件列表。
DZ_H_EMPTY = 150
DZ_H_COMPACT = 74

# ---- 界面缩放 --------------------------------------------------------------
# 所有像素尺寸都过 px()，字体的点值则由 tk scaling 统一放大。
# 高分屏（如 200% 缩放）下必须这样做，否则窗口按物理像素绘制、字号却按 96 DPI
# 计算，界面会小到看不清。
UI_SCALE = 1.0


def px(v):
    return int(round(v * UI_SCALE))


VRM_SPECS = ["1.0", "0.x"]
MODEL_EXTS = (".fbx", ".unitypackage", ".vrm", ".pmx", ".uemodel",
              ".psk", ".pskx", ".xps")

# 文件列表里「格式」列的显示名（与界面语言无关，都是格式自身的叫法）
KIND_LABEL = {"fbx": "FBX", "unitypackage": "Unity", "vrm": "VRM", "pmx": "PMX",
              "uemodel": "UEFormat", "psk": "PSK", "xps": "XPS"}

# ----------------------------------------------------------------- i18n ----
# 界面所有文案集中在此；切换语言后通过 _rebuild() 重建即可整窗本地化。
LANG = {
    "zh_CN": {
        "app_title": "模型转换器 · FBX / VRM / PMX / PSK / XPS",
        "subtitle": "将各类模型文件转换为PMX文件",
        "zoom_label": "界面缩放",
        "lang_label": "语言",
        # 拖放区两行文案：第一行是主提示（含点击说明），第二行只列格式。
        # 原来把「也可以点击本区域选择文件」塞在格式行里，一行太长（窄窗口会被裁掉），
        # 拆开后两行都短了，配合 _draw_dropzone 的自动折行，任何宽度都不会溢出。
        "drop_hint": "把模型文件拖到这里，也可以点击本区域选择文件",
        "drop_formats": "支持 .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps",
        "task": "任务",
        "task_auto": "自动（按扩展名）",
        "task_fbx2pmx": "FBX / unitypackage → PMX",
        "task_pmx2vrm": "PMX → VRM",
        "task_uemodel2pmx": "uemodel → PMX",
        "task_psk2pmx": "psk / pskx → PMX",
        "task_xps2pmx": "XPS → PMX",
        "task_check": "PMX 仅校验 + 预览",
        "hint_auto": ".fbx/.unitypackage → PMX　·　.vrm → PMX　·　.pmx → VRM　·　.uemodel → PMX　·　.psk/.pskx → PMX　·　.xps → PMX",
        "hint_fbx2pmx": "FBX / unitypackage 转成 MMD 的 PMX",
        "hint_pmx2vrm": "PMX 转成 VRM（自动识别 humanoid 骨骼，缺的会补占位骨）",
        "hint_uemodel2pmx": "UEFormat(.uemodel) 转成 MMD 的 PMX（贴图按同目录的 MI_*.json 关联）",
        "hint_psk2pmx": "Unreal ActorX 的 .psk / .pskx 转成 MMD 的 PMX（骨骼转日文标准名、带表情）",
        "hint_xps2pmx": "XPS / XNALara 的 .xps 转成 MMD 的 PMX（换轴 + 骨骼转日文标准名 + 贴图原样带走）",
        "hint_check": "只读取 PMX 做结构校验和预览，不输出文件",
        "scale": "缩放",
        "scale_auto": "自动",
        "scale_raw": "原始尺寸",
        "scale_custom": "自定义",
        "opt_flipz": "Z 轴翻转（FBX 右手系 → MMD）",
        "opt_bones": "预览叠加骨骼点",
        "opt_open": "完成后打开输出目录",
        "vrm_version": "VRM 版本",
        "opt_morphs": "导出表情（morph / blendShape）",
        "opt_twosided": "材质强制双面",
        "opt_edge": "XPS→PMX 时开启轮廓线",
        "opt_center": "FBX→PMX 自动居中并落地",
        "opt_fbxmorph": "FBX→PMX 导出形态键为表情",
        "opt_alpha": "FBX→PMX 贴图透明通道",
        "alpha_keep": "保留原样",
        "alpha_auto": "自动判定（推荐）",
        "alpha_strip": "全部去除",
        "note_alpha": "（真透明贴图如蕾丝 / 丝袜必须保留；自动判不准时也保留，可手动覆盖）",
        "opt_facing": "FBX→PMX 自动判定朝向（脚尖 / 脸部重心）",
        "opt_face180": "强制额外转 180°（判错时手动覆盖）",
        "log_facing_auto": "朝向自动判定：{reason} → {turn}",
        "log_facing_off": "朝向自动判定已关闭，按手动设置：{turn}",
        "log_turn_on": "额外转 180°（3D 视口正对相机）",
        "log_turn_off": "不额外旋转",
        "log_alpha_head": "贴图透明通道（{mode}）：去掉 {stripped} 张 / 保留 {kept} 张 / 本就不透明 {opaque} 张",
        "log_alpha_strip_one": "  去掉 alpha {name}（{detail}）",
        "log_alpha_keep_one": "  保留 alpha {name}（{detail}）",
        "log_alpha_fail": "{n} 张去 alpha 失败，已回退用原图",
        "note_winding": "（仍有镂空可勾选双面；绕序默认自动判定）",
        "output": "输出",
        "opt_samedir": "与源文件同目录",
        "browse": "浏览",
        "outdir_placeholder": "（输出到源文件所在目录）",
        "start": "开始转换",
        "open_out": "打开输出目录",
        "clear": "清空日志",
        "files_title": "已选择的文件",
        "files_count": "{n} 个 · 共 {size}",
        "files_col_name": "文件",
        "files_col_type": "格式",
        "files_col_size": "大小",
        "files_remove": "移除选中",
        "files_clear": "清空列表",
        "files_tip": "双击列表里的文件可以单独预览它；拖入新文件会追加到列表。",
        "files_sel_none": "请先在列表里选中要移除的文件（Ctrl / Shift 可多选）。",
        "files_removed": "已从列表移除 {n} 个文件（剩余 {m} 个）。",
        "files_cleared": "已清空文件列表。",
        "drop_more": "已选择 {n} 个文件 · 继续拖入或点击这里添加更多",
        "status_wait": "等待文件…",
        "status_staged": "已选择 {n} 个文件 · 等待开始",
        "log_staged": "══ 已载入 {n} 个文件（等待开始）══",
        "log_staged_add": "══ 追加 {n} 个文件 · 共 {m} 个（等待开始）══",
        "log_staged_dup": "（{n} 个文件已在列表中，已自动跳过）",
        "log_staged_tip": "确认下方选项后，点击「开始转换」按钮才会真正转换。",
        "log_staged_drop": "已就绪的文件与当前任务方向不匹配，已清空，请重新拖入。",
        "log_title": "转换日志",
        "preview_title": "模型预览（正面实时预览 · 可拖动旋转 / 滚轮缩放）",
        "preview_stats": "拖入任意格式的模型即可实时预览（默认正面）",
        "pv_front": "正视",
        "pv_left": "左视",
        "pv_back": "背视",
        "pv_top": "俯视",
        "pv_reset": "复位",
        "pv_spin": "自转",
        "pv_bone": "骨骼",
        "pv_wire": "线框",
        "pv_placeholder": "拖入模型后这里实时显示正面预览\n（按住拖动可旋转，滚轮缩放）",
        "pv_stats": "{name}\n顶点 {v} · 三角面 {t}\n骨骼 {b} · 材质 {m}{size}",
        "pv_stats_file": "\n文件 {size}",
        "tab_general": "通用",
        "tab_fbx": "FBX 选项",
        "tab_vrm": "VRM 选项",
        "tab_ue": "UE 选项",
        "opt_ue_fbx": "uemodel → PMX 时同时导出 FBX 文件",
        "opt_ue_alpha": "导出 PMX 时去除贴图透明通道（UE 贴图 alpha 常是数据遮罩）",
        "opt_ue_align": "自动对齐到参考模型（部件放回正确位置）",
        "opt_ue_align_pick": "选择参考模型…",
        "opt_ue_align_none": "未选择（按各自原点导出）",
        "opt_ue_align_wait": "先用「选择参考模型」指定整身 .uemodel",
        "note_ue_align": "（UEFormat 常把一个角色拆成多个 .uemodel：身体 / 头发+皮肤 / 服装…。部件文件自带的 *_adjust 骨指向它在整身上的挂点，勾选后会按这些骨自动求出平移，把它放回正确位置并共用同一套居中基准 —— 不勾则每个文件各自居中，叠进同一场景会互相错位）",
        "msg_ue_align_weak_ref": "「{name}」看起来不是整身模型（骨架只铺开 {spread} cm，整身通常 120 cm 以上）。\n\n对齐是单向的：应该拿「整身」当参考、把「部件」对齐过去。选反了会把整身搬进部件的局部空间，整个模型会整体偏移。\n\n已自动取消勾选对齐。请改选整身（身体）那个 .uemodel。",
        "opt_ue_height": "PMX → uemodel 身高",
        "unit_cm": "厘米",
        "note_ue": "（.uemodel 是 UEFormat 公开交换格式：FortnitePorting / FModel 等从 UE 资源导出的中间文件；转成 PMX 时会自动换轴）",
        "tab_psk": "PSK 选项",
        "opt_psk_jp": "骨骼转成 MMD 标准日文名（英文原名写在骨骼英文名里）",
        "opt_psk_ik": "补 MMD 足 IK 骨（足ＩＫ / つま先ＩＫ）",
        "opt_psk_morphs": "导出表情（MRPH 顶点位移 → PMX 表情）",
        "opt_psk_fbx": "psk / pskx → PMX 时同时导出 FBX 文件",
        "opt_psk_alpha": "导出 PMX 时去除贴图透明通道（UE 贴图 alpha 常是数据遮罩）",
        "psk_out": "PMX → psk 输出格式",
        "psk_out_pskx": "pskx（含法线/顶点色/附加UV/表情）",
        "psk_out_psk": "psk（仅标准块）",
        "opt_psk_height": "PSK 目标身高",
        "opt_psk_tex": "PMX → psk 时把贴图导出到 textures 文件夹",
        "note_psk": "（.psk / .pskx 是 Unreal 的 ActorX 交换格式，FModel / UEViewer 从 UE 资源导出。psk→PMX 会自动换轴成 MMD 的 Y-up、按身高归一到 20 单位，贴图按材质名在源文件同目录找同名图片。.pskx 是带法线/顶点色/附加 UV/表情块的扩展格式，标准 .psk 只留顶点/面/材质/骨骼/权重）",
        "tab_xps": "XPS 选项",
        "opt_xps_jp": "骨骼转成 MMD 标准日文名（英文原名写在骨骼英文名里）",
        "opt_xps_ik": "补 MMD 足 IK 骨（足ＩＫ / つま先ＩＫ）",
        "opt_xps_double": "材质默认两面描画（头发 / 衣服这类薄片不会漏背面）",
        "opt_xps_unused": "unused 占位骨放进独立的「unused」表示枠（不删除，权重不受影响）",
        "opt_xps_fbx": "XPS → PMX 时同时导出 FBX 文件",
        "note_xps": "（.xps 是 XNALara / XPS 的模型格式，现在流通的基本都是 XPS 的 Generic Item 2 变体。xps→PMX 会换轴成 MMD 的 Y-up、按身高归一到 20 单位；XPS 的 +Z 是正面、MMD 的正面是 -Z，所以只把 Z 取反。骨骼按规则转成 MMD 标准日文名，贴图按网格记录的漫反射贴图在同目录找，原样拷进 textures/ 且**不去 alpha**——XPS 的 alpha 就是头发 / 睫毛的透明度，和 UE 贴图的 alpha 含义正好相反。DDS 也直接拷：MMD 本体支持 .dds 贴图，不需要转码）",
        "tab_output": "输出",
        "opt_subfolder": "输出到同名子文件夹（文件名_日期_序号）",
        "zoom_auto": "自动（跟随系统）",
        "msg_title": "提示",
        "msg_pick": "请先把 .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps 文件拖进窗口，或点击拖放区域选择文件。",
        "msg_unsupported": "不支持的文件",
        "msg_unsupported_body": "无法识别：\n{files}\n\n支持 .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps",
        "msg_mismatch": "所选文件与当前任务方向不匹配。",
        "msg_no_outdir": "还没有输出目录，先转换一次吧。",
        "msg_open_fail": "打开失败",
        "msg_drop_unavail": "提示：系统未启用拖放接口，请用“浏览”按钮选择文件。",
        "ready": "就绪。把 .fbx / .unitypackage 文件拖进上方区域即可。",
        "zoom_changed": "界面缩放：{label}（实际 {pct}%）",
        "log_start": "══ 开始处理 {n} 个文件 ══",
        "log_done": "══ 完成：成功输出 {n} 个文件 ══",
        "log_none": "没有生成任何文件。",
        "status_process": "正在处理 {n} 个文件…",
        "status_done": "完成 · {n} 个文件",
        "status_none": "未生成文件",
        "dlg_title": "选择要转换的文件",
        "dlg_out": "选择输出目录",
        # 检查更新
        "btn_update": "检查更新",
        "upd_title": "检查更新",
        "upd_checking": "正在检查更新…（GitHub / Gitee）",
        "upd_cur": "当前版本：",
        "upd_new": "最新版本：",
        "upd_dl_head": "请到以下地址下载：",
        "upd_found": "发现新版本",
        "upd_uptodate": "已是最新版本",
        "upd_unknown": "无法连接更新服务器",
        "upd_na": "未知",
        "upd_key": "提取码：",
        "upd_copy": "复制",
        "upd_copied": "已复制",
        "upd_close": "关闭",
        "upd_latest": "已是最新版本 v{cur}。",
        "upd_available_log": "发现新版本 {new}（当前 v{cur}）",
        "upd_via": "（来源：{src}）",
        "upd_fail": "检查更新失败：{err}",
    },
    "zh_TW": {
        "app_title": "模型轉換器 · FBX / VRM / PMX / PSK / XPS",
        "subtitle": "將各類模型文件轉換為PMX文件",
        "zoom_label": "介面縮放",
        "lang_label": "語言",
        "drop_hint": "把模型檔案拖到這裡，也可以點擊此區域選擇檔案",
        "drop_formats": "支援 .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps",
        "task": "任務",
        "task_auto": "自動（依副檔名）",
        "task_fbx2pmx": "FBX / unitypackage → PMX",
        "task_pmx2vrm": "PMX → VRM",
        "task_uemodel2pmx": "uemodel → PMX",
        "task_psk2pmx": "psk / pskx → PMX",
        "task_xps2pmx": "XPS → PMX",
        "task_check": "PMX 僅校驗 + 預覽",
        "hint_auto": ".fbx/.unitypackage → PMX　·　.vrm → PMX　·　.pmx → VRM　·　.uemodel → PMX　·　.psk/.pskx → PMX　·　.xps → PMX",
        "hint_fbx2pmx": "FBX / unitypackage 轉成 MMD 的 PMX",
        "hint_pmx2vrm": "PMX 轉成 VRM（自動識別 humanoid 骨骼，缺的會補佔位骨）",
        "hint_uemodel2pmx": "UEFormat(.uemodel) 轉成 MMD 的 PMX（貼圖依同目錄的 MI_*.json 關聯）",
        "hint_psk2pmx": "Unreal ActorX 的 .psk / .pskx 轉成 MMD 的 PMX（骨骼轉日文標準名、含表情）",
        "hint_xps2pmx": "XPS / XNALara 的 .xps 轉成 MMD 的 PMX（換軸 + 骨骼轉日文標準名 + 貼圖原樣帶走）",
        "hint_check": "只讀取 PMX 做結構校驗和預覽，不輸出檔案",
        "scale": "縮放",
        "scale_auto": "自動",
        "scale_raw": "原始尺寸",
        "scale_custom": "自訂",
        "opt_flipz": "Z 軸翻轉（FBX 右手系 → MMD）",
        "opt_bones": "預覽疊加骨骼點",
        "opt_open": "完成後開啟輸出目錄",
        "vrm_version": "VRM 版本",
        "opt_morphs": "匯出表情（morph / blendShape）",
        "opt_twosided": "材質強制雙面",
        "opt_edge": "XPS→PMX 時開啟輪廓線",
        "opt_center": "FBX→PMX 自動置中並落地",
        "opt_fbxmorph": "FBX→PMX 匯出形態鍵為表情",
        "opt_alpha": "FBX→PMX 貼圖透明通道",
        "alpha_keep": "保留原樣",
        "alpha_auto": "自動判定（建議）",
        "alpha_strip": "全部去除",
        "note_alpha": "（真透明貼圖如蕾絲 / 絲襪必須保留；自動判不準時也保留，可手動覆蓋）",
        "opt_facing": "FBX→PMX 自動判定朝向（腳尖 / 臉部重心）",
        "opt_face180": "強制額外轉 180°（判錯時手動覆蓋）",
        "log_facing_auto": "朝向自動判定：{reason} → {turn}",
        "log_facing_off": "朝向自動判定已關閉，依手動設定：{turn}",
        "log_turn_on": "額外轉 180°（3D 視口正對相機）",
        "log_turn_off": "不額外旋轉",
        "log_alpha_head": "貼圖透明通道（{mode}）：去掉 {stripped} 張 / 保留 {kept} 張 / 本就不透明 {opaque} 張",
        "log_alpha_strip_one": "  去掉 alpha {name}（{detail}）",
        "log_alpha_keep_one": "  保留 alpha {name}（{detail}）",
        "log_alpha_fail": "{n} 張去 alpha 失敗，已回退用原圖",
        "note_winding": "（仍有鏤空可勾選雙面；繞序預設自動判定）",
        "output": "輸出",
        "opt_samedir": "與來源檔案同目錄",
        "browse": "瀏覽",
        "outdir_placeholder": "（輸出到來源檔案所在目錄）",
        "start": "開始轉換",
        "open_out": "開啟輸出目錄",
        "clear": "清空日誌",
        "files_title": "已選擇的檔案",
        "files_count": "{n} 個 · 共 {size}",
        "files_col_name": "檔案",
        "files_col_type": "格式",
        "files_col_size": "大小",
        "files_remove": "移除選取",
        "files_clear": "清空清單",
        "files_tip": "在清單中雙擊檔案可單獨預覽它；拖入新檔案會追加到清單。",
        "files_sel_none": "請先在清單中選取要移除的檔案（Ctrl / Shift 可多選）。",
        "files_removed": "已從清單移除 {n} 個檔案（剩餘 {m} 個）。",
        "files_cleared": "已清空檔案清單。",
        "drop_more": "已選擇 {n} 個檔案 · 繼續拖入或點擊這裡新增更多",
        "status_wait": "等待檔案…",
        "status_staged": "已選擇 {n} 個檔案 · 等待開始",
        "log_staged": "══ 已載入 {n} 個檔案（等待開始）══",
        "log_staged_add": "══ 追加 {n} 個檔案 · 共 {m} 個（等待開始）══",
        "log_staged_dup": "（{n} 個檔案已在清單中，已自動略過）",
        "log_staged_tip": "確認下方選項後，點擊「開始轉換」按鈕才會真正轉換。",
        "log_staged_drop": "已就緒的檔案與目前任務方向不符，已清空，請重新拖入。",
        "log_title": "轉換日誌",
        "preview_title": "模型預覽（正面即時預覽 · 可拖曳旋轉 / 滾輪縮放）",
        "preview_stats": "拖入任意格式的模型即可即時預覽（預設正面）",
        "pv_front": "正視",
        "pv_left": "左視",
        "pv_back": "背視",
        "pv_top": "俯視",
        "pv_reset": "復位",
        "pv_spin": "自轉",
        "pv_bone": "骨骼",
        "pv_wire": "線框",
        "pv_placeholder": "拖入模型後這裡即時顯示正面預覽\n（按住拖曳可旋轉，滾輪縮放）",
        "pv_stats": "{name}\n頂點 {v} · 三角面 {t}\n骨骼 {b} · 材質 {m}{size}",
        "pv_stats_file": "\n檔案 {size}",
        "tab_general": "通用",
        "tab_fbx": "FBX 選項",
        "tab_vrm": "VRM 選項",
        "tab_ue": "UE 選項",
        "opt_ue_fbx": "uemodel → PMX 時同時匯出 FBX 檔案",
        "opt_ue_alpha": "匯出 PMX 時去除貼圖透明通道（UE 貼圖 alpha 常是資料遮罩）",
        "opt_ue_align": "自動對齊到參考模型（部件放回正確位置）",
        "opt_ue_align_pick": "選擇參考模型…",
        "opt_ue_align_none": "未選擇（按各自原點匯出）",
        "opt_ue_align_wait": "先用「選擇參考模型」指定整身 .uemodel",
        "note_ue_align": "（UEFormat 常把一個角色拆成多個 .uemodel：身體 / 頭髮+皮膚 / 服裝…。部件檔自帶的 *_adjust 骨指向它在整身上的掛點，勾選後會依這些骨自動求出平移，把它放回正確位置並共用同一套居中基準 —— 不勾則每個檔案各自居中，疊進同一場景會互相錯位）",
        "msg_ue_align_weak_ref": "「{name}」看起來不是整身模型（骨架只鋪開 {spread} cm，整身通常 120 cm 以上）。\n\n對齊是單向的：應該拿「整身」當參考、把「部件」對齊過去。選反了會把整身搬進部件的局部空間，整個模型會整體偏移。\n\n已自動取消勾選對齊。請改選整身（身體）那個 .uemodel。",
        "opt_ue_height": "PMX → uemodel 身高",
        "unit_cm": "公分",
        "note_ue": "（.uemodel 是 UEFormat 公開交換格式：FortnitePorting / FModel 等從 UE 資源匯出的中介檔；轉成 PMX 時會自動換軸）",
        "tab_psk": "PSK 選項",
        "opt_psk_jp": "骨骼轉成 MMD 標準日文名（英文原名寫在骨骼英文名裡）",
        "opt_psk_ik": "補 MMD 足 IK 骨（足ＩＫ / つま先ＩＫ）",
        "opt_psk_morphs": "匯出表情（MRPH 頂點位移 → PMX 表情）",
        "opt_psk_fbx": "psk / pskx → PMX 時同時匯出 FBX 檔案",
        "opt_psk_alpha": "匯出 PMX 時去除貼圖透明通道（UE 貼圖 alpha 常是資料遮罩）",
        "psk_out": "PMX → psk 輸出格式",
        "psk_out_pskx": "pskx（含法線/頂點色/附加UV/表情）",
        "psk_out_psk": "psk（僅標準區塊）",
        "opt_psk_height": "PSK 目標身高",
        "opt_psk_tex": "PMX → psk 時把貼圖匯出到 textures 資料夾",
        "note_psk": "（.psk / .pskx 是 Unreal 的 ActorX 交換格式，FModel / UEViewer 從 UE 資源匯出。psk→PMX 會自動換軸成 MMD 的 Y-up、按身高歸一到 20 單位，貼圖依材質名在來源檔同目錄找同名圖片。.pskx 是帶法線/頂點色/附加 UV/表情區塊的擴充格式，標準 .psk 只留頂點/面/材質/骨骼/權重）",
        "tab_xps": "XPS 選項",
        "opt_xps_jp": "骨骼轉成 MMD 標準日文名（英文原名寫在骨骼英文名裡）",
        "opt_xps_ik": "補 MMD 足 IK 骨（足ＩＫ / つま先ＩＫ）",
        "opt_xps_double": "材質預設兩面描畫（頭髮 / 衣服這類薄片不會漏背面）",
        "opt_xps_unused": "unused 佔位骨放進獨立的「unused」表示枠（不刪除，權重不受影響）",
        "opt_xps_fbx": "XPS → PMX 時同時匯出 FBX 檔案",
        "note_xps": "（.xps 是 XNALara / XPS 的模型格式，現在流通的基本都是 XPS 的 Generic Item 2 變體。xps→PMX 會換軸成 MMD 的 Y-up、按身高歸一到 20 單位；XPS 的 +Z 是正面、MMD 的正面是 -Z，所以只把 Z 取反。骨骼按規則轉成 MMD 標準日文名，貼圖依網格記錄的漫反射貼圖在同目錄找，原樣拷進 textures/ 且**不去 alpha**——XPS 的 alpha 就是頭髮 / 睫毛的透明度，和 UE 貼圖的 alpha 含義正好相反。DDS 也直接拷：MMD 本體支援 .dds 貼圖，不需要轉碼）",
        "tab_output": "輸出",
        "opt_subfolder": "輸出到同名子資料夾（檔名_日期_序號）",
        "zoom_auto": "自動（跟隨系統）",
        "msg_title": "提示",
        "msg_pick": "請先把 .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps 檔案拖進視窗，或點擊拖放區域選擇檔案。",
        "msg_unsupported": "不支援的檔案",
        "msg_unsupported_body": "無法辨識：\n{files}\n\n支援 .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps",
        "msg_mismatch": "所選檔案與目前任務方向不符。",
        "msg_no_outdir": "還沒有輸出目錄，先轉換一次吧。",
        "msg_open_fail": "開啟失敗",
        "msg_drop_unavail": "提示：系統未啟用拖放介面，請用「瀏覽」按鈕選擇檔案。",
        "ready": "就緒。把 .fbx / .unitypackage 檔案拖進上方區域即可。",
        "zoom_changed": "介面縮放：{label}（實際 {pct}%）",
        "log_start": "══ 開始處理 {n} 個檔案 ══",
        "log_done": "══ 完成：成功輸出 {n} 個檔案 ══",
        "log_none": "沒有產生任何檔案。",
        "status_process": "正在處理 {n} 個檔案…",
        "status_done": "完成 · {n} 個檔案",
        "status_none": "未產生檔案",
        "dlg_title": "選擇要轉換的檔案",
        "dlg_out": "選擇輸出目錄",
        # 檢查更新
        "btn_update": "檢查更新",
        "upd_title": "檢查更新",
        "upd_checking": "正在檢查更新…（GitHub / Gitee）",
        "upd_cur": "目前版本：",
        "upd_new": "最新版本：",
        "upd_dl_head": "請到以下網址下載：",
        "upd_found": "發現新版本",
        "upd_uptodate": "已是最新版本",
        "upd_unknown": "無法連線更新伺服器",
        "upd_na": "未知",
        "upd_key": "提取碼：",
        "upd_copy": "複製",
        "upd_copied": "已複製",
        "upd_close": "關閉",
        "upd_latest": "已是最新版本 v{cur}。",
        "upd_available_log": "發現新版本 {new}（目前 v{cur}）",
        "upd_via": "（來源：{src}）",
        "upd_fail": "檢查更新失敗：{err}",
    },
    "en": {
        "app_title": "Model Converter · FBX / VRM / PMX / PSK / XPS",
        "subtitle": "Convert various model files into PMX files.",
        "zoom_label": "UI scale",
        "lang_label": "Language",
        "drop_hint": "Drop model files here, or click to pick files",
        "drop_formats": "Supports .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps",
        "task": "Task",
        "task_auto": "Auto (by extension)",
        "task_fbx2pmx": "FBX / unitypackage → PMX",
        "task_pmx2vrm": "PMX → VRM",
        "task_uemodel2pmx": "uemodel → PMX",
        "task_psk2pmx": "psk / pskx → PMX",
        "task_xps2pmx": "XPS → PMX",
        "task_check": "PMX check + preview only",
        "hint_auto": ".fbx/.unitypackage → PMX　·　.vrm → PMX　·　.pmx → VRM　·　.uemodel → PMX　·　.psk/.pskx → PMX　·　.xps → PMX",
        "hint_fbx2pmx": "Convert FBX / unitypackage into MMD PMX",
        "hint_pmx2vrm": "Convert PMX into VRM (auto-detect humanoid bones, fill missing with placeholders)",
        "hint_uemodel2pmx": "Convert UEFormat (.uemodel) into MMD PMX (textures linked via the sibling MI_*.json)",
        "hint_psk2pmx": "Convert Unreal ActorX .psk / .pskx into MMD PMX (standard JP bone names, morphs included)",
        "hint_xps2pmx": "Convert XPS / XNALara .xps into MMD PMX (axis swap + standard JP bone names + textures copied over)",
        "hint_check": "Only read PMX for structure check and preview, no output file",
        "scale": "Scale",
        "scale_auto": "Auto",
        "scale_raw": "Original size",
        "scale_custom": "Custom",
        "opt_flipz": "Flip Z axis (FBX right-handed → MMD)",
        "opt_bones": "Overlay bones in preview",
        "opt_open": "Open output folder when done",
        "vrm_version": "VRM version",
        "opt_morphs": "Export morphs (morph / blendShape)",
        "opt_twosided": "Force double-sided materials",
        "opt_edge": "Enable outline (XPS→PMX)",
        "opt_center": "FBX→PMX auto-center and drop to ground",
        "opt_fbxmorph": "FBX→PMX export shape keys as morphs",
        "opt_alpha": "FBX→PMX texture alpha channel",
        "alpha_keep": "Keep as-is",
        "alpha_auto": "Decide per texture (recommended)",
        "alpha_strip": "Strip all",
        "note_alpha": "(Real transparency such as lace / stockings must be kept; unclear cases are kept too and can be overridden)",
        "opt_facing": "FBX→PMX auto-detect facing (toe / face centroid)",
        "opt_face180": "Force an extra 180° turn (manual override)",
        "log_facing_auto": "Facing auto-detect: {reason} -> {turn}",
        "log_facing_off": "Facing auto-detect disabled, using manual setting: {turn}",
        "log_turn_on": "extra 180° turn applied (faces the camera)",
        "log_turn_off": "no extra rotation",
        "log_alpha_head": "Texture alpha ({mode}): stripped {stripped} / kept {kept} / already opaque {opaque}",
        "log_alpha_strip_one": "  strip alpha {name} ({detail})",
        "log_alpha_keep_one": "  keep alpha {name} ({detail})",
        "log_alpha_fail": "{n} texture(s) could not be stripped, original reused",
        "note_winding": "(If still holed, check double-sided; winding auto-detected)",
        "output": "Output",
        "opt_samedir": "Same folder as source",
        "browse": "Browse",
        "outdir_placeholder": "(Output to source file folder)",
        "start": "Start conversion",
        "open_out": "Open folder",
        "clear": "Clear",
        "files_title": "Selected files",
        "files_count": "{n} file(s) · {size} total",
        "files_col_name": "File",
        "files_col_type": "Format",
        "files_col_size": "Size",
        "files_remove": "Remove selected",
        "files_clear": "Clear list",
        "files_tip": "Double-click a file in the list to preview it; newly dropped files are appended.",
        "files_sel_none": "Select files in the list first (Ctrl / Shift to pick several).",
        "files_removed": "Removed {n} file(s) from the list ({m} left).",
        "files_cleared": "File list cleared.",
        "drop_more": "{n} file(s) selected · drop more or click here to add",
        "status_wait": "Waiting for files…",
        "status_staged": "{n} file(s) ready · not started",
        "log_staged": "══ Loaded {n} file(s) — waiting to start ══",
        "log_staged_add": "══ Added {n} more file(s) · {m} total — waiting to start ══",
        "log_staged_dup": "({n} file(s) already in the list, skipped)",
        "log_staged_tip": "Review the options below, then click \"Start conversion\" to run.",
        "log_staged_drop": "Loaded files no longer match the current task direction; cleared. Please drop them again.",
        "log_title": "Conversion log",
        "preview_title": "Model preview (live front view · drag to rotate / wheel to zoom)",
        "preview_stats": "Drop any model format to see a live preview (front view by default)",
        "pv_front": "Front",
        "pv_left": "Left",
        "pv_back": "Back",
        "pv_top": "Top",
        "pv_reset": "Reset",
        "pv_spin": "Spin",
        "pv_bone": "Bones",
        "pv_wire": "Wire",
        "pv_placeholder": "Drop a model here to see the live front view\n(drag to rotate · wheel to zoom)",
        "pv_stats": "{name}\nVerts {v} · Tris {t}\nBones {b} · Materials {m}{size}",
        "pv_stats_file": "\nFile {size}",
        "tab_general": "General",
        "tab_fbx": "FBX",
        "tab_vrm": "VRM",
        "tab_ue": "UE",
        "opt_ue_fbx": "Also export an FBX file when converting uemodel → PMX",
        "opt_ue_alpha": "Strip texture alpha when exporting PMX (UE alpha is often a data mask)",
        "opt_ue_align": "Auto-align to a reference model (put parts back in place)",
        "opt_ue_align_pick": "Pick reference model…",
        "opt_ue_align_none": "None (export at each file's own origin)",
        "opt_ue_align_wait": "Pick a full-body .uemodel with the button above first",
        "note_ue_align": "(UEFormat often splits one character into several .uemodel files: body / hair+skin / outfit… The *_adjust bones in a part file point at where it attaches on the full body. When enabled, those bones are used to solve a rigid offset that puts the part back in place and makes it share the reference's centering — otherwise every file is centred on its own and the parts misalign when loaded together)",
        "msg_ue_align_weak_ref": "\"{name}\" does not look like a full-body model (its skeleton only spans {spread} cm; a full body is usually over 120 cm).\n\nAlignment is one-way: the full body should be the reference and the parts should be aligned onto it. Choosing the wrong way round moves the whole body into the part's local space and shifts the entire model.\n\nThe align option has been turned off. Please pick the full-body .uemodel instead.",
        "opt_ue_height": "PMX → uemodel height",
        "unit_cm": "cm",
        "note_ue": "(.uemodel is the public UEFormat exchange format used by FortnitePorting / FModel; axes are re-mapped automatically when converting to PMX)",
        "tab_psk": "PSK",
        "opt_psk_jp": "Rename bones to MMD standard Japanese names (original English kept as the English name)",
        "opt_psk_ik": "Add MMD leg IK bones (足ＩＫ / つま先ＩＫ)",
        "opt_psk_morphs": "Export morphs (MRPH vertex deltas → PMX morphs)",
        "opt_psk_fbx": "Also export an FBX file when converting psk / pskx → PMX",
        "opt_psk_alpha": "Strip texture alpha when exporting PMX (UE alpha is often a data mask)",
        "psk_out": "PMX → psk output format",
        "psk_out_pskx": "pskx (normals / vertex colors / extra UVs / morphs)",
        "psk_out_psk": "psk (standard chunks only)",
        "opt_psk_height": "PSK target height",
        "opt_psk_tex": "Export textures into a textures folder when converting PMX → psk",
        "note_psk": "(.psk / .pskx is Unreal's ActorX exchange format exported by FModel / UEViewer. psk → PMX re-maps axes to MMD's Y-up, normalises the height to 20 units and matches textures by material name next to the source. .pskx is the extended flavour carrying normals / vertex colors / extra UVs / morphs, while a standard .psk keeps only points, faces, materials, bones and weights)",
        "tab_xps": "XPS",
        "opt_xps_jp": "Rename bones to MMD standard Japanese names (original English kept as the English name)",
        "opt_xps_ik": "Add MMD leg IK bones (足ＩＫ / つま先ＩＫ)",
        "opt_xps_double": "Make materials two-sided by default (hair / clothing sheets no longer show holes)",
        "opt_xps_unused": "Put `unused` placeholder bones into a separate \"unused\" display frame (kept, weights untouched)",
        "opt_xps_fbx": "Also export an FBX file when converting XPS → PMX",
        "note_xps": "(.xps is the XNALara / XPS model format; almost everything in circulation today is XPS's Generic Item 2 variant. xps → PMX re-maps axes to MMD's Y-up and normalises the height to 20 units; XPS has +Z as its front while MMD's front is -Z, so only Z is negated. Bones are renamed to MMD standard Japanese names by rule, and the diffuse texture recorded per mesh is looked up next to the source and copied into textures/ **with alpha kept** — in XPS the alpha channel really is hair / eyelash transparency, the opposite of Unreal textures. DDS is copied as-is too: MMD itself supports .dds textures, so no transcoding is needed)",
        "tab_output": "Output",
        "opt_subfolder": "Write into a same-named subfolder (name_date_001)",
        "zoom_auto": "Auto (follow system)",
        "msg_title": "Note",
        "msg_pick": "Please drop .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps files into the window, or click the drop area to pick files.",
        "msg_unsupported": "Unsupported file",
        "msg_unsupported_body": "Unrecognized:\n{files}\n\nSupports .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps",
        "msg_mismatch": "Selected files do not match the current task direction.",
        "msg_no_outdir": "No output folder yet — convert something first.",
        "msg_open_fail": "Failed to open",
        "msg_drop_unavail": "Note: drag-and-drop is unavailable on this system; use the \"Browse\" button.",
        "ready": "Ready. Drop .fbx / .unitypackage files into the area above.",
        "zoom_changed": "UI scale: {label} (actual {pct}%)",
        "log_start": "══ Processing {n} file(s) ══",
        "log_done": "══ Done: wrote {n} file(s) ══",
        "log_none": "No files were generated.",
        "status_process": "Processing {n} file(s)…",
        "status_done": "Done · {n} file(s)",
        "status_none": "No files generated",
        "dlg_title": "Select files to convert",
        "dlg_out": "Select output folder",
        # Check for updates
        "btn_update": "Check for updates",
        "upd_title": "Check for updates",
        "upd_checking": "Checking for updates… (GitHub / Gitee)",
        "upd_cur": "Current version: ",
        "upd_new": "Latest version: ",
        "upd_dl_head": "Download from:",
        "upd_found": "A new version is available",
        "upd_uptodate": "You're up to date",
        "upd_unknown": "Cannot reach the update server",
        "upd_na": "unknown",
        "upd_key": "Key: ",
        "upd_copy": "Copy",
        "upd_copied": "Copied",
        "upd_close": "Close",
        "upd_latest": "You're on the latest version (v{cur}).",
        "upd_available_log": "New version {new} available (current v{cur})",
        "upd_via": " (via {src})",
        "upd_fail": "Update check failed: {err}",
    },
    "ja": {
        "app_title": "モデル変換 · FBX / VRM / PMX / PSK / XPS",
        "subtitle": "各種モデルファイルをPMXファイルに変換します。",
        "zoom_label": "表示倍率",
        "lang_label": "言語",
        "drop_hint": "ここにモデルをドロップ（クリックでも可）",
        "drop_formats": "対応 .fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps",
        "task": "タスク",
        "task_auto": "自動（拡張子で判定）",
        "task_fbx2pmx": "FBX / unitypackage → PMX",
        "task_pmx2vrm": "PMX → VRM",
        "task_uemodel2pmx": "uemodel → PMX",
        "task_psk2pmx": "psk / pskx → PMX",
        "task_xps2pmx": "XPS → PMX",
        "task_check": "PMX 検証＋プレビューのみ",
        "hint_auto": ".fbx/.unitypackage → PMX　·　.vrm → PMX　·　.pmx → VRM　·　.uemodel → PMX　·　.psk/.pskx → PMX　·　.xps → PMX",
        "hint_fbx2pmx": "FBX / unitypackage を MMD の PMX に変換",
        "hint_pmx2vrm": "PMX を VRM に変換（humanoid ボーン自動判定、欠損はダミー骨で補完）",
        "hint_uemodel2pmx": "UEFormat(.uemodel) を MMD の PMX に変換（テクスチャは同フォルダの MI_*.json から関連付け）",
        "hint_psk2pmx": "Unreal ActorX の .psk / .pskx を MMD の PMX に変換（ボーンは日本語標準名・表情付き）",
        "hint_xps2pmx": "XPS / XNALara の .xps を MMD の PMX に変換（軸変換＋ボーンは日本語標準名＋テクスチャはそのまま）",
        "hint_check": "PMX を読むだけで構造検証とプレビュー、出力はしません",
        "scale": "倍率",
        "scale_auto": "自動",
        "scale_raw": "元のサイズ",
        "scale_custom": "カスタム",
        "opt_flipz": "Z 軸反転（FBX 右手系 → MMD）",
        "opt_bones": "プレビューにボーン点を重ねる",
        "opt_open": "完了後に出力フォルダを開く",
        "vrm_version": "VRM バージョン",
        "opt_morphs": "モーフを書き出し（morph / blendShape）",
        "opt_twosided": "マテリアルを両面化（強制）",
        "opt_edge": "輪郭線を有効化（XPS→PMX）",
        "opt_center": "FBX→PMX 自動中央配置＆接地",
        "opt_fbxmorph": "FBX→PMX シェイプキーをモーフに",
        "opt_alpha": "FBX→PMX テクスチャのアルファ",
        "alpha_keep": "そのまま残す",
        "alpha_auto": "自動判定（推奨）",
        "alpha_strip": "すべて除去",
        "note_alpha": "（レースやストッキングのような本来の透明は残す必要があります。判定できない場合も残し、手動で上書きできます）",
        "opt_facing": "FBX→PMX 向きを自動判定（つま先／顔の重心）",
        "opt_face180": "強制で 180° 追加回転（誤判定時の手動上書き）",
        "log_facing_auto": "向きの自動判定：{reason} → {turn}",
        "log_facing_off": "向きの自動判定はオフ、手動設定に従います：{turn}",
        "log_turn_on": "180° 追加回転しました（カメラ側を向く）",
        "log_turn_off": "追加回転なし",
        "log_alpha_head": "テクスチャのアルファ（{mode}）：除去 {stripped} / 保持 {kept} / 元から不透明 {opaque}",
        "log_alpha_strip_one": "  アルファ除去 {name}（{detail}）",
        "log_alpha_keep_one": "  アルファ保持 {name}（{detail}）",
        "log_alpha_fail": "{n} 枚のアルファ除去に失敗し、元画像を使用しました",
        "note_winding": "（それでも抜けがある場合は両面化をチェック。巻き順は自動判定）",
        "output": "出力",
        "opt_samedir": "元ファイルと同じフォルダ",
        "browse": "参照",
        "outdir_placeholder": "（元ファイルのあるフォルダへ出力）",
        "start": "変換開始",
        "open_out": "出力フォルダを開く",
        "clear": "ログを消去",
        "files_title": "選択したファイル",
        "files_count": "{n} 個 · 合計 {size}",
        "files_col_name": "ファイル",
        "files_col_type": "形式",
        "files_col_size": "サイズ",
        "files_remove": "選択を削除",
        "files_clear": "リストをクリア",
        "files_tip": "リスト内のファイルをダブルクリックすると個別にプレビューできます。新しくドロップしたファイルは追加されます。",
        "files_sel_none": "先にリストから削除するファイルを選択してください（Ctrl / Shift で複数選択）。",
        "files_removed": "{n} 個のファイルをリストから削除しました（残り {m} 個）。",
        "files_cleared": "ファイルリストをクリアしました。",
        "drop_more": "{n} 個を選択済み · さらにドロップするかここをクリックして追加",
        "status_wait": "ファイル待ち…",
        "status_staged": "{n} 個を選択済み · 開始待ち",
        "log_staged": "══ {n} 個のファイルを読み込みました（開始待ち）══",
        "log_staged_add": "══ {n} 個を追加 · 合計 {m} 個（開始待ち）══",
        "log_staged_dup": "（{n} 個は既にリストにあるためスキップ）",
        "log_staged_tip": "下のオプションを確認し、「変換開始」ボタンで変換を実行します。",
        "log_staged_drop": "読み込み済みのファイルが現在のタスク方向と一致しないため、クリアしました。もう一度ドロップしてください。",
        "log_title": "変換ログ",
        "preview_title": "モデルプレビュー（正面 リアルタイム・ドラッグで回転／ホイールで拡大）",
        "preview_stats": "任意の形式のモデルをドロップすると即時プレビュー（既定は正面）",
        "pv_front": "正面",
        "pv_left": "左面",
        "pv_back": "背面",
        "pv_top": "上面",
        "pv_reset": "リセット",
        "pv_spin": "回転",
        "pv_bone": "ボーン",
        "pv_wire": "ワイヤー",
        "pv_placeholder": "モデルをドロップすると正面がここに表示されます\n（ドラッグで回転／ホイールで拡大）",
        "pv_stats": "{name}\n頂点 {v} · 三角面 {t}\nボーン {b} · マテリアル {m}{size}",
        "pv_stats_file": "\nファイル {size}",
        "tab_general": "共通",
        "tab_fbx": "FBX",
        "tab_vrm": "VRM",
        "tab_ue": "UE",
        "opt_ue_fbx": "uemodel → PMX のときに FBX も同時出力",
        "opt_ue_alpha": "PMX 出力時にテクスチャのアルファを外す（UE の alpha はデータマスクのことが多い）",
        "opt_ue_align": "参照モデルに自動整列（パーツを正しい位置へ）",
        "opt_ue_align_pick": "参照モデルを選択…",
        "opt_ue_align_none": "未選択（各ファイルの原点で出力）",
        "opt_ue_align_wait": "先に「参照モデルを選択」で全身 .uemodel を指定してください",
        "note_ue_align": "（UEFormat では 1 体のキャラが複数の .uemodel に分かれることがよくあります：体 / 髪＋肌 / 服…。パーツ側の *_adjust 骨が全身への取付点を示すので、有効にするとその骨から平行移動を解いて正しい位置に戻し、参照モデルと同じセンタリング基準を共有します。無効だと各ファイルが個別にセンタリングされ、同じシーンに読み込むとズレます）",
        "msg_ue_align_weak_ref": "「{name}」は全身モデルではないようです（骨格の広がりが {spread} cm しかありません。全身なら通常 120 cm 以上）。\n\n整列は一方通行です：「全身」を参照にして「パーツ」を合わせるのが正しい向きです。逆にすると全身がパーツのローカル空間に移動され、モデル全体がずれます。\n\n整列のチェックを自動的に外しました。全身（体）の .uemodel を選び直してください。",
        "opt_ue_height": "PMX → uemodel の身長",
        "unit_cm": "cm",
        "note_ue": "（.uemodel は UEFormat の公開交換形式で、FortnitePorting / FModel などが UE アセットから書き出します。PMX への変換時に軸は自動変換されます）",
        "tab_psk": "PSK",
        "opt_psk_jp": "ボーンを MMD 標準の日本語名にする（元の英名は英語名欄に残します）",
        "opt_psk_ik": "MMD の足 IK ボーンを追加（足ＩＫ / つま先ＩＫ）",
        "opt_psk_morphs": "表情を書き出す（MRPH の頂点移動 → PMX 表情）",
        "opt_psk_fbx": "psk / pskx → PMX のときに FBX も同時出力",
        "opt_psk_alpha": "PMX 出力時にテクスチャのアルファを外す（UE の alpha はデータマスクのことが多い）",
        "psk_out": "PMX → psk の出力形式",
        "psk_out_pskx": "pskx（法線/頂点色/追加UV/表情つき）",
        "psk_out_psk": "psk（標準チャンクのみ）",
        "opt_psk_height": "PSK の目標身長",
        "opt_psk_tex": "PMX → psk のときテクスチャを textures フォルダへ書き出す",
        "note_psk": "（.psk / .pskx は Unreal の ActorX 交換形式で、FModel / UEViewer が UE アセットから書き出します。psk→PMX は MMD の Y-up に軸変換し身長を 20 単位に正規化、テクスチャはマテリアル名と同名の画像をソースと同じフォルダから探します。.pskx は法線/頂点色/追加 UV/表情チャンクを持つ拡張版で、標準 .psk は頂点・面・マテリアル・ボーン・ウェイトだけです）",
        "tab_xps": "XPS",
        "opt_xps_jp": "ボーンを MMD 標準の日本語名にする（元の英名は英語名欄に残します）",
        "opt_xps_ik": "MMD の足 IK ボーンを追加（足ＩＫ / つま先ＩＫ）",
        "opt_xps_double": "マテリアルを既定で両面描画にする（髪や服の薄い板が裏抜けしない）",
        "opt_xps_unused": "unused プレースホルダ骨を独立した「unused」表示枠へ（削除はせず、ウェイトにも影響なし）",
        "opt_xps_fbx": "XPS → PMX のときに FBX も同時出力",
        "note_xps": "（.xps は XNALara / XPS のモデル形式で、現在出回っているものはほぼ XPS の Generic Item 2 変種です。xps→PMX は MMD の Y-up に軸変換し身長を 20 単位に正規化します。XPS は +Z が正面、MMD は -Z が正面なので Z だけ反転します。ボーンは規則で MMD 標準の日本語名に変換し、テクスチャはメッシュごとに記録されたディフューズをソースと同じフォルダから探して textures/ へそのままコピーし、**アルファは外しません**——XPS のアルファは髪・まつ毛の透過度そのもので、UE テクスチャの alpha とは意味が逆だからです。DDS もそのままコピーします：MMD 本体が .dds テクスチャに対応しているため変換不要です）",
        "tab_output": "出力",
        "opt_subfolder": "同名のサブフォルダに出力（ファイル名_日付_連番）",
        "zoom_auto": "自動（システムに合わせる）",
        "msg_title": "注意",
        "msg_pick": ".fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps ファイルをウィンドウへドロップするか、ドロップ領域をクリックして選択してください。",
        "msg_unsupported": "非対応のファイル",
        "msg_unsupported_body": "認識できません：\n{files}\n\n対応：.fbx / .unitypackage / .vrm / .pmx / .uemodel / .psk / .pskx / .xps",
        "msg_mismatch": "選択したファイルは現在のタスク方向と一致しません。",
        "msg_no_outdir": "まだ出力フォルダがありません。一度変換してください。",
        "msg_open_fail": "開けませんでした",
        "msg_drop_unavail": "注意：このシステムではドラッグ＆ドロップが使えません。「参照」ボタンを使ってください。",
        "ready": "準備完了。.fbx / .unitypackage を上の領域へドロップしてください。",
        "zoom_changed": "表示倍率：{label}（実際 {pct}%）",
        "log_start": "══ {n} 個のファイルを処理 ══",
        "log_done": "══ 完了：{n} 個のファイルを出力しました ══",
        "log_none": "ファイルは生成されませんでした。",
        "status_process": "{n} 個のファイルを処理中…",
        "status_done": "完了 · {n} 個",
        "status_none": "ファイル未生成",
        "dlg_title": "変換するファイルを選択",
        "dlg_out": "出力フォルダを選択",
        # アップデート確認
        "btn_update": "アップデート確認",
        "upd_title": "アップデート確認",
        "upd_checking": "アップデートを確認中…（GitHub / Gitee）",
        "upd_cur": "現在のバージョン：",
        "upd_new": "最新バージョン：",
        "upd_dl_head": "以下からダウンロードしてください：",
        "upd_found": "新しいバージョンがあります",
        "upd_uptodate": "最新バージョンです",
        "upd_unknown": "更新サーバーに接続できません",
        "upd_na": "不明",
        "upd_key": "キー：",
        "upd_copy": "コピー",
        "upd_copied": "コピー済み",
        "upd_close": "閉じる",
        "upd_latest": "最新バージョンです（v{cur}）。",
        "upd_available_log": "新しいバージョン {new} があります（現在 v{cur}）",
        "upd_via": "（{src} 経由）",
        "upd_fail": "アップデート確認に失敗しました：{err}",
    },
}
LANG_NAMES = {"zh_CN": "简体中文", "zh_TW": "繁體中文", "en": "English", "ja": "日本語"}
APP_LANG = "zh_CN"          # 当前界面语言，由设置文件（lang 字段）决定


def t(key, **kw):
    table = LANG.get(APP_LANG, LANG["zh_CN"])
    s = table.get(key)
    if s is None:
        s = LANG["zh_CN"].get(key, key)
    if kw:
        try:
            return s.format(**kw)
        except Exception:
            return s
    return s


# ---- PMX → psk 输出格式两档：值用内部代号，界面显示本地化文案 ----
# （放在 refresh_choices 之前：它在模块加载时就会被调用一次。）
PSK_OUT_CODES = ("pskx", "psk")
PSK_OUTS = []                       # 由 refresh_choices() 按当前语言填


def psk_out_label(code):
    return t("psk_out_" + code)


def psk_out_code_of(label, default="pskx"):
    for c in PSK_OUT_CODES:
        if psk_out_label(c) == label:
            return c
    return default


# 任务 / 缩放 等下拉选项随语言变化，集中刷新
TASK_CHOICES = []
TASK_BY_LABEL = {}
TASK_LABELS = []
ZOOM_CHOICES = []
ZOOM_BY_LABEL = {}


def refresh_choices():
    global TASK_CHOICES, TASK_BY_LABEL, TASK_LABELS, ZOOM_CHOICES, ZOOM_BY_LABEL
    global PSK_OUTS
    TASK_CHOICES = [(t("task_auto"), "auto"),
                    (t("task_fbx2pmx"), "fbx2pmx"),
                    (t("task_pmx2vrm"), "pmx2vrm"),
                    (t("task_uemodel2pmx"), "uemodel2pmx"),
                    (t("task_psk2pmx"), "psk2pmx"),
                    (t("task_xps2pmx"), "xps2pmx"),
                    (t("task_check"), "check")]
    TASK_BY_LABEL = dict(TASK_CHOICES)
    TASK_LABELS = [lbl for lbl, _ in TASK_CHOICES]
    ZOOM_CHOICES = [(t("zoom_auto"), None), ("100%", 1.0), ("125%", 1.25),
                    ("150%", 1.5), ("175%", 1.75), ("200%", 2.0), ("250%", 2.5)]
    ZOOM_BY_LABEL = dict(ZOOM_CHOICES)
    PSK_OUTS = [psk_out_label(c) for c in PSK_OUT_CODES]


refresh_choices()

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

# 拖放状态跨界面重建保留：callback 只构造一次；oldmap 记录「每个」被替换窗口的
# 原始 WndProc。转发消息时必须用该窗口自己的 old，不能用共享变量——否则多个窗口
# 串用同一旧过程，深入子窗口时极易崩溃（这正是早期只挂顶层、不敢挂子窗口的原因）。
_dnd_state = None            # {"callback": WNDPROC, "oldmap": {hwnd: old}}
_dnd_on_files = None         # 文件就绪回调

# ------------------------------------------------------------------- drag ---
def _make_drop_proc():
    """构造 WNDPROC：捕获 WM_DROPFILES 交给回调，其余消息转发给各窗口原始过程。"""
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    ptr = ctypes.sizeof(ctypes.c_void_p)
    LRESULT = ctypes.c_longlong if ptr == 8 else ctypes.c_long
    WPARAM = ctypes.c_ulonglong if ptr == 8 else ctypes.c_uint
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 WPARAM, LRESULT)

    def proc(h, msg, wp, lp):
        # 直接读模块全局 _dnd_state（不要捕获局部变量：_make_drop_proc 在
        # _dnd_state 赋值之前被调用，捕获会锁死成 None）
        state = _dnd_state
        if msg == WM_DROPFILES:
            files = []
            try:
                n = shell32.DragQueryFileW(wintypes.HANDLE(wp), 0xFFFFFFFF,
                                           None, 0)
                for i in range(n):
                    buf = ctypes.create_unicode_buffer(2048)
                    if shell32.DragQueryFileW(wintypes.HANDLE(wp), i, buf, 2048):
                        if buf.value:
                            files.append(buf.value)
                shell32.DragFinish(wintypes.HANDLE(wp))
            except Exception:
                traceback.print_exc()
            if files and _dnd_on_files is not None:
                try:
                    _dnd_on_files(files)
                except Exception:
                    traceback.print_exc()
            return 0
        hk = int(getattr(h, "value", h))
        old = state["oldmap"].get(hk)
        if old:
            return user32.CallWindowProcW(old, h, msg, wp, lp)
        return 0

    return WNDPROC(proc)


def enable_drop(hwnd, on_files, register_children=True):
    """让一个（含其全部子控件）Tk 窗口接收 Explorer 拖入的文件。

    根因：tkinter 控件在 Windows 上各是一个原生子窗口，鼠标拖到哪个子控件，
    Explorer 就把 WM_DROPFILES 发给那个子控件。只给顶层 root 注册拖放时，窗口
    内容区（拖放区 Canvas、日志、预览等）都是子控件，落到它们上面 root 收不到
    消息，表现为「拖上去没反应」。这里给 root + 所有子孙窗口都注册，并各自保存
    原始 WndProc 用于转发，于是整个窗口任意位置都能拖入。
    """
    if not HAS_CTYPES or os.name != "nt":
        return False
    global _dnd_state, _dnd_on_files
    _dnd_on_files = on_files
    try:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        ptr = ctypes.sizeof(ctypes.c_void_p)
        LRESULT = ctypes.c_longlong if ptr == 8 else ctypes.c_long
        WPARAM = ctypes.c_ulonglong if ptr == 8 else ctypes.c_uint
        setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                             ctypes.c_void_p]
        try:
            user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int,
                                              ctypes.c_void_p]
        except Exception:
            pass
        user32.CallWindowProcW.restype = LRESULT
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                           wintypes.UINT, WPARAM, LRESULT]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                           ctypes.c_wchar_p, wintypes.UINT]
        shell32.DragFinish.argtypes = [wintypes.HANDLE]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]

        if _dnd_state is None:
            _dnd_state = {"callback": _make_drop_proc(), "oldmap": {}}
        callback = _dnd_state["callback"]

        def hook_one(h):
            h = wintypes.HWND(h)
            hk = h.value
            if hk in _dnd_state["oldmap"]:
                return
            old = setter(h, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
            if old:
                _dnd_state["oldmap"][hk] = old
                shell32.DragAcceptFiles(h, True)

        hook_one(hwnd)
        if register_children:
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                            ctypes.c_void_p)

            def enum_proc(h, _lp):
                hook_one(h)
                return True

            user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC,
                                                ctypes.c_void_p]
            user32.EnumChildWindows.restype = wintypes.BOOL
            user32.EnumChildWindows(wintypes.HWND(hwnd),
                                    WNDENUMPROC(enum_proc), None)
        return True
    except Exception:
        traceback.print_exc()
        return False


# ------------------------------------------------------------------ helpers --
def stdout_redirect(q):
    """Route print() from the converters into the GUI log, line by line."""

    class W:
        def __init__(self):
            self.buf = ""

        def write(self, s):
            self.buf += s
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                q.put(("log", line))

        def flush(self):
            if self.buf.strip():
                q.put(("log", self.buf))
            self.buf = ""

    return W()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.2f %s" % (n, unit))
        n /= 1024.0


def classify(path):
    if os.path.isdir(path):
        return "dir"
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":
        return "fbx"
    if ext == ".unitypackage":
        return "unitypackage"
    if ext == ".vrm":
        return "vrm"
    if ext == ".pmx":
        return "pmx"
    if ext == ".uemodel":
        return "uemodel"
    if ext in (".psk", ".pskx"):
        return "psk"
    if ext == ".xps":
        return "xps"
    return "unknown"


AUTO_TASK = {"fbx": "fbx2pmx", "unitypackage": "fbx2pmx",
             "pmx": "pmx2vrm",
             "uemodel": "uemodel2pmx", "psk": "psk2pmx", "xps": "xps2pmx"}


def find_fbx(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in filenames:
            if f.lower().endswith(".fbx"):
                p = os.path.join(dirpath, f)
                if os.path.getsize(p) > 1024:
                    out.append(p)
    return out


def find_models(root):
    """拖入文件夹时，找出里面所有可处理的模型文件。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in filenames:
            if f.lower().endswith(MODEL_EXTS):
                p = os.path.join(dirpath, f)
                if os.path.getsize(p) > 1024:
                    out.append(p)
    return out


# -------------------------------------------------------------- dpi / zoom --
def declare_dpi_aware():
    """让进程声明 DPI 感知，Windows 才不会对窗口做位图拉伸（发虚）。"""
    if os.name != "nt" or not HAS_CTYPES:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # system aware
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def system_dpi():
    """当前系统的实际 DPI（96 = 100%，192 = 200%）。"""
    if os.name != "nt" or not HAS_CTYPES:
        return 96
    user32 = ctypes.windll.user32
    try:
        dpi = int(user32.GetDpiForSystem())                 # Win10 1607+
        if dpi > 0:
            return dpi
    except Exception:
        pass
    try:
        hdc = user32.GetDC(0)
        try:
            return int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))
        finally:
            user32.ReleaseDC(0, hdc)
    except Exception:
        return 96


def norm_zoom(v):
    """把设置文件里的值规整成倍率；None 表示跟随系统。"""
    if v is None or v == "" or v == "auto":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.5 <= f <= 4.0 else None


def zoom_label(factor):
    if factor is None:
        return "自动（跟随系统）"
    for lbl, f in ZOOM_CHOICES:
        if f is not None and abs(f - factor) < 1e-6:
            return lbl
    return "%.0f%%" % (factor * 100)


# ---- 贴图 alpha 三档：值用内部代号（keep/auto/strip），界面显示本地化文案 ----
def alpha_label(code):
    return t("alpha_" + code)


def alpha_labels():
    return [alpha_label(c) for c in fbx2pmx.ALPHA_MODES]


def alpha_code_of(label, default="auto"):
    for c in fbx2pmx.ALPHA_MODES:
        if alpha_label(c) == label:
            return c
    return default


def apply_scaling(root, factor):
    """设定字体缩放。factor=None 跟随系统。返回实际生效的倍率。"""
    global UI_SCALE
    if factor is None:
        factor = max(1.0, min(system_dpi() / 96.0, 3.0))
    UI_SCALE = factor
    try:
        root.tk.call("tk", "scaling", 96.0 * factor / 72.0)
    except Exception:
        pass
    return factor


def load_zoom_pref():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return norm_zoom(json.load(f).get("ui_zoom"))
    except Exception:
        return None


def validate_pmx(path):
    """Re-open the written PMX and return (summary_dict, problems_list)."""
    m = pmx_check.read_pmx(path)
    nv, nb = len(m["vertices"]), len(m["bones"])
    bad_v = sum(1 for f in m["faces"] if f >= nv)
    bad_b = 0
    for v in m["vertices"]:
        for b in v[4]:
            if b >= nb or b < 0:
                bad_b += 1
    wsum_bad = 0
    for v in m["vertices"]:
        wt, ws = v[3], v[5]
        if wt == 0:
            s = 1.0
        elif wt in (1, 3):
            # BDEF2 / SDEF：只存第 1 根骨骼的权重，另一根是 1-w，和恒为 1
            s = ws[0] + (1.0 - ws[0])
        else:
            s = sum(ws)
        if abs(s - 1.0) > 0.02:
            wsum_bad += 1
    cover = sum(mm["faces"] for mm in m["materials"])
    problems = []
    if m["consumed"] != m["filesize"]:
        problems.append("字节数不匹配 %d/%d" % (m["consumed"], m["filesize"]))
    # MMD 只认 UTF-16LE 的 PMX（原版提示：エンコード方式がUTF16のPMXファイル
    # しか読み込めません）。编码为 UTF-8 时 MMD 会直接拒绝载入。
    if m.get("globals") and m["globals"][0] == 1:
        problems.append("文本编码是 UTF-8，MMD 无法载入（需要 UTF-16LE）")
    if bad_v:
        problems.append("%d 个面引用了不存在的顶点" % bad_v)
    if bad_b:
        problems.append("%d 个权重的骨骼索引越界" % bad_b)
    if wsum_bad:
        problems.append("%d 个顶点权重和偏离 1.0" % wsum_bad)
    if cover != len(m["faces"]):
        problems.append("材质面覆盖不全 %d/%d" % (cover, len(m["faces"])))
    return m, problems


# ------------------------------------------------------------------- app ----
class BigCheck(tk.Frame):
    """比原生 Checkbutton 更大的复选项：放大的勾选框 + 更大的点击区域。"""

    def __init__(self, master, text="", variable=None, command=None,
                 font=None, fg=TXT, bg=CARD, padx=0, **kw):
        super().__init__(master, bg=bg, **kw)
        self.var = variable if variable is not None else tk.BooleanVar()
        self.command = command
        self._bg = bg
        self._size = px(22)
        self._box = tk.Canvas(self, width=self._size, height=self._size,
                              bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._box.pack(side="left", padx=(padx, px(8)))
        self._lbl = tk.Label(self, text=text, bg=bg, fg=fg,
                             font=font or F_BODY, cursor="hand2")
        self._lbl.pack(side="left")
        self._box.bind("<Button-1>", self._toggle)
        self._lbl.bind("<Button-1>", self._toggle)
        self.bind("<Button-1>", self._toggle)
        try:
            self.var.trace_add("write", lambda *_a: self._draw())
        except Exception:
            self.var.trace("w", lambda *_a: self._draw())
        self._draw()

    def _toggle(self, event=None):
        self.var.set(not self.var.get())
        self._draw()
        if callable(self.command):
            self.command()

    def _draw(self):
        c = self._box
        c.delete("all")
        s = self._size
        on = bool(self.var.get())
        c.create_rectangle(2, 2, s - 2, s - 2,
                           outline=(ACCENT if on else "#9aa3b0"),
                           width=max(1, px(2)),
                           fill=(ACCENT if on else self._bg))
        if on:
            c.create_line(5, s * 0.52, s * 0.42, s - 6, s - 5, 5,
                          fill="#ffffff", width=max(2, px(3)),
                          capstyle="round", joinstyle="round")

    def configure(self, **kw):
        # 兼容 .config(text=...) 的写法
        if "text" in kw:
            self._lbl.configure(text=kw["text"])
        rest = {k: v for k, v in kw.items() if k != "text"}
        if rest:
            super().configure(**rest)


class ConverterApp:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self._pending_files = []
        self._previewed = None       # 已经送出预览的那个文件，避免重复载入打断视角
        self._drop_event = threading.Event()
        self._pending_drop = None
        self.last_out_dir = None
        self.last_pmx = None
        self.last_model = None
        self.last_mesh = None
        self._img = None
        self._drag_hot = False

        self.var_scale_auto = tk.BooleanVar(value=True)
        self.var_scale_raw = tk.BooleanVar(value=False)
        self.var_scale_custom = tk.BooleanVar(value=False)
        self.var_scale_value = tk.StringVar(value="1.0")
        self.var_flipz = tk.BooleanVar(value=True)
        self.var_bones = tk.BooleanVar(value=False)
        self.var_open_after = tk.BooleanVar(value=False)
        self.var_same_dir = tk.BooleanVar(value=True)
        self.var_outdir = tk.StringVar(value="")
        self.var_zoom = tk.StringVar(value="自动（跟随系统）")
        self.var_task = tk.StringVar(value=TASK_LABELS[0])
        self.var_vrm_spec = tk.StringVar(value="1.0")
        self.var_morphs = tk.BooleanVar(value=True)
        self.var_center = tk.BooleanVar(value=True)
        self.var_fbx_morphs = tk.BooleanVar(value=True)
        self.var_alpha_mode = tk.StringVar(value=alpha_label("auto"))
        self.var_auto_facing = tk.BooleanVar(value=True)
        self.var_face180 = tk.BooleanVar(value=False)
        self.var_two_sided = tk.BooleanVar(value=False)
        self.var_edge = tk.BooleanVar(value=False)
        # UE（UEFormat / .uemodel）
        self.var_ue_fbx = tk.BooleanVar(value=False)
        self.var_ue_alpha = tk.BooleanVar(value=True)
        self.var_ue_align = tk.BooleanVar(value=False)
        self.var_ue_align_ref = tk.StringVar(value="")
        self.var_ue_height = tk.StringVar(value="180")
        # PSK / PSKX（Unreal ActorX）
        self.var_psk_jp = tk.BooleanVar(value=True)
        self.var_psk_ik = tk.BooleanVar(value=True)
        self.var_psk_morphs = tk.BooleanVar(value=True)
        self.var_psk_fbx = tk.BooleanVar(value=False)
        self.var_psk_alpha = tk.BooleanVar(value=True)
        # PMX → psk / pskx
        self.var_psk_out = tk.StringVar(value=PSK_OUTS[0])
        self.var_psk_height = tk.StringVar(value="160")
        self.var_psk_tex = tk.BooleanVar(value=True)
        # XPS / XNALara
        self.var_xps_jp = tk.BooleanVar(value=True)
        self.var_xps_ik = tk.BooleanVar(value=True)
        self.var_xps_double = tk.BooleanVar(value=True)
        self.var_xps_unused = tk.BooleanVar(value=True)
        self.var_xps_fbx = tk.BooleanVar(value=False)
        self.var_subfolder = tk.BooleanVar(value=True)
        self._task_code = "auto"             # 任务方向内部代号（与语言无关）
        self.var_lang = tk.StringVar(value="zh_CN")
        self.zoom = None                      # None = 跟随系统 DPI
        self.dnd_ok = None
        self._split = 0.60                    # 左右分栏比例（预览占右侧 40%）
        self._vsplit = None                   # 左栏上下比例（None = 按内容自动）

        self._load_settings()
        self._build()
        self.root.after(60, self._pump)
        self.root.after(80, self._poll_drop)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------- layout --
    def _build(self):
        r = self.root
        r.title("%s  v%s" % (t("app_title"), __version__))
        r.configure(bg=BG)
        # 右侧要放整条预览栏，窗口下限宽度得留够
        w = max(px(920), min(px(1240), r.winfo_screenwidth() - px(60)))
        h = max(px(600), min(px(800), r.winfo_screenheight() - px(100)))
        r.geometry("%dx%d" % (1280, 1400))	# 初始窗口大小
        r.minsize(min(px(860), w), min(px(560), h))

        outer = tk.Frame(r, bg=BG)
        outer.pack(fill="both", expand=True, padx=px(16), pady=px(14))

        # ---- header
        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        toprow = tk.Frame(head, bg=BG)
        toprow.pack(fill="x")
        tk.Label(toprow, text=t("app_title").split(" · ")[0], font=F_TITLE,
                 bg=BG, fg=TXT).pack(side="left")
        # 右上角：语言 + 界面缩放
        rbox = tk.Frame(toprow, bg=BG)
        rbox.pack(side="right")
        self.var_lang = tk.StringVar(value=LANG_NAMES.get(APP_LANG, "简体中文"))
        tk.Label(rbox, text=t("lang_label"), font=F_SMALL, bg=BG,
                 fg=MUTED).pack(side="left", padx=(0, px(6)))
        self.om_lang = tk.OptionMenu(rbox, self.var_lang,
                                     *list(LANG_NAMES.values()),
                                     command=self._on_lang)
        self._style_om(self.om_lang, width=11)
        self.om_lang.pack(side="left", padx=(0, px(10)))
        tk.Label(rbox, text=t("zoom_label"), font=F_SMALL, bg=BG,
                 fg=MUTED).pack(side="left", padx=(0, px(6)))
        self.om_zoom = tk.OptionMenu(rbox, self.var_zoom,
                                     *[lbl for lbl, _ in ZOOM_CHOICES],
                                     command=self._on_zoom)
        self._style_om(self.om_zoom, width=14)
        self.om_zoom.pack(side="left")
        # 副标题行右侧：版本号 + 「检查更新」（版本号取自 main.py 顶部的 __version__）
        # 先 pack 右侧控件，保证窄窗口时被挤掉的是副标题而不是按钮。
        subrow = tk.Frame(head, bg=BG)
        subrow.pack(fill="x", pady=(px(2), 0))
        self.btn_upd = self._btn(subrow, t("btn_update"), self.check_update,
                                 kind="ghost", padx=px(10), pady=px(2))
        self.btn_upd.pack(side="right")
        tk.Label(subrow, text="v" + __version__, font=F_SMALL, bg=BG,
                 fg=FAINT).pack(side="right", padx=(0, px(10)))
        tk.Label(subrow, text=t("subtitle"), font=F_SUB, bg=BG, fg=MUTED,
                 anchor="w").pack(side="left", fill="x", expand=True)

        # ---- 主体：左右分栏（Blender 那种可以拖动分割条的排版）
        # 左栏 = 拖放 + 选项 + 按钮 + 日志；右栏 = 整条高度的模型预览。
        # 分割条位置会记进 config.json，下次打开还在原来的地方。
        self.panes = tk.PanedWindow(outer, orient="horizontal", bg="#d3dae4",
                                    bd=0, sashwidth=px(10), sashrelief="flat",
                                    sashpad=0, handlesize=px(6),
                                    opaqueresize=True)
        self.panes.pack(fill="both", expand=True, pady=(px(12), 0))
        leftwrap = tk.Frame(self.panes, bg=BG)
        self.panes.add(leftwrap, minsize=px(430), stretch="always",
                       width=px(620))
        right = tk.Frame(self.panes, bg=BG)
        self.panes.add(right, minsize=px(300), stretch="always", width=px(420))
        self._restore_split()

        # ---- 左栏再横切一刀：上段 = 拖放区 / 文件列表 / 选项 / 按钮，下段 = 日志
        # 为什么必须再切：200% 缩放下上段的自然高度（拖放区 150 + 文件列表 324
        # + 选项 558 + 按钮行 89 = 1121px）已经接近整窗可用的 1216px，日志再想
        # 分一杯羹就只能把选项页裁掉。放进 PanedWindow 后上段能保住自己的高度，
        # 日志拿剩下的，用户还能拖这条横向分割条自己分配（比例记进 config.json）。
        self.vpanes = tk.PanedWindow(leftwrap, orient="vertical", bg="#d3dae4",
                                     bd=0, sashwidth=px(10), sashrelief="flat",
                                     sashpad=0, handlesize=px(6),
                                     opaqueresize=True)
        self.vpanes.pack(fill="both", expand=True)
        top = tk.Frame(self.vpanes, bg=BG)
        self.vpanes.add(top, minsize=px(300), stretch="always")
        left = tk.Frame(self.vpanes, bg=BG)          # 下半段：日志
        self.vpanes.add(left, minsize=px(58), stretch="always")

        # ---- 日志（放在下半段）
        logbox = tk.Frame(left, bg=CARD, highlightthickness=1,
                          highlightbackground=BORDER)
        logbox.pack(fill="both", expand=True)
        lhead = tk.Frame(logbox, bg=CARD)
        lhead.pack(fill="x", padx=px(10), pady=(px(8), px(4)))
        tk.Label(lhead, text=t("log_title"), font=F_BOLD, bg=CARD, fg=TXT,
                 anchor="w").pack(side="left")
        # 状态文字放这儿而不是按钮行：高分屏（200% 缩放）下按钮行本来就装不下
        # 「按钮 + 状态」，实测中文溢出 109px、英文 329px。挂在日志卡片标题右侧
        # 既不额外占高度，也不会被裁掉。
        self.lbl_status = tk.Label(lhead, text=t("status_wait"), font=F_SMALL,
                                   bg=CARD, fg=MUTED)
        self.lbl_status.pack(side="right")
        self.txt = tk.Text(logbox, font=F_LOG, bg=CARD, fg=TXT, relief="flat",
                           wrap="none", height=6, highlightthickness=0,
                           padx=px(10), pady=px(8), insertbackground=TXT)
        sb = tk.Scrollbar(logbox, command=self.txt.yview, relief="flat",
                          bd=0, width=px(14))
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("info", foreground=MUTED)
        self.txt.tag_configure("ok", foreground=OK)
        self.txt.tag_configure("err", foreground=ERR)
        self.txt.tag_configure("warn", foreground=WARN)
        self.txt.tag_configure("head", foreground=ACCENT,
                               font=(FONT, 10, "bold"))
        self.txt.tag_configure("mono", foreground="#3a4250")
        self.txt.configure(state="disabled")

        act = tk.Frame(top, bg=BG)
        act.pack(side="bottom", fill="x", pady=(px(8), 0))
        self.btn_go = self._btn(act, t("start"), self.start_from_ui,
                                kind="primary")
        self.btn_go.pack(side="left")
        self.btn_open = self._btn(act, t("open_out"), self.open_outdir,
                                  kind="ghost", padx=px(12))
        self.btn_open.pack(side="left", padx=(px(8), 0))
        self.btn_clear = self._btn(act, t("clear"), self.clear_log, kind="ghost",
                                   padx=px(12))
        self.btn_clear.pack(side="left", padx=(px(8), 0))

        # ---- drop zone
        self._dz_h = px(DZ_H_EMPTY)   # 拖放区当前高度（有文件时会压扁）
        self.dz = tk.Canvas(top, height=px(DZ_H_EMPTY), bg=CARD,
                            highlightthickness=1,
                            highlightbackground=BORDER, cursor="hand2")
        self.dz.pack(fill="x", pady=(px(8), 0))
        self.dz.bind("<Configure>", lambda e: self._draw_dropzone())
        self.dz.bind("<Enter>", lambda e: self._hover(True))
        self.dz.bind("<Leave>", lambda e: self._hover(False))
        self.dz.bind("<Button-1>", lambda e: self.browse())

        # ---- 已选择文件列表（拖入后一目了然；空列表时整块隐藏，不占地方）
        self._files_shown = False
        self.filecard = tk.Frame(top, bg=CARD, highlightthickness=1,
                                 highlightbackground=BORDER)
        fh = tk.Frame(self.filecard, bg=CARD)
        fh.pack(fill="x", padx=px(10), pady=(px(8), px(2)))
        tk.Label(fh, text=t("files_title"), font=F_BOLD, bg=CARD,
                 fg=TXT).pack(side="left")
        self.lbl_files = tk.Label(fh, text="", font=F_SMALL, bg=CARD, fg=MUTED)
        self.lbl_files.pack(side="left", padx=(px(8), px(6)), pady=(px(2), 0))
        self.btn_files_clear = self._btn(fh, t("files_clear"),
                                         self.clear_files, kind="ghost",
                                         padx=px(10), pady=px(2))
        self.btn_files_clear.pack(side="right")
        self.btn_files_del = self._btn(fh, t("files_remove"),
                                       self.remove_selected_files,
                                       kind="ghost", padx=px(10), pady=px(2))
        self.btn_files_del.pack(side="right", padx=(0, px(6)))
        ftv = tk.Frame(self.filecard, bg=CARD)
        ftv.pack(fill="x", padx=px(10), pady=(0, px(8)))
        self.tv = ttk.Treeview(ftv, columns=("name", "type", "size"),
                               show="headings", height=6,
                               style="Files.Treeview", selectmode="extended")
        self.tv.heading("name", text=t("files_col_name"), anchor="w")
        self.tv.heading("type", text=t("files_col_type"), anchor="center")
        self.tv.heading("size", text=t("files_col_size"), anchor="e")
        self.tv.column("name", width=px(250), minwidth=px(120),
                       anchor="w", stretch=True)
        self.tv.column("type", width=px(58), minwidth=px(48),
                       anchor="center", stretch=False)
        self.tv.column("size", width=px(78), minwidth=px(64),
                       anchor="e", stretch=False)
        tv_sb = ttk.Scrollbar(ftv, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=tv_sb.set)
        tv_sb.pack(side="right", fill="y")
        self.tv.pack(side="left", fill="both", expand=True)
        self.tv.bind("<Double-1>", self._preview_row)
        self.tv.bind("<Delete>", lambda e: self.remove_selected_files())

        # ---- options（Notebook 分页：为将来加功能预留扩展空间）
        # 每个页签的内容都在可滚动容器里（见 _tab），所以这里用 expand=True 让
        # Notebook 吃掉剩余竖向空间：窗口大时页面铺满，窗口小时页面自己出滚动条，
        # 而不是被下面的按钮行挤掉。
        self._scroll_canvases = []
        nb = ttk.Notebook(top)
        nb.pack(fill="both", expand=True, pady=(px(8), 0))
        self.nb = nb
        self._style_notebook(nb)
        # 页签的滚轮滚动（挂在窗口上，不抢预览控件的滚轮缩放）。
        # _rebuild() 会再跑一遍 _build()，绑过一次就不要再绑，否则一次滚轮滚两格。
        if not getattr(self, "_wheel_bound", False):
            self._wheel_bound = True
            self.root.bind("<MouseWheel>", self._on_wheel, add="+")
            self.root.bind("<Button-4>", self._on_wheel, add="+")
            self.root.bind("<Button-5>", self._on_wheel, add="+")

        # 通用
        self.var_task.set(TASK_BY_LABEL.get(self._task_code, TASK_LABELS[0]))
        tab_g = self._tab(nb, t("tab_general"))
        row0 = tk.Frame(tab_g, bg=CARD)
        row0.pack(fill="x", padx=px(14), pady=(px(12), 0))
        tk.Label(row0, text=t("task"), font=F_BOLD, bg=CARD, fg=TXT,
                 width=8, anchor="w").pack(side="left")
        self.om_task = tk.OptionMenu(row0, self.var_task, *TASK_LABELS,
                                     command=self._on_task)
        self._style_om(self.om_task, width=26)
        self.om_task.pack(side="left")
        # 分栏后左栏会变窄，提示文字单独占一行并跟着栏宽自动折行
        self.lbl_hint = tk.Label(tab_g, text="", font=F_SMALL, bg=CARD,
                                 fg=MUTED, justify="left", anchor="w")
        self.lbl_hint.pack(fill="x", padx=px(14), pady=(px(6), 0))
        tab_g.bind("<Configure>",
                   lambda e: self.lbl_hint.configure(
                       wraplength=max(px(150), e.width - px(28))))

        row1 = tk.Frame(tab_g, bg=CARD)
        row1.pack(fill="x", padx=px(14), pady=(px(8), 0))
        tk.Label(row1, text=t("scale"), font=F_BOLD, bg=CARD, fg=TXT,
                 width=8, anchor="w").pack(side="left")
        for text, var in ((t("scale_auto"), self.var_scale_auto),
                          (t("scale_raw"), self.var_scale_raw),
                          (t("scale_custom"), self.var_scale_custom)):
            rb = tk.Radiobutton(row1, text=text, variable=var, value=True,
                                bg=CARD, fg=TXT, font=F_BODY,
                                activebackground=CARD, activeforeground=TXT,
                                selectcolor=CARD, highlightthickness=0, bd=0,
                                command=self._pick_scale)
            rb.pack(side="left", padx=(0, px(14)))
        self.ent_scale = tk.Entry(row1, textvariable=self.var_scale_value,
                                  width=8, font=F_BODY, bg=CARD, fg=TXT,
                                  relief="solid", bd=1, justify="center",
                                  disabledbackground="#f0f2f6",
                                  highlightthickness=0, insertbackground=TXT)
        self.ent_scale.pack(side="left", padx=(px(8), 0))

        self._bigcheck(tab_g, t("opt_flipz"), self.var_flipz)
        self._bigcheck(tab_g, t("opt_bones"), self.var_bones,
                       command=self._rerender)
        self._bigcheck(tab_g, t("opt_open"), self.var_open_after)
        self._bigcheck(tab_g, t("opt_twosided"), self.var_two_sided)

        # FBX 选项
        tab_f = self._tab(nb, t("tab_fbx"))
        self._bigcheck(tab_f, t("opt_center"), self.var_center)
        self._bigcheck(tab_f, t("opt_fbxmorph"), self.var_fbx_morphs)
        self._bigcheck(tab_f, t("opt_facing"), self.var_auto_facing,
                       command=self._rerender)
        self._bigcheck(tab_f, t("opt_face180"), self.var_face180,
                       command=self._rerender)
        row_a = tk.Frame(tab_f, bg=CARD)
        row_a.pack(anchor="w", padx=px(14), pady=(px(4), 0))
        tk.Label(row_a, text=t("opt_alpha"), font=F_BODY, bg=CARD,
                 fg=TXT).pack(side="left", padx=(0, px(8)))
        self.om_alpha = tk.OptionMenu(row_a, self.var_alpha_mode,
                                      *alpha_labels())
        self._style_om(self.om_alpha, width=18)
        self.om_alpha.pack(side="left")
        tk.Label(tab_f, text=t("note_alpha"), font=F_SUB, bg=CARD,
                 fg=MUTED).pack(anchor="w", padx=px(14),
                                pady=(px(2), px(2)))
        tk.Label(tab_f, text=t("note_winding"), font=F_SUB, bg=CARD,
                 fg=MUTED).pack(anchor="w", padx=px(14),
                                pady=(px(2), px(12)))

        # VRM 选项
        tab_v = self._tab(nb, t("tab_vrm"))
        rowv = tk.Frame(tab_v, bg=CARD)
        rowv.pack(anchor="w", padx=px(14), pady=(px(12), 0))
        tk.Label(rowv, text=t("vrm_version"), font=F_BODY, bg=CARD,
                 fg=TXT).pack(side="left")
        self.om_spec = tk.OptionMenu(rowv, self.var_vrm_spec, *VRM_SPECS)
        self._style_om(self.om_spec, width=6)
        self.om_spec.pack(side="left", padx=(px(8), 0))
        self._bigcheck(tab_v, t("opt_morphs"), self.var_morphs)

        # UE 选项（UEFormat / .uemodel）
        tab_u = self._tab(nb, t("tab_ue"))
        self._bigcheck(tab_u, t("opt_ue_fbx"), self.var_ue_fbx)
        self._bigcheck(tab_u, t("opt_ue_alpha"), self.var_ue_alpha)
        self._bigcheck(tab_u, t("opt_ue_align"), self.var_ue_align,
                       command=self._pick_ue_align_ref)
        # 参考模型选择行
        rowal = tk.Frame(tab_u, bg=CARD)
        rowal.pack(fill="x", padx=px(28), pady=(px(2), 0))
        self.btn_ue_align = tk.Button(
            rowal, text=t("opt_ue_align_pick"), font=F_SUB, bg=CARD, fg=ACCENT,
            relief="solid", bd=1, highlightthickness=0, cursor="hand2",
            command=self._choose_ue_align_ref)
        self.btn_ue_align.pack(side="left")
        self.lbl_ue_align = tk.Label(rowal, text=t("opt_ue_align_none"),
                                     font=F_SUB, bg=CARD, fg=MUTED,
                                     anchor="w", justify="left")
        self.lbl_ue_align.pack(side="left", padx=(px(8), 0))
        tk.Label(tab_u, text=t("note_ue_align"), font=F_SUB, bg=CARD,
                 fg=MUTED, justify="left", anchor="w", wraplength=px(520)
                 ).pack(fill="x", padx=px(28), pady=(px(2), px(6)))
        self._sync_ue_align_ui()
        self.lbl_ue_note = tk.Label(tab_u, text=t("note_ue"), font=F_SUB,
                                    bg=CARD, fg=MUTED, justify="left",
                                    anchor="w")
        self.lbl_ue_note.pack(fill="x", padx=px(14), pady=(px(6), px(12)))
        tab_u.bind("<Configure>",
                   lambda e: self.lbl_ue_note.configure(
                       wraplength=max(px(150), e.width - px(28))))

        # PSK 选项（Unreal ActorX / .psk / .pskx）
        tab_p = self._tab(nb, t("tab_psk"))
        self._bigcheck(tab_p, t("opt_psk_jp"), self.var_psk_jp)
        self._bigcheck(tab_p, t("opt_psk_ik"), self.var_psk_ik)
        self._bigcheck(tab_p, t("opt_psk_morphs"), self.var_psk_morphs)
        self._bigcheck(tab_p, t("opt_psk_fbx"), self.var_psk_fbx)
        self._bigcheck(tab_p, t("opt_psk_alpha"), self.var_psk_alpha)
        self.lbl_psk_note = tk.Label(tab_p, text=t("note_psk"), font=F_SUB,
                                     bg=CARD, fg=MUTED, justify="left",
                                     anchor="w")
        self.lbl_psk_note.pack(fill="x", padx=px(14), pady=(px(6), px(12)))
        tab_p.bind("<Configure>",
                   lambda e: self.lbl_psk_note.configure(
                       wraplength=max(px(150), e.width - px(28))))

        # XPS 选项（XNALara / XPS，.xps）
        tab_x = self._tab(nb, t("tab_xps"))
        self._bigcheck(tab_x, t("opt_xps_jp"), self.var_xps_jp)
        self._bigcheck(tab_x, t("opt_xps_ik"), self.var_xps_ik)
        self._bigcheck(tab_x, t("opt_xps_double"), self.var_xps_double)
        self._bigcheck(tab_x, t("opt_xps_unused"), self.var_xps_unused)
        self._bigcheck(tab_x, t("opt_xps_fbx"), self.var_xps_fbx)
        self._bigcheck(tab_x, t("opt_edge"), self.var_edge)
        self.lbl_xps_note = tk.Label(tab_x, text=t("note_xps"), font=F_SUB,
                                     bg=CARD, fg=MUTED, justify="left",
                                     anchor="w")
        self.lbl_xps_note.pack(fill="x", padx=px(14), pady=(px(6), px(12)))
        tab_x.bind("<Configure>",
                   lambda e: self.lbl_xps_note.configure(
                       wraplength=max(px(150), e.width - px(28))))

        # 输出
        tab_o = self._tab(nb, t("tab_output"))
        self._bigcheck(tab_o, t("opt_subfolder"), self.var_subfolder)
        self._bigcheck(tab_o, t("opt_samedir"), self.var_same_dir,
                       command=self._toggle_outdir)
        rowo = tk.Frame(tab_o, bg=CARD)
        rowo.pack(fill="x", padx=px(14), pady=(px(8), px(12)))
        self.ent_out = tk.Entry(rowo, textvariable=self.var_outdir, font=F_BODY,
                                bg=CARD, fg=TXT, relief="solid", bd=1,
                                disabledbackground="#f0f2f6",
                                highlightthickness=0, insertbackground=TXT)
        self.ent_out.pack(side="left", fill="x", expand=True,
                          padx=(0, px(6)))
        self.btn_browse_out = self._btn(rowo, t("browse"), self.pick_outdir,
                                        kind="ghost", padx=px(12), pady=px(4))
        self.btn_browse_out.pack(side="left")

        # ---- 右栏：模型预览（整列高度，跟 Blender 的 3D 视图区一个位置）
        pvcard = self._card(right, t("preview_title"))
        pvcard.pack(fill="both", expand=True)
        pvbox = tk.Frame(pvcard, bg=CARD,
                         highlightthickness=1, highlightbackground=BORDER)
        pvbox.pack(fill="both", expand=True, padx=px(8), pady=(px(2), px(6)))
        self.pv = model_preview.Preview3D(
            pvbox, size=PREVIEW_BASE, uiscale=UI_SCALE,
            labels=model_preview.pv_labels(
                front=t("pv_front"), left=t("pv_left"), back=t("pv_back"),
                top=t("pv_top"), reset=t("pv_reset"), spin=t("pv_spin"),
                bone=t("pv_bone"), wire=t("pv_wire"),
                placeholder=t("pv_placeholder")))
        self.pv.pack(fill="both", expand=True)
        self.pv.set_bones(bool(self.var_bones.get()))
        self.lbl_stats = tk.Label(pvcard, text=t("preview_stats"),
                                  font=F_SMALL, bg=CARD, fg=MUTED,
                                  justify="left", wraplength=px(360))
        self.lbl_stats.pack(anchor="w", padx=px(10), pady=(0, px(10)))

        # ---- progress
        self.pb = tk.Frame(r, bg=BORDER, height=px(4))
        self.pb.pack(fill="x", side="bottom")
        self.pb_fill = tk.Frame(self.pb, bg=ACCENT, height=px(4))
        self.pb_fill.place(x=0, y=0, relwidth=0.0, relheight=1.0)
        self._anim = None

        self._toggle_outdir()
        self._pick_scale()
        self._update_hint()
        self._refresh_files_ui()       # 重建界面后文件列表还在（语言 / 缩放切换）
        if self._pending_files:
            # 状态栏也别退回「等待文件」：列表里明明已经有文件了
            n = len(self._pending_files)
            self.status(t("status_process", n=n) if self.busy
                        else t("status_staged", n=n))
        self._draw_dropzone()
        # 分割条位置要等窗口有真实宽度之后才能落到正确的地方
        self.root.after(80, self._poll_split)
        # 拖放注册必须等所有子控件的原生窗口真正创建后再做，见 _schedule_dnd。
        self._schedule_dnd()

    # --------------------------------------------------------- split pane --
    def _restore_split(self):
        """恢复上次记下的左右分栏比例（要到窗口有尺寸之后才生效）。"""
        try:
            frac = getattr(self, "_split", None)
            total = self.panes.winfo_width()
            if total > 1 and frac:
                x = int(total * frac)
                self.panes.sash_place(0, x, 0)
                return True
            return total > 1
        except Exception:
            return True

    def _poll_split(self, tries=0):
        ok = self._restore_split()
        okv = self._restore_vsplit()
        if not (ok and okv) and tries < 20:
            self.root.after(60, lambda: self._poll_split(tries + 1))

    def _save_split(self):
        try:
            total = self.panes.winfo_width()
            x, _y = self.panes.sash_coord(0)
            if total > 1 and x > 0:
                self._split = min(0.85, max(0.28, x / float(total)))
        except Exception:
            pass

    # --------------------------------------------------- 左栏上下分割条 --
    def _restore_vsplit(self):
        """恢复（或首次计算）左栏上下分割条的位置。

        默认策略：上段（拖放区 + 文件列表 + 选项 + 按钮）先拿到它需要的高度，
        日志分剩下的（≥ 110px）。200% 缩放下上段本身就要 1121px，靠这条分割条
        才能保住选项页不被裁掉；用户拖过之后就按用户的比例来（存 config.json）。
        """
        try:
            total = self.vpanes.winfo_height()
            if total <= 1:
                return False
            self._place_vsplit(total)
            return True
        except Exception:
            return True

    def _place_vsplit(self, total=None):
        try:
            total = total or self.vpanes.winfo_height()
            if total <= 1:
                return
            frac = getattr(self, "_vsplit", None)     # 用户拖过 → 用用户的比例
            if frac:
                y = int(total * frac)
            else:
                need = sum(w.winfo_reqheight() for w in
                           (self.dz, self.nb, self.btn_go.master))
                if getattr(self, "_files_shown", False):
                    need += self.filecard.winfo_reqheight()
                need += px(70)        # 各块之间的间距 + 分割条自身
                y = min(need, total - px(58))
            y = max(px(150), min(y, total - px(58)))
            self.vpanes.sash_place(0, 0, y)
        except Exception:
            pass

    def _save_vsplit(self):
        try:
            total = self.vpanes.winfo_height()
            _x, y = self.vpanes.sash_coord(0)
            if total > 1 and y > 0:
                self._vsplit = min(0.92, max(0.15, y / float(total)))
        except Exception:
            pass

    def _card(self, parent, title):
        box = tk.Frame(parent, bg=CARD, highlightthickness=1,
                       highlightbackground=BORDER)
        tk.Label(box, text=title, font=F_BOLD, bg=CARD, fg=TXT,
                 anchor="w").pack(fill="x", padx=px(10), pady=(px(8), px(4)))
        return box

    def _btn(self, parent, text, cmd, kind="ghost", padx=None, pady=None):
        # 默认值在调用时才算，才能在缩放变化后生效
        padx = px(16) if padx is None else padx
        pady = px(8) if pady is None else pady
        if kind == "primary":
            bg, fg, active = ACCENT, "#ffffff", ACCENT_HOVER
        elif kind == "soft":
            bg, fg, active = ACCENT_SOFT, ACCENT, "#d6e4ff"
        else:
            bg, fg, active = CARD, TXT, "#eef1f5"
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                      activebackground=active, activeforeground=fg,
                      relief="flat", bd=0, highlightthickness=1,
                      highlightbackground=BORDER if kind == "ghost" else bg,
                      font=F_BOLD if kind == "primary" else F_BODY,
                      padx=padx, pady=pady, cursor="hand2")
        if kind == "ghost":
            b.bind("<Enter>", lambda e: b.configure(bg=active))
            b.bind("<Leave>", lambda e: b.configure(bg=bg))
        b._base_bg = bg
        return b

    def _style_om(self, om, width):
        om.configure(bg=CARD, fg=TXT, font=F_BODY, bd=1, relief="solid",
                     highlightthickness=0, activebackground=CARD,
                     activeforeground=TXT, width=width, anchor="w", cursor="hand2")
        om["menu"].configure(bg=CARD, fg=TXT, font=F_BODY,
                             activebackground=ACCENT_SOFT, activeforeground=TXT, bd=0)

    def _style_notebook(self, nb):
        try:
            st = ttk.Style()
            try:
                if st.theme_use() != "clam":
                    st.theme_use("clam")
            except Exception:
                pass
            st.configure("TNotebook", background=BG, borderwidth=0)
            st.configure("TNotebook.Tab", font=F_BODY, padding=(px(12), px(6)),
                         background=CARD, foreground=TXT, borderwidth=1,
                         relief="flat")
            st.map("TNotebook.Tab",
                   background=[("selected", ACCENT), ("active", ACCENT_SOFT)],
                   foreground=[("selected", "#ffffff"), ("active", TXT)])
            self._style_tree(st)
        except Exception:
            pass

    def _style_tree(self, st=None):
        """文件列表（ttk.Treeview）的白底浅色样式，跟其余控件统一。"""
        try:
            st = st or ttk.Style()
            st.configure("Files.Treeview",
                         background=CARD, fieldbackground=CARD,
                         foreground=TXT, font=F_BODY,
                         rowheight=px(22), borderwidth=0, relief="flat")
            st.configure("Files.Treeview.Heading",
                         background="#eef1f5", foreground=MUTED,
                         font=F_SMALL, relief="flat", borderwidth=0,
                         padding=(px(4), px(3)))
            st.map("Files.Treeview",
                   background=[("selected", ACCENT_SOFT)],
                   foreground=[("selected", ACCENT_HOVER)])
            st.map("Files.Treeview.Heading",
                   background=[("active", "#e4e9f0")])
            # 布局沿用父样式 Treeview（ttk 的 "Files.Treeview" 会自动继承），
            # 不用自己拼 element 名，避免拼错时静默失效。
        except Exception:
            pass

    def _tab(self, nb, title):
        """建一个页签，**内容放在可滚动容器里**。

        为什么必须能滚：`top` 那一栏的排版顺序是「按钮行(side=bottom) → 拖放区 →
        文件列表 → Notebook」，Notebook 是最后一个，竖向空间不够时 pack 会从它身上
        扣，表现就是「窗口调小以后页签下面的选项看不见了」（用户 2026-09-24 反馈）。
        现在页签内容装不下时右侧自动出现滚动条，鼠标滚轮也能滚，不会再被裁掉。

        返回的是内容容器（往它里面 pack 控件即可），调用方不用关心滚动的事。
        """
        outer = tk.Frame(nb, bg=CARD, highlightthickness=1,
                         highlightbackground=BORDER)
        nb.add(outer, text=title)

        # width 给个小值：真正的宽度由 fill="both"+expand 撑开，这里只是别让
        # Canvas 的默认请求宽度把左栏的最小宽度顶起来。
        cv = tk.Canvas(outer, bg=CARD, highlightthickness=0, bd=0,
                       width=px(120), height=px(150),
                       yscrollincrement=px(20))
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(cv, bg=CARD)
        win = cv.create_window(0, 0, window=inner, anchor="nw")

        def sync(_e=None):
            cv.configure(scrollregion=cv.bbox("all"))
            need = inner.winfo_reqheight() > cv.winfo_height() + px(2)
            if need and not sb.winfo_ismapped():
                sb.pack(side="right", fill="y")
            elif not need and sb.winfo_ismapped():
                sb.pack_forget()

        inner.bind("<Configure>", lambda e: sync())
        cv.bind("<Configure>",
                lambda e: (cv.itemconfigure(win, width=e.width), sync()))
        cv._scrollable = True
        self._scroll_canvases.append(cv)
        return inner

    def _on_wheel(self, e):
        """滚轮：指针落在哪个可滚动页签上就滚哪个（页签里的子控件也算）。

        不用 bind_all —— 预览控件自己绑了 <MouseWheel>（滚轮缩放），bind_all 会把
        它顶掉。这里挂在外层窗口上，事件先经过控件自己的绑定，到不了这儿的说明
        指针不在预览上；再从 e.widget 沿 master 往上找页签的画布。
        """
        w = e.widget
        while w is not None:
            if getattr(w, "_scrollable", False):
                first, last = w.yview()
                if first > 0.0 or last < 1.0:
                    delta = getattr(e, "delta", 0)
                    if delta == 0:
                        delta = 120 if getattr(e, "num", 0) == 4 else -120
                    w.yview_scroll(-1 if delta > 0 else 1, "units")
                    return "break"
                return
            w = getattr(w, "master", None)

    def _bigcheck(self, parent, text, var, command=None, pady=None):
        bc = BigCheck(parent, text=text, variable=var, command=command,
                      bg=CARD, font=F_BODY)
        bc.pack(anchor="w", padx=px(14),
                pady=(px(4) if pady is None else pady))
        return bc

    def _draw_dropzone(self):
        c = self.dz
        c.delete("all")
        n = len(self._pending_files)
        w = max(c.winfo_width(), px(300))
        # 高度用自己记的意图值（_dz_h）：canvas 高度固定，configure(height=)
        # 之后 winfo_height() 可能还是旧值，读它会画到框外。
        h = self._dz_h or px(DZ_H_COMPACT if n else DZ_H_EMPTY)
        hot = self._drag_hot
        c.create_rectangle(px(8), px(8), w - px(8), h - px(8),
                           outline=ACCENT if hot else "#ccd4e0",
                           width=max(1, px(2)), dash=(px(8), px(6)),
                           fill=ACCENT_SOFT if hot else "#fbfcfe")
        if n:
            # 已经有文件了：拖放区收成一条窄带，把竖向空间让给下面的文件列表
            c.create_text(px(30), h * 0.5, text="⇩", font=F_ICON2,
                          fill=ACCENT)
            c.create_text(px(56), h * 0.5, anchor="w", text=t("drop_more", n=n),
                          font=F_DROP2,
                          fill=ACCENT_HOVER if hot else TXT)
            return
        # 文本宽度跟着画布走：窄窗口里两行文案会自动折行，而不是被右边裁掉。
        # 三个锚点按「最多折成两行」留了间距（150px 高：图标 ~12-47、
        # 主提示折两行 51-99、格式行折两行 103-137），折行也不会互相压住。
        wrap = max(px(180), w - px(56))
        c.create_text(w / 2, h * 0.20, text="⇩", font=F_ICON,
                      fill=ACCENT)
        c.create_text(w / 2, h * 0.50, text=t("drop_hint"),
                      font=F_DROP, fill=TXT if not hot else ACCENT_HOVER,
                      width=wrap, justify="center")
        c.create_text(w / 2, h * 0.80,
                      text=t("drop_formats"),
                      font=F_DROP2, fill=MUTED,
                      width=wrap, justify="center")

    def _set_dz_compact(self, compact):
        """有文件时把拖放区压扁，让文件列表拿到竖向空间。"""
        want = px(DZ_H_COMPACT if compact else DZ_H_EMPTY)
        if self._dz_h == want:
            return
        self._dz_h = want
        try:
            self.dz.configure(height=want)
        except Exception:
            pass

    def _hover(self, state):
        if state != self._drag_hot:
            self._drag_hot = state
            self._draw_dropzone()

    def _schedule_dnd(self):
        """等窗口真正映射、子控件原生窗口都创建好之后，再注册拖放。

        关键点：`_build()` 里刚 pack 完控件时，Tk 还没把它们的原生子窗口创建
        出来（winfo_id() 此时只是父窗口 id，EnumChildWindows 也枚举不到）。若在此
        刻注册，会退化成「只有顶层注册」，拖到内容区仍然没反应。所以延迟到窗口
        映射之后，并先 update_idletasks() 强制创建所有子窗口，再枚举注册。
        """
        self._dnd_tries = 0
        self.root.after(120, self._do_install_dnd)

    def _do_install_dnd(self):
        try:
            # 强制 Tk 建好所有待创建的原生子窗口
            self.root.update_idletasks()
            ok = enable_drop(self.root.winfo_id(), self._on_drop_files,
                             register_children=True)
            self.dnd_ok = ok
            if not ok:
                self.log(t("msg_drop_unavail"), "warn")
                return
            # 窗口可能尚未映射（winfo_viewable 为假），此时枚举到的子窗口不全。
            # 隔一拍再补注册一次，覆盖后创建的控件；重建界面同理。
            self._dnd_tries += 1
            if not self.root.winfo_viewable() and self._dnd_tries < 8:
                self.root.after(200, self._do_install_dnd)
            else:
                # 追加一个延迟补注册，兜住布局稳定后才创建的子控件
                self.root.after(400, self._dnd_topup)
        except Exception:
            traceback.print_exc()

    def _dnd_topup(self):
        """布局稳定后再枚举一次，把此后新建的子控件也纳入拖放。"""
        try:
            if self.dnd_ok:
                enable_drop(self.root.winfo_id(), self._on_drop_files,
                            register_children=True)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------- ui zoom --
    def _on_zoom(self, label):
        self._set_zoom(ZOOM_BY_LABEL.get(label))

    def _on_lang(self, name):
        global APP_LANG
        code = None
        for k, v in LANG_NAMES.items():
            if v == name:
                code = k
                break
        if code is None or code == APP_LANG:
            return
        # OptionMenu 存的是本地化文案，切语言前先换算回内部代号，切完再按新文案写回
        _acode = alpha_code_of(self.var_alpha_mode.get())
        _pcode = psk_out_code_of(self.var_psk_out.get())
        APP_LANG = code
        refresh_choices()
        try:
            self.var_zoom.set(zoom_label(self.zoom))
        except Exception:
            pass
        try:
            self.var_alpha_mode.set(alpha_label(_acode))
        except Exception:
            pass
        try:
            self.var_psk_out.set(psk_out_label(_pcode))
        except Exception:
            pass
        self._save_settings()
        self._rebuild()

    def _on_task(self, label):
        self._task_code = TASK_BY_LABEL.get(self.var_task.get(), "auto")
        self._save_settings()
        self._update_hint()
        self._refilter_pending()

    def _update_hint(self):
        task = self._task_code
        tips = {
            "auto": t("hint_auto"),
            "fbx2pmx": t("hint_fbx2pmx"),
            "pmx2vrm": t("hint_pmx2vrm"),
            "uemodel2pmx": t("hint_uemodel2pmx"),
            "psk2pmx": t("hint_psk2pmx"),
            "xps2pmx": t("hint_xps2pmx"),
            "check": t("hint_check"),
        }
        if getattr(self, "lbl_hint", None) is not None:
            self.lbl_hint.configure(text=tips.get(task, ""))

    def _set_zoom(self, factor):
        old = UI_SCALE
        apply_scaling(self.root, factor)
        self.zoom = factor
        try:
            self.var_zoom.set(zoom_label(factor))
        except Exception:
            pass
        self._save_settings()
        if abs(UI_SCALE - old) < 1e-6:
            return
        self._rebuild()
        self.log(t("zoom_changed", label=zoom_label(factor),
                   pct=round(UI_SCALE * 100)), "info")

    def _rebuild(self):
        """按新的缩放倍率重建界面；日志内容和预览会保留。"""
        text = None
        if getattr(self, "txt", None) is not None:
            try:
                text = self.txt.get("1.0", "end-1c")
            except Exception:
                text = None
        self._progress(False)
        try:
            self._save_split()          # 重建前先记下当前分割位置
        except Exception:
            pass
        for child in self.root.winfo_children():
            child.destroy()
        self._build()
        if text:
            self.txt.configure(state="normal")
            self.txt.insert("1.0", text)
            self.txt.see("end")
            self.txt.configure(state="disabled")
        if self.busy:
            self.btn_go.configure(state="disabled", bg="#a9bde8")
            self._progress(True)
        self._restore_preview()

    def _restore_preview(self):
        if getattr(self, "last_mesh", None) is not None:
            self._show_mesh(self.last_mesh, self.last_pmx)

    # -------------------------------------------------------- settings io --
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                s = json.load(f)
            lang = s.get("lang", "zh_CN")
            if lang in LANG:
                global APP_LANG
                APP_LANG = lang
            refresh_choices()
            self.var_lang.set(LANG_NAMES.get(APP_LANG, "简体中文"))
            self.var_flipz.set(bool(s.get("flip_z", True)))
            self.var_bones.set(bool(s.get("show_bones", False)))
            self.var_open_after.set(bool(s.get("open_after", False)))
            self.var_same_dir.set(bool(s.get("same_dir", True)))
            self.var_outdir.set(s.get("outdir", "") or "")
            self.var_scale_value.set(str(s.get("scale_value", "1.0")))
            mode = s.get("scale_mode", "auto")
            self.var_scale_auto.set(mode == "auto")
            self.var_scale_raw.set(mode == "raw")
            self.var_scale_custom.set(mode == "custom")
            self.zoom = norm_zoom(s.get("ui_zoom"))
            self.var_zoom.set(zoom_label(self.zoom))
            try:
                sp = float(s.get("split", 0.6))
                if 0.2 < sp < 0.9:
                    self._split = sp
            except Exception:
                pass
            try:
                vs = s.get("vsplit")
                if vs is not None and 0.15 < float(vs) < 0.95:
                    self._vsplit = float(vs)
            except Exception:
                pass
            self.var_vrm_spec.set(s.get("vrm_spec", "1.0"))
            self.var_morphs.set(bool(s.get("export_morphs", True)))
            self.var_two_sided.set(bool(s.get("force_two_sided", False)))
            self.var_edge.set(bool(s.get("vrm2pmx_edge", False)))
            self.var_center.set(bool(s.get("center", True)))
            self.var_fbx_morphs.set(bool(s.get("fbx_morphs", True)))
            # alpha：新键 alpha_mode 优先；老配置只有布尔 remove_alpha 时按
            # true -> auto / false -> keep 迁移（老的 true 是「一律去 alpha」，
            # 正是本次要修的行为；迁到自动判定既保住意图又不再误伤真透明贴图）。
            if "alpha_mode" in s:
                code = fbx2pmx.alpha_mode_of(s.get("alpha_mode"))
            elif "remove_alpha" in s:
                code = fbx2pmx.alpha_mode_of(None,
                                             bool(s.get("remove_alpha")))
            else:
                code = "auto"
            self.var_alpha_mode.set(alpha_label(code))
            self.var_auto_facing.set(bool(s.get("auto_facing", True)))
            self.var_face180.set(bool(s.get("face_180", False)))
            self.var_ue_fbx.set(bool(s.get("ue_fbx", False)))
            self.var_ue_alpha.set(bool(s.get("ue_alpha", True)))
            self.var_ue_height.set(str(s.get("ue_height", "180")))
            self.var_psk_jp.set(bool(s.get("psk_jp", True)))
            self.var_psk_ik.set(bool(s.get("psk_ik", True)))
            self.var_psk_morphs.set(bool(s.get("psk_morphs", True)))
            self.var_psk_fbx.set(bool(s.get("psk_fbx", False)))
            self.var_psk_alpha.set(bool(s.get("psk_alpha", True)))
            self.var_psk_out.set(psk_out_label(
                s.get("psk_out", "pskx")) if s.get("psk_out") in PSK_OUT_CODES
                else PSK_OUTS[0])
            self.var_psk_height.set(s.get("psk_height", "160"))
            self.var_psk_tex.set(bool(s.get("psk_tex", True)))
            self.var_xps_jp.set(bool(s.get("xps_jp", True)))
            self.var_xps_ik.set(bool(s.get("xps_ik", True)))
            self.var_xps_double.set(bool(s.get("xps_double", True)))
            self.var_xps_unused.set(bool(s.get("xps_unused", True)))
            self.var_xps_fbx.set(bool(s.get("xps_fbx", False)))
            self.var_subfolder.set(bool(s.get("subfolder", True)))
            code = s.get("task", "auto")
            if code not in {c for _, c in TASK_CHOICES}:
                code = TASK_BY_LABEL.get(code, "auto")  # 兼容旧版以标签存储的设置
            if code not in {c for _, c in TASK_CHOICES}:
                code = "auto"
            self._task_code = code
            self.var_task.set(TASK_BY_LABEL.get(code, TASK_LABELS[0]))
        except Exception:
            pass

    def _save_settings(self):
        try:
            mode = ("auto" if self.var_scale_auto.get() else
                    "raw" if self.var_scale_raw.get() else "custom")
            try:
                self._save_split()
            except Exception:
                pass
            try:
                self._save_vsplit()
            except Exception:
                pass
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({"flip_z": self.var_flipz.get(),
                           "show_bones": self.var_bones.get(),
                           "open_after": self.var_open_after.get(),
                           "same_dir": self.var_same_dir.get(),
                           "outdir": self.var_outdir.get(),
                           "scale_mode": mode,
                           "scale_value": self.var_scale_value.get(),
                           "ui_zoom": ("auto" if self.zoom is None
                                       else self.zoom),
                           "lang": APP_LANG,
                           "task": self._task_code,
                           "vrm_spec": self.var_vrm_spec.get(),
                           "export_morphs": self.var_morphs.get(),
                           "force_two_sided": self.var_two_sided.get(),
                           "vrm2pmx_edge": self.var_edge.get(),
                           "center": self.var_center.get(),
                           "fbx_morphs": self.var_fbx_morphs.get(),
                           "alpha_mode": alpha_code_of(self.var_alpha_mode.get()),
                           "auto_facing": self.var_auto_facing.get(),
                           "face_180": self.var_face180.get(),
                           "ue_fbx": self.var_ue_fbx.get(),
                           "ue_alpha": self.var_ue_alpha.get(),
                           "ue_align": bool(self.var_ue_align.get()),
                           "ue_align_ref": self.var_ue_align_ref.get(),
                           "ue_height": self.var_ue_height.get(),
                           "psk_jp": self.var_psk_jp.get(),
                           "psk_ik": self.var_psk_ik.get(),
                           "psk_morphs": self.var_psk_morphs.get(),
                           "psk_fbx": self.var_psk_fbx.get(),
                           "psk_alpha": bool(self.var_psk_alpha.get()),
                           "psk_out": psk_out_code_of(self.var_psk_out.get()),
                           "psk_height": self.var_psk_height.get(),
                           "psk_tex": bool(self.var_psk_tex.get()),
                           "xps_jp": self.var_xps_jp.get(),
                           "xps_ik": self.var_xps_ik.get(),
                           "xps_double": self.var_xps_double.get(),
                           "xps_unused": self.var_xps_unused.get(),
                           "xps_fbx": self.var_xps_fbx.get(),
                           "subfolder": bool(self.var_subfolder.get()),
                           "split": getattr(self, "_split", 0.6),
                           "vsplit": getattr(self, "_vsplit", None)},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------ logging --
    def log(self, text, tag="mono"):
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n", tag)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def status(self, text):
        self.lbl_status.configure(text=text)

    def _progress(self, on):
        if on:
            self.pb_fill.place_configure(relwidth=0.15)
            self._animate()
        else:
            if self._anim:
                self.root.after_cancel(self._anim)
                self._anim = None
            self.pb_fill.place_configure(relwidth=0.0)

    def _animate(self):
        try:
            x = float(self.pb_fill.place_info().get("x") or 0)
        except Exception:
            x = 0
        width = self.pb.winfo_width() or 900
        step = 9
        x = (x + step) % (width + 200)
        self.pb_fill.place_configure(x=x - 140, relwidth=0.15)
        self._anim = self.root.after(28, self._animate)

    # ---------------------------------------------------------- ui handlers --
    def _pick_scale(self):
        custom = self.var_scale_custom.get()
        self.ent_scale.configure(state="normal" if custom else "disabled",
                                 fg=TXT if custom else FAINT)

    # -- uemodel 部件对齐到参考模型 ------------------------------------------
    def _pick_ue_align_ref(self):
        """勾选「自动对齐」时若还没选参考模型，直接弹选择框。"""
        if self.var_ue_align.get() and not self.var_ue_align_ref.get():
            self._choose_ue_align_ref()
        self._sync_ue_align_ui()

    def _choose_ue_align_ref(self):
        p = filedialog.askopenfilename(
            title=t("opt_ue_align_pick"),
            filetypes=[("UEFormat 模型", "*.uemodel"), ("所有文件", "*.*")])
        if p:
            self.var_ue_align_ref.set(p)
            self.var_ue_align.set(True)
            self._check_ue_align_ref(p)
        self._sync_ue_align_ui()

    def _check_ue_align_ref(self, path):
        """检查选中的参考模型是否是「整身（世界空间）」。

        对齐是单向的：把当前文件搬进参考模型的空间。用户很容易选反
        （拿部件当参考），那会把整身搬进部件的局部空间 —— 表现成
        「整个模型整体往下掉十几单位」。这里提前拦一下，转换前就提示。
        """
        try:
            ref = uemodelio.read_uemodel(path)
            spread = uemodel2pmx._bone_spread(ref)
            # 整身模型骨架铺开通常 >120cm；局部空间部件只有几十 cm
            if spread and spread < 120.0:
                messagebox.showwarning(
                    t("opt_ue_align_pick"),
                    t("msg_ue_align_weak_ref", name=os.path.basename(path),
                      spread=("%.0f" % spread)))
                self.var_ue_align.set(False)
        except Exception:
            pass  # 读不了就交给转换阶段处理

    def _sync_ue_align_ui(self):
        ref = self.var_ue_align_ref.get()
        on = bool(self.var_ue_align.get())
        if not ref:
            self.lbl_ue_align.configure(
                text=t("opt_ue_align_wait"), fg=FAINT)
        else:
            self.lbl_ue_align.configure(
                text=os.path.basename(ref), fg=(TXT if on else MUTED))
        try:
            self.btn_ue_align.configure(state="normal")
        except tk.TclError:
            pass

    def _toggle_outdir(self):
        same = self.var_same_dir.get()
        self.ent_out.configure(state="disabled" if same else "normal",
                               fg=FAINT if same else TXT)
        self.btn_browse_out.configure(state="disabled" if same else "normal")
        if same:
            self.ent_out.delete(0, "end")
            self.ent_out.insert(0, t("outdir_placeholder"))

    def browse(self):
        types = [("支持的模型文件",
                  "*.fbx *.unitypackage *.vrm *.pmx *.uemodel *.psk *.pskx"),
                 ("FBX 模型", "*.fbx"),
                 ("Unity 资源包", "*.unitypackage"),
                 ("VRM 模型", "*.vrm"),
                 ("PMX 模型", "*.pmx"),
                 ("UEFormat 模型", "*.uemodel"),
                 ("PSK / PSKX 模型", "*.psk *.pskx"),
                 ("所有文件", "*.*")]
        paths = filedialog.askopenfilenames(title=t("dlg_title"),
                                            filetypes=types)
        if paths:
            self.accept_paths(list(paths))

    def pick_outdir(self):
        d = filedialog.askdirectory(title=t("dlg_out"))
        if d:
            self.var_outdir.set(d)

    def open_outdir(self):
        target = self.last_out_dir
        if not target:
            if not self.var_same_dir.get() and self.var_outdir.get().strip():
                target = self.var_outdir.get().strip()
            else:
                target = BASE
        try:
            if os.path.isdir(target):
                os.startfile(target)
            else:
                messagebox.showinfo(t("msg_title"), t("msg_no_outdir"))
        except Exception as e:
            messagebox.showerror(t("msg_open_fail"), str(e))

    # --------------------------------------------------------- 检查更新 --
    def check_update(self):
        """后台查最新 tag（GitHub → Gitee 兜底），跟 __version__ 比大小。"""
        if getattr(self, "_upd_busy", False):
            return
        self._upd_busy = True
        try:
            self.btn_upd.configure(state="disabled")
        except Exception:
            pass
        self.log(t("upd_checking"), "info")
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        # 网络请求放后台线程，结果通过队列回主线程（见 _pump）
        try:
            tag, src = fetch_latest_version()
            self.q.put(("update", {"status": "ok", "tag": tag, "source": src}))
        except Exception as e:
            self.q.put(("update", {"status": "error",
                                   "msg": "%s: %s" % (type(e).__name__, e)}))

    def _on_update_result(self, info):
        # 整体兜底：这个回调跑在 _pump 的循环里，抛异常会把界面刷新一起带崩
        try:
            self._upd_busy = False
            try:
                self.btn_upd.configure(state="normal")
            except Exception:
                pass
            info = info or {}
            ok = info.get("status") == "ok"
            tag = info.get("tag") if ok else None
            src = SOURCE_LABEL.get(info.get("source"), "") if ok else ""
            via = t("upd_via", src=src) if src else ""
            if tag and parse_version(tag) > parse_version(__version__):
                self.log(t("upd_available_log", new=tag, cur=__version__)
                         + via, "head")
            elif tag:
                self.log(t("upd_latest", cur=__version__) + via, "ok")
            else:
                self.log(t("upd_unknown"), "warn")
            self._show_update_window(tag, info.get("msg", ""))
        except Exception as e:
            self.log(t("upd_fail", err="%s: %s" % (type(e).__name__, e)), "err")

    def _show_update_window(self, tag, err=""):
        """弹「当前版本 / 最新版本 / 下载地址」窗口。"""
        cur = __version__
        newer = bool(tag) and parse_version(tag) > parse_version(cur)
        if newer:
            head, head_fg = t("upd_found"), ACCENT
        elif tag:
            head, head_fg = t("upd_uptodate"), OK
        else:
            head, head_fg = t("upd_unknown"), WARN

        old = getattr(self, "_upd_win", None)      # 已经开着就先关掉，别叠窗
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self._upd_win = win
        win.title(t("upd_title"))
        win.configure(bg=BG)
        try:
            win.transient(self.root)
        except Exception:
            pass

        box = tk.Frame(win, bg=CARD, highlightthickness=1,
                       highlightbackground=BORDER)
        box.pack(fill="both", expand=True, padx=px(14), pady=px(14))
        pad = tk.Frame(box, bg=CARD)
        pad.pack(fill="both", expand=True, padx=px(18), pady=px(16))

        tk.Label(pad, text=head, font=F_BOLD, bg=CARD, fg=head_fg,
                 anchor="w").pack(fill="x")
        self._upd_vrow(pad, t("upd_cur"), version_label(cur), TXT)
        self._upd_vrow(pad, t("upd_new"),
                       version_label(tag) if tag else t("upd_na"),
                       head_fg if tag else MUTED)

        tk.Frame(pad, bg=BORDER, height=max(1, px(1))).pack(
            fill="x", pady=(px(12), 0))
        tk.Label(pad, text=t("upd_dl_head"), font=F_BOLD, bg=CARD, fg=TXT,
                 anchor="w").pack(fill="x", pady=(px(10), 0))
        for name, url, key in DOWNLOAD_SOURCES:
            self._upd_dlrow(pad, name, url, key)

        if err:
            tk.Label(pad, text=t("upd_fail", err=err), font=F_SMALL, bg=CARD,
                     fg=WARN, anchor="w", justify="left",
                     wraplength=px(540)).pack(fill="x", pady=(px(10), 0))

        foot = tk.Frame(pad, bg=CARD)
        foot.pack(fill="x", pady=(px(16), 0))
        self._btn(foot, t("upd_close"), win.destroy,
                  kind="soft").pack(side="right")

        win.bind("<Escape>", lambda e: win.destroy())
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.update_idletasks()
        try:
            w = max(win.winfo_reqwidth(), px(520))
            h = win.winfo_reqheight()
            x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
            win.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))
        except Exception:
            pass
        try:
            win.lift()
            win.focus_set()
        except Exception:
            pass

    def _upd_vrow(self, parent, label, value, color):
        r = tk.Frame(parent, bg=CARD)
        r.pack(fill="x", pady=(px(8), 0))
        tk.Label(r, text=label, font=F_BODY, bg=CARD, fg=MUTED,
                 anchor="w").pack(side="left")
        tk.Label(r, text=value, font=F_BOLD, bg=CARD, fg=color,
                 anchor="w").pack(side="left")

    def _upd_dlrow(self, parent, name, url, key):
        """一行下载地址：平台名 + 可点击链接（+ 提取码）+ 复制按钮。"""
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(px(8), 0))
        tk.Label(row, text=name, font=F_SMALL, bg=CARD, fg=MUTED,
                 width=10, anchor="w").pack(side="left")
        lnk = tk.Label(row, text=url, font=F_BODY, bg=CARD, fg=ACCENT,
                       anchor="w", cursor="hand2")
        lnk.pack(side="left")
        lnk.bind("<Button-1>", lambda e, u=url: self._open_url(u))
        lnk.bind("<Enter>", lambda e: lnk.configure(fg=ACCENT_HOVER))
        lnk.bind("<Leave>", lambda e: lnk.configure(fg=ACCENT))

        if key:
            tk.Label(row, text="%s%s" % (t("upd_key"), key), font=F_SMALL,
                     bg=CARD, fg=TXT).pack(side="left", padx=(px(10), 0))
        copy_text = url + (("\n" + key) if key else "")
        btn = self._btn(row, t("upd_copy"), None, kind="ghost",
                        padx=px(8), pady=px(1))
        btn.configure(font=F_SMALL,
                      command=lambda b=btn, tx=copy_text: self._copy_text(tx, b))
        btn.pack(side="right")

    def _copy_text(self, text, btn=None):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            return
        self.log(text.replace("\n", "  "), "info")
        if btn is not None:                        # 按钮上闪一下「已复制」
            try:
                old = btn.cget("text")
                btn.configure(text=t("upd_copied"))
                btn.after(1200, lambda: btn.configure(text=old))
            except Exception:
                pass

    def _open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception:
            self.log(url, "info")

    def start_from_ui(self):
        if self.busy:
            return
        if not self._pending_files:
            messagebox.showinfo(t("msg_title"), t("msg_pick"))
            return
        # 期间可能改过「任务」方向，开跑前按当前方向再筛一次
        files = self._filter_task(self._pending_files, notify=True)
        if not files:
            return
        self._pending_files = files
        self._run(files)

    # ------------------------------------------------------------- pipeline --
    def _on_drop_files(self, paths):
        # 由原生窗口回调（ctypes WINFUNCTYPE）调用：此时 GIL 持有但【没有 Tcl 锁】，
        # 因此严禁调用任何 Tk 函数。只把路径暂存到属性并置事件，主线程的轮询循环
        # （_poll_drop，运行在 mainloop 内）会取走并处理。
        self._pending_drop = paths
        self._drop_event.set()

    def _poll_drop(self):
        try:
            if self._drop_event.is_set():
                self._drop_event.clear()
                files = self._pending_drop
                self._pending_drop = None
                if files and not self.busy:
                    self.accept_paths(files)
        except Exception:
            traceback.print_exc()
        finally:
            self.root.after(60, self._poll_drop)

    def accept_paths(self, paths):
        """拖入 / 选择的文件：只载入并预览，**不自动开始转换**。

        真正的转换只发生在用户点击「开始转换」（start_from_ui）时。
        """
        if self.busy:
            return
        files = self._resolve_paths(paths)
        if files:
            self._stage(files)

    def _resolve_paths(self, paths):
        """把拖入的路径（可能含目录）解析成本次任务的待转文件；没有可用文件时返回 []。"""
        task = self._task_code
        files = []
        for p in paths:
            if classify(p) == "dir":
                files.extend(find_models(p) if task == "auto" else find_fbx(p))
            else:
                files.append(p)
        if not files:
            return []
        unknown = [os.path.basename(f) for f in files
                   if classify(f) == "unknown"]
        if unknown:
            messagebox.showwarning(t("msg_unsupported"),
                                   t("msg_unsupported_body",
                                     files="\n".join(unknown[:5])))
            files = [f for f in files if classify(f) != "unknown"]
        if not files:
            return []
        return self._filter_task(files, notify=True)

    def _filter_task(self, files, notify=True):
        """按当前任务方向过滤文件（auto = 不筛）。"""
        task = self._task_code
        if task == "auto":
            return list(files)
        # 指定了方向就只挑对应扩展名的文件
        want = {"fbx2pmx": ("fbx", "unitypackage"),
                "pmx2vrm": ("pmx",),
                "uemodel2pmx": ("uemodel",),
                "psk2pmx": ("psk",),
                "xps2pmx": ("xps",),
                "check": ("pmx",)}[task]
        keep = [f for f in files if classify(f) in want]
        if not keep and notify:
            messagebox.showinfo(t("msg_title"), t("msg_mismatch"))
        return keep

    def _merge_files(self, files):
        """把新文件并进待转列表（按规范化绝对路径去重），返回真正新增的那些。"""
        seen = set()
        for f in self._pending_files:
            seen.add(os.path.normcase(os.path.abspath(f)))
        added = []
        for f in files:
            key = os.path.normcase(os.path.abspath(f))
            if key in seen:
                continue
            seen.add(key)
            added.append(f)
        if added:
            self._pending_files = list(self._pending_files) + added
        return added

    # ------------------------------------------------------- 文件列表 UI --
    def _refresh_files_ui(self):
        """把待转文件同步到列表控件；没有文件时整块隐藏。"""
        files = self._pending_files
        n = len(files)
        total = 0
        for f in files:
            try:
                total += os.path.getsize(f)
            except OSError:
                pass
        self.lbl_files.configure(
            text=(t("files_count", n=n, size=human(total)) if n else ""))
        # 同名文件（来自不同目录）补上上级目录名，避免看不出区别
        names = {}
        for f in files:
            b = os.path.basename(f)
            names[b] = names.get(b, 0) + 1
        kids = self.tv.get_children()
        if kids:
            self.tv.delete(*kids)
        # 行数跟着文件数走（2~5 行），文件少时不留一片空行白占竖向空间
        try:
            self.tv.configure(height=max(2, min(5, n)))
        except Exception:
            pass
        for i, f in enumerate(files):
            b = os.path.basename(f)
            if names.get(b, 0) > 1:
                b = os.path.join(os.path.basename(os.path.dirname(f)), b)
            try:
                size = human(os.path.getsize(f))
            except OSError:
                size = "—"
            kind = classify(f)
            self.tv.insert("", "end", iid=str(i),
                           values=(b, KIND_LABEL.get(kind, kind.upper()),
                                   size))
        if n and not self._files_shown:
            self.filecard.pack(fill="x", pady=(px(8), 0), before=self.nb)
            self._files_shown = True
            # 新出现的控件（文件列表 / 滚动条）也要能被拖入，
            # 等它建好原生窗口后补挂一次（_dnd_topup 只挂没挂过的）
            self.root.after(80, self._dnd_topup)
        elif not n and self._files_shown:
            self.filecard.pack_forget()
            self._files_shown = False
        self._set_dz_compact(bool(n))
        self._draw_dropzone()
        # 文件列表一出一进，上段需要的高度差 200 多像素，自动重排一次
        # （用户自己拖过分割条就不动了，尊重用户的选择）
        if not getattr(self, "_vsplit", None):
            self._place_vsplit()

    def remove_selected_files(self):
        """移除列表里选中的文件（Ctrl / Shift 可多选）。"""
        if self.busy or not self._pending_files:
            return
        try:
            idx = sorted(int(i) for i in self.tv.selection())
        except Exception:
            idx = []
        if not idx:
            self.log(t("files_sel_none"), "info")
            return
        gone = {self._pending_files[i] for i in idx}
        drop = set(idx)
        self._pending_files = [f for i, f in enumerate(self._pending_files)
                               if i not in drop]
        if self._previewed in gone:
            self._previewed = None
        self.log(t("files_removed", n=len(idx), m=len(self._pending_files)),
                 "info")
        self._after_files_changed()

    def clear_files(self):
        """清空待转列表（连预览一起清掉）。"""
        if self.busy or not self._pending_files:
            return
        self._pending_files = []
        self._previewed = None
        self._clear_preview()
        self.log(t("files_cleared"), "info")
        self._after_files_changed()

    def _clear_preview(self):
        try:
            self.pv.set_mesh(None)
        except Exception:
            pass
        try:
            self.lbl_stats.configure(text=t("preview_stats"))
        except Exception:
            pass

    def _after_files_changed(self):
        """列表被手工改过之后：刷新 UI + 状态栏，必要时换预览对象。"""
        self._refresh_files_ui()
        n = len(self._pending_files)
        if not n:
            self.status(t("status_wait"))
            return
        self.status(t("status_staged", n=n))
        # 还留着文件就把预览切到（新的）第一个；_previewed 没被删时这里会自己跳过
        self._kick_preview(self._preview_sources(self._pending_files))

    def _preview_row(self, event=None):
        """双击列表某一行 → 只预览这个文件。"""
        iid = self.tv.identify_row(event.y) if event is not None else ""
        if not iid:
            sel = self.tv.selection()
            iid = sel[0] if sel else ""
        try:
            path = self._pending_files[int(iid)]
        except (ValueError, IndexError, TypeError):
            return
        if not os.path.isfile(path):
            return
        self._previewed = None
        self._kick_preview([path], force=True)

    def _stage(self, files):
        """文件已就绪：入列表 + 出预览 + 提示，等用户点「开始转换」。

        拖入新文件是**追加**（按绝对路径去重，重复的自动跳过），列表里的条目
        可以单独移除或整体清空；真正的转换只发生在点「开始转换」时。
        """
        added = self._merge_files(files)
        if not added:
            self.log(t("log_staged_dup", n=len(files)), "info")
            self.status(t("status_staged", n=len(self._pending_files)))
            return
        n_all = len(self._pending_files)
        had_prior = n_all > len(added)
        self._refresh_files_ui()
        # 预览刚拖进来的第一个文件，用户能立刻确认拖对了没；
        # 新文件都没法预览时退回列表第一个，别让预览空着。
        self._kick_preview(self._preview_sources(added)
                           or self._preview_sources(self._pending_files))
        self.status(t("status_staged", n=n_all))
        self.log("")
        if had_prior:
            self.log(t("log_staged_add", n=len(added), m=n_all), "head")
        else:
            self.log(t("log_staged", n=n_all), "head")
        for p in added:
            self.log("   " + p, "ok")
        if len(added) < len(files):
            self.log(t("log_staged_dup", n=len(files) - len(added)), "info")
        self.log(t("log_staged_tip"), "info")
        if not had_prior:
            self.log(t("files_tip"), "info")

    def _refilter_pending(self):
        """任务方向变了：把已就绪的文件按新方向重筛一遍。"""
        if not self._pending_files:
            return
        keep = self._filter_task(self._pending_files, notify=False)
        if keep == self._pending_files:
            return
        if keep:
            self._pending_files = keep
            self._previewed = None
            self._refresh_files_ui()
            self._kick_preview(keep)
            self.status(t("status_staged", n=len(keep)))
        else:
            self._pending_files = []
            self._previewed = None
            self._refresh_files_ui()
            self._clear_preview()
            self.status(t("status_wait"))
            self.log(t("log_staged_drop"), "warn")

    def _snapshot_cfg(self):
        # 「自动」= 交给各转换器选自己的稳妥默认：fbx / vrm / psk 走 "mmd"
        #（按身高归一到 20 单位），uemodel 走 "fixed"（UE cm→MMD 1/8 固定比例，
        # 部件才能保持相对大小）。这里统一发 "auto"，由各转换器自己解释，
        # 免得 GUI 端硬写死成 mmd 把 uemodel 的默认覆盖掉。
        if self.var_scale_auto.get():
            scale = "auto"
        elif self.var_scale_raw.get():
            scale = 1.0
        else:
            try:
                scale = float(self.var_scale_value.get().strip())
            except ValueError:
                scale = "auto"
        return {
            "scale": scale,
            "flip_z": bool(self.var_flipz.get()),
            "outdir": (None if self.var_same_dir.get()
                       else (self.var_outdir.get().strip() or None)),
            "open_after": bool(self.var_open_after.get()),
            "show_bones": bool(self.var_bones.get()),
            "task": self._task_code,
            "vrm_spec": (self.var_vrm_spec.get() or "1.0"),
            "export_morphs": bool(self.var_morphs.get()),
            "force_two_sided": bool(self.var_two_sided.get()),
            "edge": bool(self.var_edge.get()),
            "center": bool(self.var_center.get()),
            "fbx_morphs": bool(self.var_fbx_morphs.get()),
            "alpha_mode": alpha_code_of(self.var_alpha_mode.get()),
            "auto_facing": bool(self.var_auto_facing.get()),
            "face_180": bool(self.var_face180.get()),
            "ue_fbx": bool(self.var_ue_fbx.get()),
            "ue_alpha": bool(self.var_ue_alpha.get()),
            "ue_align": bool(self.var_ue_align.get()),
            "ue_align_ref": self.var_ue_align_ref.get(),
            "ue_height": self.var_ue_height.get(),
            "subfolder": bool(self.var_subfolder.get()),
            "psk_jp": bool(self.var_psk_jp.get()),
            "psk_ik": bool(self.var_psk_ik.get()),
            "psk_morphs": bool(self.var_psk_morphs.get()),
            "psk_fbx": bool(self.var_psk_fbx.get()),
            "psk_alpha": bool(self.var_psk_alpha.get()),
            "psk_out": psk_out_code_of(self.var_psk_out.get()),
            "psk_height": self.var_psk_height.get(),
            "psk_tex": bool(self.var_psk_tex.get()),
            "xps_jp": bool(self.var_xps_jp.get()),
            "xps_ik": bool(self.var_xps_ik.get()),
            "xps_double": bool(self.var_xps_double.get()),
            "xps_unused": bool(self.var_xps_unused.get()),
            "xps_fbx": bool(self.var_xps_fbx.get()),
        }

    def _run(self, files):
        cfg = self._snapshot_cfg()
        self._save_settings()
        self.busy = True
        self.btn_go.configure(state="disabled", bg="#a9bde8")
        self._progress(True)
        self.status(t("status_process", n=len(files)))
        self.log("")
        self.log(t("log_start", n=len(files)), "head")
        # 预览通常在选择文件时就已经出过了；这里只是兜底（例如首帧被跳过）
        self._kick_preview(files)
        thr = threading.Thread(target=self._worker, args=(files, cfg),
                               daemon=True)
        thr.start()

    # -- worker thread ------------------------------------------------------
    def _worker(self, files, cfg):
        old_out = sys.stdout
        sys.stdout = stdout_redirect(self.q)
        made = []
        try:
            for path in files:
                made.extend(self._convert_one(path, cfg))
        except Exception:
            self.q.put(("log", traceback.format_exc()))
            self.q.put(("err", "转换失败，详见日志"))
        finally:
            try:
                sys.stdout.flush()
            except Exception:
                pass
            sys.stdout = old_out
            self.q.put(("done", made, cfg))

    def _logfn(self):
        """给转换脚本用的日志回调（在 worker 线程里跑，只能往队列里塞）。"""
        q = self.q

        def fn(msg, tag=None):
            q.put(("log", msg, tag or "mono"))
        return fn

    def _convert_one(self, path, cfg):
        q = self.q
        kind = classify(path)
        task = cfg.get("task", "auto")
        if task == "auto":
            task = AUTO_TASK.get(kind, "")
        if not task:
            q.put(("log", "跳过不支持的文件：%s" % os.path.basename(path),
                   "warn"))
            return []
        folder = cfg["outdir"] or os.path.dirname(os.path.abspath(path)) or BASE
        # 输出目录可能还不存在（例如用户指定的 out/ 子目录），先建好再写文件
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            q.put(("log", "无法创建输出目录 %s：%s" % (folder, e), "err"))
            return []

        # 「输出到同名子文件夹」：每次转换单独一个 <文件名>_<日期>_<序号> 目录，
        # 重复转换不会互相覆盖（纯校验任务不建目录）。
        if cfg.get("subfolder", True) and task != "check":
            stem = os.path.splitext(os.path.basename(path))[0]
            folder = self._out_folder(folder, stem)
            q.put(("log", "输出目录：%s" % folder, "info"))

        if task == "check":
            q.put(("log", ""))
            q.put(("log", "→ %s（仅校验）" % os.path.basename(path), "head"))
            self._report(path, os.path.dirname(os.path.abspath(path)), cfg)
            return [path]

        if task == "pmx2vrm":
            return self._do_pmx2vrm(path, folder, cfg)

        if task == "uemodel2pmx":
            return self._do_uemodel2pmx(path, folder, cfg)

        if task == "psk2pmx":
            return self._do_psk2pmx(path, folder, cfg)

        if task == "xps2pmx":
            return self._do_xps2pmx(path, folder, cfg)

        return self._do_fbx2pmx(path, kind, folder, cfg)

    # -- 输出目录：同名子文件夹（文件名_日期_序号）--------------------------
    def _out_folder(self, base, stem):
        """在 base 下建 <stem>_<YYYYMMDD>_<NNN> 并返回（重名就往后 +1）。

        用户要求：重复转换同一文件时不要互相覆盖，每次都落到新编号的目录里。
        例：R2T1AimisiMd10011.pskx → R2T1AimisiMd10011_20260101_001
            再转一次                  → R2T1AimisiMd10011_20260101_002
        """
        day = time.strftime("%Y%m%d")
        n = 1
        while n < 10000:
            d = os.path.join(base, "%s_%s_%03d" % (stem, day, n))
            if not os.path.exists(d):
                try:
                    os.makedirs(d)
                except OSError:
                    pass
                return d
            n += 1
        return base

    # -- PMX → VRM ---------------------------------------------------------
    def _do_pmx2vrm(self, path, folder, cfg):
        q = self.q
        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(folder, stem + ".vrm")
        q.put(("log", ""))
        q.put(("log", "→ %s" % os.path.basename(path), "head"))
        q.put(("log", "读取 PMX…", "info"))
        st = pmx2vrm.convert(
            path, out, spec=cfg["vrm_spec"], scale_mode=cfg["scale"],
            meta={"title": stem}, log=self._logfn(),
            max_morphs=(None if cfg.get("export_morphs", True) else 0),
            force_double_sided=cfg.get("force_two_sided", False))
        q.put(("log", "已写出 %s（%s）"
               % (os.path.basename(out), human(st["bytes"])), "ok"))
        q.put(("log", "VRM 版本 %s · 节点 %d · primitive %d"
               % (cfg["vrm_spec"], st["nodes"], st["primitives"]), "mono"))
        # 预览用源 PMX 渲染（VRM 本身没有渲染器）
        self._report(path, os.path.dirname(os.path.abspath(path)), cfg)
        return [out]

    # -- uemodel (UEFormat) → PMX ------------------------------------------
    def _do_uemodel2pmx(self, path, folder, cfg):
        q = self.q
        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(folder, stem + ".pmx")
        fbx = os.path.join(folder, stem + ".fbx") if cfg.get("ue_fbx") else None
        q.put(("log", ""))
        q.put(("log", "→ %s" % os.path.basename(path), "head"))
        q.put(("log", "读取 UEFormat…", "info"))
        st = uemodel2pmx.convert(path, out, scale_mode=cfg["scale"],
                                 log=self._logfn(), name=stem,
                                 fbx_path=fbx,
                                 remove_alpha=cfg.get("ue_alpha", True),
                                 align_to=(cfg.get("ue_align_ref") or None)
                                 if cfg.get("ue_align") else None,
                                 force_double_sided=cfg.get("force_two_sided",
                                                            False))
        q.put(("log", "已写出 %s（%s）"
               % (os.path.basename(out), human(st["bytes"])), "ok"))
        if fbx:
            if st.get("fbx"):
                q.put(("log", "同时导出 %s（%s）"
                       % (os.path.basename(fbx), human(st["fbx"]["bytes"])),
                       "ok"))
            else:
                q.put(("log", "FBX 导出失败：%s"
                       % (st.get("fbx_error") or "未知原因"), "err"))
        self._report(out, folder, cfg)
        made = [out]
        if fbx and os.path.isfile(fbx):
            made.append(fbx)
        return made

    # -- psk / pskx (Unreal ActorX) → PMX ---------------------------------
    def _do_psk2pmx(self, path, folder, cfg):
        q = self.q
        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(folder, stem + ".pmx")
        fbx = os.path.join(folder, stem + ".fbx") if cfg.get("psk_fbx") else None
        q.put(("log", ""))
        q.put(("log", "→ %s" % os.path.basename(path), "head"))
        q.put(("log", "读取 PSK…", "info"))
        st = psk2pmx.convert(path, out, scale_mode=cfg["scale"],
                             log=self._logfn(), name=stem,
                             fbx_path=fbx,
                             jp_bones=cfg.get("psk_jp", True),
                             make_ik=cfg.get("psk_ik", True),
                             export_morphs=cfg.get("psk_morphs", True),
                             remove_alpha=cfg.get("psk_alpha", True),
                             force_double_sided=cfg.get("force_two_sided",
                                                        False))
        q.put(("log", "已写出 %s（%s）"
               % (os.path.basename(out), human(st["bytes"])), "ok"))
        if fbx:
            if st.get("fbx"):
                q.put(("log", "同时导出 %s（%s）"
                       % (os.path.basename(fbx), human(st["fbx"]["bytes"])),
                       "ok"))
            else:
                q.put(("log", "FBX 导出失败：%s"
                       % (st.get("fbx_error") or "未知原因"), "err"))
        self._report(out, folder, cfg)
        made = [out]
        if fbx and os.path.isfile(fbx):
            made.append(fbx)
        return made

    # -- XPS / XNALara → PMX -----------------------------------------------
    def _do_xps2pmx(self, path, folder, cfg):
        q = self.q
        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(folder, stem + ".pmx")
        fbx = os.path.join(folder, stem + ".fbx") if cfg.get("xps_fbx") else None
        q.put(("log", ""))
        q.put(("log", "→ %s" % os.path.basename(path), "head"))
        q.put(("log", "读取 XPS…", "info"))
        st = xps2pmx.convert(path, out, scale_mode=cfg["scale"],
                             log=self._logfn(), name=stem,
                             fbx_path=fbx,
                             jp_bones=cfg.get("xps_jp", True),
                             make_ik=cfg.get("xps_ik", True),
                             hide_unused=cfg.get("xps_unused", True),
                             all_double_sided=cfg.get("xps_double", True),
                             center=cfg.get("center", True),
                             enable_edge=cfg.get("edge", False),
                             force_double_sided=cfg.get("force_two_sided",
                                                        False))
        q.put(("log", "已写出 %s（%s）"
               % (os.path.basename(out), human(st["bytes"])), "ok"))
        if fbx:
            if st.get("fbx"):
                q.put(("log", "同时导出 %s（%s）"
                       % (os.path.basename(fbx), human(st["fbx"]["bytes"])),
                       "ok"))
            else:
                q.put(("log", "FBX 导出失败：%s"
                       % (st.get("fbx_error") or "未知原因"), "err"))
        self._report(out, folder, cfg)
        made = [out]
        if fbx and os.path.isfile(fbx):
            made.append(fbx)
        return made

    def _report_uemodel(self, path, folder, cfg):
        """回读 .uemodel 做结构校验，并把预览交给模型视图。"""
        q = self.q
        q.put(("log", "校验中…", "info"))
        try:
            m = uemodelio.read_uemodel(path)
        except Exception as e:
            q.put(("log", "uemodel 回读失败：%s" % e, "err"))
            return
        lod = m.lods[0] if m.lods else None
        if lod is None:
            q.put(("log", "   ✗ 文件里没有 LOD 数据", "err"))
            return
        nt = len(lod.indices) // 3
        cov = sum(int(x.num_faces) for x in lod.materials)
        q.put(("log", "   UEFormat v%d · 顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d"
               % (m.version, len(lod.vertices), nt, len(m.skeleton.bones),
                  len(lod.materials)), "mono"))
        q.put(("log", "   权重 %d 条 · 表情 %d 个 · UV %d 套"
               % (len(lod.weights), len(lod.morphs), len(lod.uvs)), "mono"))
        bad = []
        if cov != nt:
            bad.append("材质面区间合计 %d 面 ≠ 索引缓冲 %d 面" % (cov, nt))
        if any(w.bone >= len(m.skeleton.bones) for w in lod.weights):
            bad.append("有权重指向不存在的骨骼")
        nv = len(lod.vertices)
        if any(i >= nv or i < 0 for i in lod.indices):
            bad.append("有越界的顶点索引")
        if bad:
            for b in bad:
                q.put(("log", "   ✗ " + b, "err"))
        else:
            q.put(("log", "   ✓ 结构校验通过（字节数、索引、权重、材质覆盖）",
                   "ok"))

        try:
            q.put(("log", "生成预览…", "info"))
            mesh = model_preview.mesh_from_uemodel(path)
            q.put(("mesh", mesh, path))
        except Exception as e:
            q.put(("log", "预览渲染失败：%s" % e, "warn"))

    # -- FBX / unitypackage → PMX -----------------------------------------
    def _do_fbx2pmx(self, path, kind, folder, cfg):
        q = self.q
        made = []
        if kind == "unitypackage":
            q.put(("log", "解包 %s" % os.path.basename(path)))
            q.put(("log", "", "mono"))
            dest = os.path.join(folder, os.path.splitext(
                os.path.basename(path))[0] + "_extracted")
            unpack_unitypackage(path, dest)
            fbxs = find_fbx(dest)
            if not fbxs:
                q.put(("log", "包里没有找到 FBX。", "err"))
                return made
            fbxs.sort(key=lambda p: -os.path.getsize(p))
            q.put(("log", "包内 FBX（按体积排序）：", "info"))
            for p in fbxs:
                q.put(("log", "    %-46s %s"
                       % (os.path.relpath(p, dest)[:46], human(os.path.getsize(p))),
                       "info"))
            folder = dest
        elif kind == "fbx":
            fbxs = [path]
        else:
            return made

        for fbx in fbxs:
            stem = os.path.splitext(os.path.basename(fbx))[0]
            out = os.path.join(folder, stem + ".pmx")
            q.put(("log", ""))
            q.put(("log", "→ %s" % os.path.basename(fbx), "head"))
            q.put(("log", "读取 FBX…", "info"))
            scene = fbx2pmx.Scene(fbx)
            nb = sum(1 for m in scene.models.values() if m.cls == "LimbNode")
            ngeo = sum(1 for n in scene.objects if n.name == "Geometry")
            q.put(("log", "   FBX 7.%d：%d 个网格，%d 根骨骼"
                   % (scene.ver, ngeo, nb), "info"))
            q.put(("log", "转换中…", "info"))
            st = fbx2pmx.convert(scene, out, scale_mode=cfg["scale"],
                                 flip_z=cfg["flip_z"], name=stem,
                                 center=cfg.get("center", True),
                                 morphs=cfg.get("fbx_morphs", True),
                                 alpha_mode=cfg.get("alpha_mode", "auto"),
                                 auto_facing=cfg.get("auto_facing", True),
                                 face_180=cfg.get("face_180", False))
            # 朝向依据写进日志：判错了能看懂是凭什么判的、也能手动覆盖
            _turn = t("log_turn_on") if st.get("face_turn") else t("log_turn_off")
            if cfg.get("auto_facing", True):
                q.put(("log", t("log_facing_auto",
                                reason=st.get("facing_reason", ""),
                                turn=_turn), "mono"))
            else:
                q.put(("log", t("log_facing_off", turn=_turn), "mono"))
            # 贴图 alpha 逐张的判定明细
            al = st.get("alpha") or {}
            if al and al.get("mode") != "keep":
                q.put(("log", t("log_alpha_head",
                                mode=alpha_label(al.get("mode", "auto")),
                                stripped=al.get("stripped", 0),
                                kept=al.get("kept", 0),
                                opaque=al.get("opaque", 0)), "mono"))
                for bn, verdict, detail in al.get("decisions", [])[:24]:
                    if verdict == "strip":
                        q.put(("log", t("log_alpha_strip_one", name=bn,
                                        detail=detail), "mono"))
                    else:
                        q.put(("log", t("log_alpha_keep_one", name=bn,
                                        detail=detail), "mono"))
                if al.get("failed"):
                    q.put(("log", t("log_alpha_fail", n=al["failed"]), "warn"))
            _extra = ""
            if st.get("morphs"):
                _extra = "，%d 个表情" % st["morphs"]
            q.put(("log", "已写出 %s（%s%s）"
                   % (os.path.basename(out), human(st["bytes"]), _extra), "ok"))
            self._report(out, folder, cfg)
            made.append(out)

        return made

    def _report(self, pmx, folder, cfg):
        q = self.q
        q.put(("log", "校验中…", "info"))
        try:
            m, problems = validate_pmx(pmx)
        except Exception as e:
            q.put(("log", "校验读取失败：%s" % e, "err"))
            return
        q.put(("log", "   顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d"
               % (len(m["vertices"]), len(m["faces"]) // 3,
                  len(m["bones"]), len(m["materials"])), "mono"))
        if m["morphs"]:
            q.put(("log", "   表情 %d 个" % len(m["morphs"]), "mono"))
        if problems:
            for p in problems:
                q.put(("log", "   ✗ " + p, "err"))
        else:
            q.put(("log", "   ✓ 结构校验全部通过（索引、权重、字节数、材质覆盖）",
                   "ok"))
        if len(m["textures"]) == 0:
            q.put(("log", "   提示：无贴图引用，进 MMD 是纯色模型", "warn"))
        if len(m["morphs"]) == 0:
            q.put(("log", "   提示：无表情数据，需在 PMXEditor 里手动添加", "warn"))

        # preview：读取 PMX 网格 → 实时正面预览（只显示，不落盘保存图片）
        try:
            q.put(("log", "生成预览…", "info"))
            mesh = model_preview.mesh_from_pmx(pmx)
            q.put(("mesh", mesh, pmx))
        except Exception as e:
            q.put(("log", "预览渲染失败：%s" % e, "warn"))

    # -- UI pump ------------------------------------------------------------
    def _pump(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.log(msg[1], msg[2] if len(msg) > 2 else "mono")
                elif kind == "mesh":
                    self._show_mesh(msg[1], msg[2], reflow=True)
                elif kind == "mesh_tex":
                    self._show_mesh(msg[1], msg[2], reflow=False)
                elif kind == "drop":
                    self.accept_paths(msg[1])
                elif kind == "err":
                    self.log(msg[1], "err")
                elif kind == "done":
                    self._finish(msg[1], msg[2])
                elif kind == "update":
                    self._on_update_result(msg[1])
        except queue.Empty:
            pass
        self.root.after(60, self._pump)

    def _show_mesh(self, mesh, pmx=None, reflow=True):
        """把加载好的预览网格交给实时预览控件。

        reflow=True：首次装载（重置视角、计算 LOD）。
        reflow=False：仅贴图到位后重绘，不打断用户当前视角。
        """
        if mesh is None:
            return
        if pmx:
            self.last_pmx = pmx
        self.last_mesh = mesh
        try:
            if reflow:
                self.pv.set_mesh(mesh)
                self.pv.set_bones(bool(self.var_bones.get()))
            else:
                self.pv.redraw()
        except Exception as e:
            self.log("预览显示失败：%s" % e, "warn")
            return
        if not reflow:
            return
        st = mesh.stats()
        name = os.path.basename(pmx) if pmx else mesh.name
        extra = ""
        if pmx and os.path.exists(pmx):
            extra = t("pv_stats_file", size=human(os.path.getsize(pmx)))
        self.lbl_stats.configure(
            text=t("pv_stats", name=name, v=st["verts"], t=st["tris"],
                   b=st["bones"], m=st["mats"], size=extra))

    def _preview_sources(self, files):
        """挑出能直接预览的文件（排除目录）。"""
        out = []
        for f in files:
            if os.path.isfile(f) and model_preview.classify(f) != "unknown":
                out.append(f)
        return out

    def _kick_preview(self, files, force=False):
        """把第一个文件的正面显示出来（实时反馈，不做任何转换）。

        对 VRM / PMX 这类可能内嵌大量贴图的格式，先以“基色”立刻出正面，
        再在后台线程解码贴图、到位后只重绘（不打断用户视角）——保证拖入即见。

        force=False 时，若这个文件已经预览过就不再重载：点「开始转换」不该把
        用户刚转好的视角复位。
        """
        srcs = self._preview_sources(files)
        if not srcs:
            return
        first = srcs[0]
        if not force and first == self._previewed:
            return
        self._previewed = first
        kind = model_preview.classify(first)
        _flipz = bool(self.var_flipz.get())
        _autoface = bool(self.var_auto_facing.get())
        _face180 = bool(self.var_face180.get())

        def job():
            try:
                if kind in ("vrm", "pmx", "xps"):
                    mesh = model_preview.load_preview(first, with_textures=False)
                    self.q.put(("mesh", mesh, first))     # 立刻出正面（基色）
                    if mesh.load_textures():
                        self.q.put(("mesh_tex", mesh, first))  # 贴图到位后重绘
                else:
                    mesh = model_preview.load_preview(
                        first, flip_z=_flipz, auto_facing=_autoface,
                        face_180=_face180)
                    self.q.put(("mesh", mesh, first))
            except Exception as e:
                # 失败就把“已预览”标记撤回，下次（例如点开始转换时）还能再试
                if self._previewed == first:
                    self._previewed = None
                self.q.put(("log", "预览失败：%s: %s"
                            % (type(e).__name__, e), "warn"))
        threading.Thread(target=job, daemon=True).start()

    def _rerender(self):
        # “预览叠加骨骼点”勾选后即时重绘
        if getattr(self, "pv", None) is not None:
            self.pv.set_bones(bool(self.var_bones.get()))

    def _finish(self, made, cfg):
        self.busy = False
        self.btn_go.configure(state="normal", bg=ACCENT)
        self._progress(False)
        self.last_out_dir = os.path.dirname(made[0]) if made else None
        if made:
            self.log("")
            self.log(t("log_done", n=len(made)), "head")
            for p in made:
                self.log("   " + p, "ok")
            self.status(t("status_done", n=len(made)))
            if cfg.get("open_after") and self.last_out_dir:
                try:
                    os.startfile(self.last_out_dir)
                except Exception:
                    pass
        else:
            self.log(t("log_none"), "err")
            self.status(t("status_none"))
        self._save_settings()

    def _on_close(self):
        self._save_settings()
        self.root.destroy()


def main():
    declare_dpi_aware()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    root = tk.Tk()
    # 必须在建立任何控件之前把字体缩放定好。
    # 自动 = 跟随系统 DPI：200% 缩放的屏幕就是 2.0，字号与其它程序一致。
    apply_scaling(root, load_zoom_pref())
    app = ConverterApp(root)
    app.log(t("ready"), "info")
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror("启动失败", err)
        except Exception:
            sys.stderr.write(err)
        raise
