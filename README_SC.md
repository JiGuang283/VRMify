# Model-To-VRM

把 MMD 的 PMX 模型转成 **VRM 1.0 / 0.x**，可以直接导入 Unity（UniVRM）、VSeeFace、three-vrm 等支持 VRM 的软件和游戏。
FBX、unitypackage、uemodel、psk、xps 和模型网站下载的压缩包也能直接转。

带图形界面：批量转换、三维预览、转换后微调。转换器只用 Python 标准库，不需要 Blender、Unity 或任何插件。

[English](README.md) | 简体中文

## 下载

到 [Releases](https://github.com/JiGuang283/Model-To-VRM/releases/latest) 下载 `Model-To-VRM-v<版本>-win64.zip`，
解压后双击 `Model-To-VRM.exe`。不用安装，删掉文件夹就是卸载。

- 程序没有代码签名，第一次运行时 Windows 可能提示「已保护你的电脑」：点「更多信息 → 仍要运行」。
- 窗口用系统的 WebView2（Windows 11 自带，Windows 10 基本都有）；没有时自动改用浏览器打开界面。

## 功能

**转换**

- **骨骼**：MMD 标准骨名（以及常见的英文骨名）自动对应 VRM humanoid；修正 MMD 特有的骨骼结构（付与骨、层级），
  A-pose 自动抬成 VRM 要求的 T-pose。
- **材质**：MToon 卡通着色。阴影色和明暗交界从 PMX 的 toon 贴图换算，透明 / 裁剪 / 不透明按贴图自动判定。
- **表情**：常用表情自动对应 VRM 预设（眨眼、口型あいうえお）；喜怒哀乐像 VRoid 一样组合眉、眼、嘴，整张脸一起变。
  除了 MMD 日文名，也认 VRoid、VRChat、ARKit 和常见中文的表情名。材质表情、骨骼表情、UV 表情都会转换；
  有眼骨时补视线表情。
- **物理**：PMX 的刚体和关节换算成 VRM 1.0 SpringBone，头发、裙摆、飘带的手感接近原生 VRM 模型；
  模型没有物理时按骨骼名自动补。
- **其它格式**：FBX / unitypackage、uemodel（UEFormat）、psk / pskx（Unreal ActorX）、xps（XNALara）先转成 PMX
  再转 VRM。zip / 7z / rar 压缩包直接加，里面的模型会被找出来转换，道具和只有衣服的模型自动跳过。
- **兼容性**：输出通过 VRM 官方 JSON Schema 校验，避开会让 UniVRM 导入中断的数据。

**界面**

- **批量**：一次加多个文件或文件夹，几个模型同时转。
- **预览**：用 three-vrm 查看结果（和 UniVRM 同一套 MToon / SpringBone 规则），可以播放待机、走路、跑步动作，
  调灯光、试表情、截图。PMX 用 MMD 的着色和物理原样预览。
- **转换后调整**：不用重新转换就能改名称、缩略图、每个预设对应哪个表情和强度、视线范围、物理的硬度 / 重力 /
  开关，保存后直接写进 VRM。调整按源文件记住，下次再转同一个文件会自动套用。
- 中文 / English 界面。

## 使用

1. 把模型文件、文件夹或压缩包拖进窗口（或点「添加文件」）。
2. 按需改设置，点「开始转换」。输出默认在程序旁边的 `vrm` 文件夹，也可以选「源文件旁」或指定文件夹。
3. 点文件旁的预览按钮查看结果；需要的话在「调整」面板里微调。

设置分四页：

| 页 | 内容 |
|---|---|
| VRM | 版本（1.0 / 0.x）；只转换人物；物理、表情、描边；贴图最大边长、不透明贴图存 JPEG（文件能小一半以上） |
| 输入格式 | FBX、uemodel、psk、xps 各自的选项（朝向、贴图透明、骨骼命名等） |
| VRM 信息 | 作者、版本和使用许可；默认是最保守的授权（需要署名、禁止再分发、禁止修改） |
| 输出 | 转成 VRM 还是只转 PMX；保存位置；同时转换几个 |

**压缩包**：zip 用内置解压，文件名是 GBK / Shift-JIS 的老压缩包也能正确还原；7z / rar 借用电脑上的 7-Zip、WinRAR
或 Bandizip，都没有时用 Windows 自带的 tar（Windows 10 的 tar 解不了部分 7z，这时请装 7-Zip）。不支持加密的压缩包。

## 从源码运行

需要 Python 3.10 以上。

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt    # 可选：pywebview（独立窗口、拖放）、Pillow（贴图处理更快、贴图瘦身）
```

双击 `启动.bat`（也可以把文件拖到它上面），或者 `python ui/app.py [文件 ...]`（`--browser` 用系统浏览器打开界面）。
不装可选依赖也能转换，只是界面在浏览器里打开、贴图处理慢一些、不能瘦身贴图。

### 命令行

```bash
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"            # VRM 1.0
python convert/pmx2vrm.py "model.pmx" --spec 0x                 # VRM 0.x
python convert/fbx2pmx.py "model.fbx" -o "model.pmx"            # 其它格式 → PMX，再转 VRM
```

参数和转换规则见 [PMX → VRM](docs/pmx-to-vrm.md) 和 [其它格式 → PMX](docs/other-formats.md)。

## 常见问题

**导入 Unity / 游戏时卡在加载中**：多半是 UniVRM 读到了不合规的数据。先用 `python tools/qa/check_vrm.py 文件.vrm`
检查；Unity 程序的日志 `%USERPROFILE%\AppData\LocalLow\<公司名>\<产品名>\Player.log` 里有具体异常。

**脸偏白、没有阴影**：VRoid 等原生 VRM 模型的脸本来基本是平涂的，阴影主要来自头发等的投影。阴影色由 PMX 的 toon
贴图决定；在预览的「灯光」里调角度、开关投影检查。

**头发 / 裙摆抖动、穿模**：SpringBone 是近似换算，不是复刻 MMD 的物理。在「调整」面板里整体或逐条调硬度和重力。

**模型镂空 / 黑边**：看转换日志里「绕序自动判定」那一行；薄片头发、裙摆依赖双面显示的，在「VRM」页打开「所有材质双面」。

**FBX 转出来背对镜头、透明不对**：在「输入格式」页打开「再转 180°」，或换一种「贴图透明通道」的处理方式，
详见 [其它格式 → PMX](docs/other-formats.md)。

## 已知限制

- VRM 0.x 不输出 SpringBone（物理只写 VRM 1.0）。
- 物理是近似换算：PMX 裙摆里连接相邻链的横向关节无法表达，整片裙子的联动不如原模型。
- 乘算球面贴图、共享 toon07～10 没有对应项；描边默认不输出。
- 表情预设按名字对应和组合，名字都认不出的模型需要在「调整」里手动指定。
- FBX 转出的 PMX 保留英文骨名；XPS 没有表情。

## 开发

```
convert/            转换器：pmx2vrm.py（PMX → VRM）、vrmconv/（各个阶段）、fbx2pmx / uemodel2pmx / psk2pmx / xps2pmx、
                    pipeline.py（界面用的转换流程）、parallel.py（多进程）、vrm_edit.py（转换后调整）
formats/            格式读写：pmxio、vrmio（GLB）、fbx_reader、uemodelio、pskio、xpsio、archive（解压）…
ui/                 图形界面：app.py（入口）、server.py（后端）、web/（页面）、viewer/（three-vrm 预览）
tests/              单元测试（用 synth.py 合成的小模型）
tools/              批量转换、VRM 检查、物理动态测试、真实模型回归对比、打包发布（见 tools/README.md）
docs/               转换说明
```

模块之间用裸名导入（`import pmxio`），入口脚本运行时会把 `formats/`、`convert/` 加进 `sys.path`。

```bash
pip install -r requirements-dev.txt          # jsonschema、pyinstaller 等
python -m unittest discover -s tests         # 单元测试
python tools/regress.py record <模型目录>     # 记下真实模型的转换结果（不进仓库）
python tools/regress.py check                # 改代码后重转、对比
python tools/build_release.py                # 打包 → dist/Model-To-VRM-v<版本>-win64.zip
```

发版：改 `ui/version.py` → 提交 → 打 `v<版本>` tag 并推送。GitHub Actions 会测试、打包、实际运行 exe 做冒烟测试，
然后建一个草稿 Release。

## 许可

- 以 [GPL-3.0](LICENSE) 发布。本项目基于 [SaraKale/Model-To-PMX](https://github.com/SaraKale/Model-To-PMX)。
- 预览用的 three.js、three-vrm 是 MIT 许可（见 `ui/viewer/vendor/`）。
- **转换前请确认你有权使用该模型。** 很多 MMD 模型禁止二次配布，转出的 VRM 同样不能公开分发。
