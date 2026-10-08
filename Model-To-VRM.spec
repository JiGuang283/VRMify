# -*- mode: python ; coding: utf-8 -*-
# 图形界面（ui/app.py）的 PyInstaller 打包配置：
#
#     pyinstaller Model-To-VRM.spec        → dist/Model-To-VRM/Model-To-VRM.exe
#
# 打包环境里要装 pywebview（原生窗口 + 拖放）和 Pillow（贴图瘦身），见 requirements.txt；
# 没装的话 exe 会退回系统浏览器 / 不能瘦身贴图。settings.json、edits.json 写在 exe 旁边
# （那里写不了时改到 %APPDATA%\Model-To-VRM）。发布用的 zip 由 tools/build_release.py 生成。
import os
import re
import sys

is_win = sys.platform.startswith('win')
is_mac = sys.platform == 'darwin'
app_name = 'Model-To-VRM'
icon_file = 'ui/icon.ico' if is_win else None      # 由 tools/make_icon.py 生成

# ---- 版本号：唯一来源是 ui/version.py，写进 exe 的「属性 → 详细信息」
with open(os.path.join('ui', 'version.py'), encoding='utf-8') as _f:
    app_version = re.search(r'__version__\s*=\s*["\']([^"\']+)', _f.read()).group(1)
_v = tuple((int(x) for x in (re.findall(r'\d+', app_version) + ['0'] * 4)[:4]))
version_file = None
if is_win:
    version_file = os.path.join(workpath, 'version_info.txt')
    os.makedirs(workpath, exist_ok=True)
    with open(version_file, 'w', encoding='utf-8') as _f:
        _f.write("""VSVersionInfo(
  ffi=FixedFileInfo(filevers=%(v)r, prodvers=%(v)r, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'Model-To-VRM'),
      StringStruct('FileVersion', '%(s)s'),
      StringStruct('InternalName', 'Model-To-VRM'),
      StringStruct('OriginalFilename', 'Model-To-VRM.exe'),
      StringStruct('ProductName', 'Model-To-VRM'),
      StringStruct('ProductVersion', '%(s)s'),
      StringStruct('LegalCopyright', 'GPL-3.0')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""" % {'v': _v, 's': app_version})

a = Analysis(
    ['ui/app.py'],
    # 模块之间是用裸名 import 的（import pipeline / pmxio …），运行期才把子目录加进 sys.path，
    # PyInstaller 的静态分析看不到，所以子目录要写进 pathex
    pathex=['.', 'ui', 'convert', 'formats'],
    binaries=[],
    # 界面页面和预览组件（three.js / three-vrm 离线副本）；server.py 在 _MEIPASS 下按同样的相对路径找
    datas=[('ui/web', 'ui/web'), ('ui/viewer', 'ui/viewer')],
    hiddenimports=[
        'server', 'version', 'pipeline', 'pmx2vrm', 'vrm_edit',
        'vrmconv', 'vrmconv.humanoid', 'vrmconv.skeleton', 'vrmconv.mesh', 'vrmconv.materials',
        'vrmconv.expressions', 'vrmconv.springbone', 'vrmconv.vrm_ext', 'vrmconv.mathutil',
        'vrmconv.textures', 'vrmconv.autophys', 'vrmconv.morphbake',
        # 其它输入格式（pipeline 里按需 import）
        'fbx2pmx', 'fbx_reader', 'unitypackage_unpack', 'archive', 'uemodel2pmx', 'uemodelio',
        'psk2pmx', 'pskio', 'xps2pmx', 'xpsio', 'fbxout', 'pmxio', 'vrmio', 'longpath', 'unityasset',
        'zstandard',
        # 浏览器模式下的文件对话框（子进程 --dialog）
        'tkinter', 'tkinter.filedialog',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=app_name,
    icon=icon_file,
    version=version_file,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=app_name,
)
if is_mac:
    app = BUNDLE(coll, name=f'{app_name}.app', icon=icon_file, bundle_identifier='com.model2vrm.app')
