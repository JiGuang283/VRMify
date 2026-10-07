# -*- mode: python ; coding: utf-8 -*-
# 新界面（ui/app.py）的 PyInstaller 打包配置：
#
#     pyinstaller Model-To-VRM.spec        → dist/Model-To-VRM/Model-To-VRM.exe
#
# 打包环境里要装 pywebview（原生窗口 + 拖放）和 Pillow（贴图瘦身），见 requirements.txt；
# 没装的话 exe 会退回系统浏览器 / 不能瘦身贴图。settings.json、edits.json 写在 exe 旁边。
# 旧的 tkinter 界面（main.py）仍用 main.spec 打包。
import sys

is_win = sys.platform.startswith('win')
is_mac = sys.platform == 'darwin'
app_name = 'Model-To-VRM'
icon_file = 'MC_2.ico' if is_win else ('MC_2.icns' if is_mac else None)

a = Analysis(
    ['ui/app.py'],
    # 模块之间是用裸名 import 的（import pipeline / pmxio …），运行期才把子目录加进 sys.path，
    # PyInstaller 的静态分析看不到，所以子目录要写进 pathex
    pathex=['.', 'ui', 'convert', 'formats', 'gfx'],
    binaries=[],
    # 界面页面和预览组件（three.js / three-vrm 离线副本）；server.py 在 _MEIPASS 下按同样的相对路径找
    datas=[('ui/web', 'ui/web'), ('gfx/web', 'gfx/web')],
    hiddenimports=[
        'server', 'pipeline', 'pmx2vrm', 'vrm_edit', 'pmx_check',
        'vrmconv', 'vrmconv.humanoid', 'vrmconv.skeleton', 'vrmconv.mesh', 'vrmconv.materials',
        'vrmconv.expressions', 'vrmconv.springbone', 'vrmconv.vrm_ext', 'vrmconv.mathutil',
        'vrmconv.textures',
        # 其它输入格式（pipeline 里按需 import）
        'fbx2pmx', 'fbx_reader', 'fbx_probe', 'unitypackage_unpack', 'uemodel2pmx', 'uemodelio',
        'psk2pmx', 'pskio', 'xps2pmx', 'xpsio', 'fbxout', 'pmxio', 'vrmio', 'longpath',
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
