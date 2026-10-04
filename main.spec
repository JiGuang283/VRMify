# -*- mode: python ; coding: utf-8 -*-
import os
import re
import sys

app_name = 'ModelConvert'

is_win = sys.platform.startswith('win')
is_mac = sys.platform == 'darwin'
is_linux = sys.platform.startswith('linux')

if is_win:
    icon_file = 'MC_2.ico'
elif is_mac:
    icon_file = 'MC_2.icns'
else:
    icon_file = None


# ---- 版本号 --------------------------------------------------------------
# 从 main.py 顶部读 __version__（唯一来源），生成 PyInstaller 的版本资源文件。
# 这样 exe 的「属性 → 详细信息」里就有版本号，且和界面显示 / 检查更新同源。
def _read_version():
    try:
        with open('main.py', 'r', encoding='utf-8') as f:
            m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']',
                          f.read(), re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    return '0.0.0'


app_version = _read_version()
_v = [int(x) for x in (re.findall(r'\d+', app_version) + ['0', '0', '0'])[:3]]

version_file = None
if is_win:
    version_file = os.path.join('build', 'version_info.txt')
    os.makedirs('build', exist_ok=True)
    with open(version_file, 'w', encoding='utf-8') as f:
        f.write("""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%d, %d, %d, 0),
    prodvers=(%d, %d, %d, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'SaraKale'),
         StringStruct('FileDescription', 'Model Converter'),
         StringStruct('FileVersion', '%s'),
         StringStruct('InternalName', 'ModelConvert'),
         StringStruct('OriginalFilename', 'ModelConvert.exe'),
         StringStruct('ProductName', 'ModelConvert'),
         StringStruct('ProductVersion', '%s')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""" % (_v[0], _v[1], _v[2], _v[0], _v[1], _v[2], app_version, app_version))

a = Analysis(
    ['main.py'],
    # 引擎模块已归类到 formats / convert / gfx 子目录，且 main.py 里是用裸名
    # import（import fbx2pmx 等）。PyInstaller 只做静态分析、看不到 main.py
    # 运行期才加的 sys.path，所以必须在这里把子目录加进 pathex，否则打包后
    # 运行到 import 处会报 ModuleNotFoundError。
    pathex=['.', 'formats', 'convert', 'gfx'],
    binaries=[],
    datas=[],
    hiddenimports=['fbx2pmx', 'pmx_check', 'pmx2vrm', 'vrm2pmx', 'preview','pmx2psk','psk2pmx','unitypackage_unpack', 'fbx_reader', 'pmxio', 'vrmio','pskio','xps2pmx',
                   # Windows 超长路径（MAX_PATH 260）兼容层
                   'longpath',
                   # UEFormat（.uemodel）双向转换 + ASCII FBX 写出器
                   'uemodel2pmx', 'pmx2uemodel', 'uemodelio', 'fbxout',
                   # 解压 ZSTD 压缩体（.uemodel 运行期懒加载）
                   'zstandard'],
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
    upx=True if is_win else False,
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
    app = BUNDLE(
        coll,
        name=f'{app_name}.app',
        icon=icon_file,
        bundle_identifier='com.modelconvert.app',
        version=app_version,
    )