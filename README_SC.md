# Model-To-VRM（纯 Python）

把 MMD 的 **PMX 模型转成 VRM 1.0 / 0.x**，可以直接导入 Unity（UniVRM）、VSeeFace、three-vrm 等支持 VRM 的软件和游戏。
FBX / unitypackage、uemodel（UEFormat）、psk / pskx（Unreal ActorX）、xps（XNALara）这些格式可以先转成 PMX，再转 VRM。

**全部用 Python 标准库实现**：不需要 Blender、Unity、mmd_tools、UniVRM，直接读写二进制格式。

[English](README.md) | [简体中文](README_SC.md)

> 本项目派生自 [SaraKale/Model-To-PMX](https://github.com/SaraKale/Model-To-PMX)（GPL-3.0）。
> 2026-10 起修改：重做了 PMX → VRM（骨骼、表情、MToon 材质、SpringBone 物理），
> 删除了 VRM → PMX、PMX → uemodel / psk / FBX 等输出方向。

---

## 一、功能

### PMX → VRM

| 方面 | 做法 |
|---|---|
| **骨骼** | 自动把 MMD 标准骨骼名（`下半身`、`左腕`、`左人指２` …）对应到 VRM humanoid；付与 100% 的 D 骨（`足D` 等）并入付与親；修正 humanoid 层级；缺的必需骨位补占位骨 |
| **姿势** | A-pose 手臂自动抬成 VRM 要求的 T-pose（骨骼和顶点一起转） |
| **坐标** | PMX（左手系）→ glTF（右手系）做一次镜面反射，绕序自动判定，模型不会左右翻转或镂空 |
| **材质** | MToon。阴影色、明暗交界按 PMX 的 toon 贴图换算（皮肤是粉色阴影，和原生 VRoid 一致）；颜色写线性值；按贴图 alpha 分布自动选不透明 / 裁剪 / 半透明；加算球面贴图 → matcap |
| **表情** | 顶点表情 → morph target（稀疏存储，文件小）；常用表情自动对应 VRM 预设（眨眼、あいうえお）；喜怒哀乐预设像 VRoid 一样组合眉、眼、嘴（比如 angry = 怒り + ｷﾘｯ + 口角下げ），整张脸一起变，只有 ARKit 表情（VRChat 模型常见）的也能组合；材质表情 → 材质颜色绑定（显示 / 隐藏部件）；骨骼表情（寄り目 等）烘焙成顶点表情；UV 表情（漫符切换）→ 贴图偏移；有眼骨时补视线表情 lookUp / lookDown / lookLeft / lookRight |
| **物理** | PMX 刚体 / 关节 → VRM 1.0 SpringBone：头发、裙摆、飘带、袖子按原生 VRM 的手感换算，碰撞体来自 PMX 的跟随骨骼刚体。模型完全没有物理时（FBX 等常见）按骨骼名和骨架结构自动补：头发、裙摆、袖子、飘带、尾巴会摆动，身体自动加碰撞体 |
| **贴图** | 可选限制最大边长（4096 / 2048 / 1024）、不透明贴图存成 JPEG，VRM 文件通常能小一半以上（需要 Pillow）；带透明的贴图保持 PNG |
| **VRM 信息** | 作者、版本、联系方式、使用许可（谁可以使用、商用、修改、再分发、署名）在界面里填；默认取保守值 |
| **兼容性** | 输出通过 VRM 官方 JSON Schema 校验，避开 UniVRM 导入时会中断的数据（BlendShape 重名、颜色分量缺失等） |

物理和材质的换算细节见 [Document/PMX转VRM_使用说明.md](Document/PMX转VRM_使用说明.md)。

### 其他格式 → PMX

| 输入 | 说明 |
|---|---|
| FBX / unitypackage | 二进制和 ASCII FBX 7.x；自动判定朝向、处理贴图 alpha 遮罩、跳过游戏里的效果片网格。FBX 里没写贴图文件名时（Unity 资源常见），按 Unity 材质（.mat）找回贴图；英文骨名（HumanIK / Mixamo / VRoid / Blender / 3ds Max）和英文表情名（VRChat、VRoid、`MTH_A` 这类）自动对应 VRM；同一位置的几套替换网格（手势、张嘴的头）只显示一套，其余做成切换表情。转 VRM 时解包的资源和中间的 PMX 放临时目录，输出目录只有 VRM；包里有好几个模型时，只有衣服 / 配件的那个（头部或手上没有网格）跳过 |
| uemodel（UEFormat v1–v10） | FortnitePorting / FModel 导出的中间格式；可按整身模型对齐拆开的部件 |
| psk / pskx（Unreal ActorX） | 带权重、MRPH 顶点表情、附加 UV；Bip001 骨名转 MMD 日文标准名 |
| xps（XNALara 二进制） | 自动换轴、骨名转日文标准名、补足 IK、保留贴图 alpha |

这些格式转出的 PMX 可以再转 VRM。说明见 [FBX转PMX](Document/FBX转PMX_使用说明.md)、[PSK转PMX](Document/PSK转PMX_使用说明.md)。

### 三维预览

用 three-vrm（和 UniVRM 同一套 MToon / SpringBone 规则）查看转换结果：待机 / 走路 / 跑步 / 抬腿动作、
可调的灯光和投影、表情滑块、截图。PMX 用 three.js 的 MMD 组件原生显示（MMD 着色 + MMD 物理），不经过 VRM。离线可用。

### 转换后调整

预览右侧的「调整」面板（预览的模型能调整时自动打开；预览还没转换的 PMX 时要先转换），对单个模型做转换后的微调，**不用重新转换**，保存后直接写进输出的 VRM：

- **名称**、**缩略图**（用当前预览画面，正方形，导入游戏 / VRM 软件时列表里显示的头像）
- **预设表情**：每个 VRM 预设（开心、眨眼、口型あいうえお …）用哪个 PMX 表情。自动对应只认标准日文表情名，
  命名不规范的模型可以在这里补齐；「▶」可以试看。组合出来的情绪预设显示成「怒り+ｷﾘｯ+口角下げ」，
  觉得不合适可以改回单个表情（组合的那个留在自定义表情里）。每个预设右边的滑块是**强度**（0～150%，
  拖动时试看）：形态键权重按比例缩放，单个权重最多到 100%，所以超过 100% 只对组合表情有效
- **视线范围**：眼睛看到最边上时左右 / 向下 / 向上各转多少度（默认按眼球结构自动算），箭头可以试看；
  VRM 视线（看向镜头等）和转换时补的 lookLeft 等视线表情一起改（表情最多到左右 40°、上下 30°）
- **物理**：整体的硬度 / 阻尼 / 重力，以及每条链的硬度、重力和开关，拖动滑块时预览实时生效

调整按源文件记在 `edits.json`（缩略图在 `edits/`），以后再转这个文件会自动套上；「全部还原」回到转换时的样子。
同一个 VRM 反复调整不会越调越歪：第一次调整时原始值存进了文件里，每次都从原始值重新套用。

---

## 二、环境要求

- **Python 3.10+**（转换本身只用标准库，装了 Pillow 会快不少（贴图检查用它）；没装 pywebview 时文件对话框用 tkinter，Windows 官方安装包自带）
- **可选**（`pip install -r requirements.txt`）：`pywebview`（独立窗口、可以把文件拖进窗口，不装就用系统浏览器）、
  `Pillow`（贴图瘦身）

---

## 三、使用方法

### 图形界面

**双击 `启动.bat`**（也可以把文件拖到它上面）。它优先用项目里的 `.venv`，没有就用系统的 Python。
第一次使用建议建好环境：

```bash
uv venv .venv && uv pip install -r requirements.txt     # 或 python -m venv .venv 再 .venv\Scripts\pip install -r requirements.txt
```

命令行启动：`python ui/app.py [文件或文件夹 ...]`（`--browser` 强制用系统浏览器）。

- 左侧：添加文件 / 文件夹（PMX、VRM、压缩包、FBX、unitypackage、uemodel、psk、xps），点底部的「开始转换」
  （都转过了就是「重新转换」）。设置分四页：
  - **VRM**：版本、物理、表情、描边、贴图最大边长和 JPEG
  - **输入格式**：FBX、uemodel、psk、xps 各自的选项
  - **VRM 信息**：作者和使用许可（名称默认用模型文件名）
  - **输出**：非 PMX 的格式转成 VRM（默认，先转 PMX 再转 VRM）还是只转 PMX；保存位置：程序目录（默认，程序旁边的
    `vrm` 文件夹）/ 源文件旁 / 指定文件夹，是否每次建子文件夹；同时转换几个（自动 / 1 / 2 / 4：几个文件、或一个
    包 / 压缩包里有几个模型时同时转，快一倍以上，每个大约要 1.5 GB 内存）
- 右侧：three-vrm 预览。还没转换的文件点「预览」会先在后台快速转一份临时 VRM；转换完成后显示实际输出。
  PMX（加进来的 PMX 文件、输出里的 PMX、「只转 PMX」的结果）直接原生预览：MMD 的卡通着色、球面贴图、表情，
  MMD 物理（ammo.js）实时模拟，标准骨名（日文 MMD 名或常见英文名）的模型也能做待机 / 走路等动作。
  灯光、表情、调整、信息几个面板在预览右侧一栏打开（一次一个），预览区跟着变窄，不会挡住模型。
  左栏和右侧面板栏的宽度可以拖动两边的分隔线调整（记在设置里，双击分隔线恢复默认）。
  「调整」面板见上面的[转换后调整](#转换后调整)。
- **模型网站下载的压缩包可以直接加**（zip / 7z / rar / tar.gz 等，分卷包加第一卷）：在临时目录解压，转换里面的 PMX
  （没有 PMX 时转其它格式；包里套包时再解一层），输出放在压缩包旁边。zip 用内置的解压，文件名是 GBK / Shift-JIS
  （甚至混用）的老压缩包也能正确还原；7z / rar 借用电脑上的 7-Zip / WinRAR / Bandizip，都没有时用 Windows 自带的
  tar（Windows 11 可以解 7z 和 rar；Windows 10 的版本解不了 LZMA 压缩的 7z，文件名有系统编码以外的字也会失败——这时装 7-Zip）。
  有密码的压缩包不支持。
- 界面支持中文 / English，设置保存在 `settings.json`。
- 用系统浏览器时拿不到拖入文件的路径，请用「添加文件」按钮；页面关掉一分钟后程序自动退出。

### 命令行

路径含空格或中文时加双引号。

```bash
# PMX → VRM 1.0（默认带 SpringBone）
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"

# VRM 0.x（老软件用；0.x 不输出 SpringBone）
python convert/pmx2vrm.py "model.pmx" --spec 0x --title "名字" --author "作者"

# 预览 VRM：用图形界面打开（也能直接加 .vrm 文件）
python ui/app.py "model.vrm"
```

`pmx2vrm.py` 的常用参数：

| 参数 | 说明 |
|---|---|
| `--spec 1.0\|0x` | VRM 版本，默认 1.0 |
| `--scale auto\|倍率` | 默认把身高归一到 1.6 m |
| `--keep-pose` | 保留原姿势（默认 A-pose → T-pose） |
| `--no-spring` / `--no-spring-skirt` | 不转物理 / 不转裙摆类物理 |
| `--spring-stiffness k` | SpringBone 硬度整体倍率，默认 1.0 |
| `--spring-gravity g` / `--spring-gravity-skirt g` | 头发等 / 裙摆的重力，默认 0 / 0.1 |
| `--no-auto-spring` | 模型没有物理刚体时不自动补 |
| `--no-spring-center` | 不以模型根节点为 center（默认设置，人物整体移动时头发不被甩） |
| `--outline` | 按 PMX 轮廓线设置写 MToon 描边（默认不描边） |
| `--force-double-sided` | 所有材质双面 |
| `--flip-winding` | 强制反转三角绕序（默认自动判定） |
| `--dense-morphs` / `--max-morphs N` | 表情用密集存储 / 只导出前 N 个 |
| `--no-combo-expr` | 情绪预设不组合眉 / 眼 / 嘴，每个预设只对应一个 PMX 表情 |
| `--no-gaze` | 不补视线表情（默认有左右眼骨时转眼骨生成 lookUp / lookDown / lookLeft / lookRight） |
| `--tex-max N` / `--tex-jpeg` | 贴图最大边长 / 不透明贴图存 JPEG（需要 Pillow） |
| `--title` / `--author` | 写进 VRM meta |

其他格式 → PMX：

```bash
python convert/fbx2pmx.py "model.fbx" -o "model.pmx"            # --alpha keep|auto|strip、--face-180
python convert/uemodel2pmx.py "model.uemodel" -o "model.pmx"    # --fbx 同时写一份 FBX
python convert/psk2pmx.py "model.pskx" -o "model.pmx"           # --raw-bone-names、--no-ik
python convert/xps2pmx.py "model.xps" -o "model.pmx"            # --show-unused、--one-sided
python convert/pmx_check.py "model.pmx"                         # PMX 校验 + 预览图
python formats/unitypackage_unpack.py "pack.unitypackage"       # 解包 unitypackage
```

### 批量转换、检查和测试

`tools/` 下有批量转换、VRM 结构 / 官方 Schema 检查、SpringBone 动态测试、真实模型回归对比，
用法见 [tools/README.md](tools/README.md)：

```bash
python tools/batch_convert.py model -o vrm           # 每个角色输出带 / 不带 SpringBone 两份
python tools/qa/check_vrm.py vrm                     # 需要 pip install jsonschema
python tools/qa/dynamics.py vrm                      # 需要 Node.js
python -m unittest discover -s tests                 # 单元测试（用合成的小模型，不需要真实模型）
python tools/regress.py record model/某角色           # 记下真实模型的转换结果作为基准
python tools/regress.py check                        # 改代码后重转、对比基准
```

---

## 四、目录结构

```
Model-To-VRM/
├── 启动.bat                双击启动图形界面
├── requirements.txt        可选依赖（pywebview、Pillow）
├── Model-To-VRM.spec       PyInstaller 打包配置
├── convert/
│   ├── pmx2vrm.py          PMX → VRM 入口（convert() 与命令行）
│   ├── vrmconv/            PMX → VRM 的各个阶段
│   │   ├── humanoid.py     骨骼名映射、付与骨合并、层级修正、T-pose
│   │   ├── skeleton.py     骨骼预处理、glTF 节点树、占位骨
│   │   ├── mesh.py         顶点权重、按材质切网格
│   │   ├── materials.py    MToon 材质（toon 换算、透明判定）
│   │   ├── expressions.py  表情
│   │   ├── morphbake.py    骨骼表情 / 视线表情烘焙成顶点表情，UV 表情 → 贴图偏移
│   │   ├── springbone.py   物理 → SpringBone
│   │   ├── textures.py     贴图瘦身（缩小、JPEG）
│   │   └── vrm_ext.py      VRM 扩展本体
│   ├── fbx2pmx.py  uemodel2pmx.py  psk2pmx.py  xps2pmx.py   其他格式 → PMX
│   ├── pmx_check.py        PMX 校验
│   ├── pipeline.py         转换流程：识别格式 → 其他格式先转 PMX → 再转 VRM（界面用）
│   └── vrm_edit.py         转换后调整：表情指定、物理、名称、缩略图
├── formats/                格式读写（pmxio、vrmio(GLB)、fbx_reader、uemodelio、pskio、xpsio …）、archive（7z / rar / tar 解压）、longpath（Windows 长路径）
├── ui/                     图形界面
│   ├── app.py              入口（pywebview 窗口 / 浏览器）
│   ├── server.py           后端：文件列表、选项、转换任务、本地 HTTP 接口
│   ├── web/                页面（HTML / CSS / JS，中英双语）
│   └── viewer/             three-vrm 预览组件 viewer-core.js，vendor/ 里是 three.js、three-vrm 离线副本
├── tests/                  单元测试（synth.py 合成测试用的小模型）
├── .github/workflows/      GitHub Actions：推送时测试、打 tag 时打包发布
└── tools/                  批量转换、质量检查、回归对比（不随程序打包）
```

模块之间用裸名导入（`import pmxio`），`ui/server.py` 和各个命令行脚本运行时会把 `formats/`、`convert/` 放进 `sys.path`。

---

## 五、打包和发布

发布形式是**绿色版 zip**：解压后双击 `Model-To-VRM.exe`，不用安装；删掉文件夹就是卸载。

```bash
pip install -r requirements-dev.txt     # pyinstaller + pywebview + Pillow
python tools/build_release.py           # → dist/Model-To-VRM-v<版本>-win64.zip（只要文件夹：pyinstaller Model-To-VRM.spec）
```

- **版本号**只在 `ui/version.py` 里改；界面顶栏、exe 的「属性 → 详细信息」、zip 文件名都从这里取。
- **务必用 spec 文件**：子目录模块是运行时才加进 `sys.path` 的，PyInstaller 的静态分析看不到，spec 里配好了
  `pathex`、`hiddenimports` 和页面资源（`ui/web`、`ui/viewer`）。打包环境里装了 pywebview 和 Pillow，exe 才有独立窗口和贴图瘦身。
- **设置文件**（`settings.json`、`edits.json`）写在 exe 旁边；那里写不了（比如放进了 `C:\Program Files`）时改放
  `%APPDATA%\Model-To-VRM`。
- **独立窗口**用系统的 WebView2（Win11 自带，Win10 基本都有）；没有 WebView2 或窗口打不开时自动改用浏览器。
- exe 没有代码签名，第一次运行时 Windows 会提示「已保护你的电脑」：点「更多信息 → 仍要运行」。
- zip 里附带 `LICENSE`（GPL-3.0）和 README。

**GitHub Actions**（`.github/workflows/`）：

| 工作流 | 什么时候跑 | 做什么 |
|---|---|---|
| `test.yml` | 推送到 main 且改了代码；也可手动运行 | Linux 上跑单元测试（含官方 VRM Schema 校验），约 1 分钟 |
| `release.yml` | 推送 `v*` tag；也可手动运行 | Windows 上测试 → `build_release.py` 打包 → `smoke_exe.py` 启动 exe 实际转换几个合成模型 → tag 触发时建**草稿** Release 并附上 zip；手动运行只把 zip 放进 Artifacts |

发版：改 `ui/version.py` → 提交 → `git tag v0.1.0` → `git push origin main v0.1.0` → 到 GitHub 的 Releases 里检查草稿、点发布。
tag 和 `ui/version.py` 不一致时打包会直接失败。

---

## 六、常见问题

### 导入 Unity / 游戏时卡在加载中

多半是 UniVRM 读到不合规的数据抛了异常。先用 `tools/qa/check_vrm.py` 检查文件；Unity 程序的日志在
`%USERPROFILE%\AppData\LocalLow\<公司名>\<产品名>\Player.log`，里面会有具体异常。

### 脸偏白 / 没有阴影

VRoid 等原生模型的脸本来就基本是平涂的，阴影主要来自头发等的**投影**（粉色）。这些由 PMX 的 toon 贴图决定；
如果模型没有 toon 贴图，眼睛、眉毛之类的材质不画阴影。打开三维预览，用「灯光」里的角度和投影开关检查。

### 头发 / 裙摆抖动、穿模

SpringBone 是近似换算，不是复刻 MMD 的物理。可以先用 `--spring-stiffness` 整体调硬 / 调软，
导入 Unity 后也可以在 UniVRM 里逐条调。大幅抬腿时裙摆和大腿根部有少量重叠，原生模型同样如此。

### 模型镂空 / 黑边

看日志里「绕序自动判定」那一行。判定错了加 `--flip-winding`；薄片头发、裙摆依赖双面的加 `--force-double-sided`。

### 报错 `FileNotFoundError`（路径太长）

Windows 默认限制路径 260 字符，unitypackage 解出来的目录很深时容易超限。本工具会自动改用 `\\?\` 长路径形式；
旧版本可以把文件挪到靠近盘符根目录的地方再转。

### FBX 转出来背对镜头 / 透明不对

FBX 没有「正面朝哪」的字段，默认按脚尖方向、脸部网格重心判定，不对时加 `--face-180`。贴图 alpha 默认
`--alpha auto` 逐张判定是透明还是遮罩，整块半透用 `strip`，蕾丝、发梢变硬边用 `keep`。详见
[FBX转PMX_使用说明](Document/FBX转PMX_使用说明.md)。

### MMD 提示无法载入 PMX（编码）

MMD 只读 UTF-16LE 编码的 PMX（中文汉化版的提示把意思译反了）。本工具输出的 PMX 都是 UTF-16LE；
旧文件可以用 `python formats/pmxio.py "旧文件.pmx"` 转换。

### PmxEditor 提示「アンカーデータの読み込みに失敗しました」

PmxEditor 会自动加载和模型同名的 `.psk` 当锚点数据，而 `.psk` 也是 Unreal 网格的扩展名。点确定即可，模型正常；
把 PMX 改名或关掉 `[ファイル] → [アンカーデータの自動読み込み／保存]` 就不会再弹。

---

## 七、已知限制

- **VRM 0.x 不输出 SpringBone**（物理只写 VRM 1.0）。
- **物理**：PMX 里链与链之间的横向关节（裙摆常见）在 SpringBone 里无法表达，会舍弃；转换时按裙片朝向修正腿推裙摆的方向，
  裙子不容易被腿从中间劈开，但整片裙子的联动仍不如原 PMX。
- **材质**：乘算球面贴图（.sph）、共享 toon07～10 没有对应项；描边默认不输出。
- **表情预设**：按表情名对应和组合（MMD 日文名、VRoid / VRChat 英文名、ARKit、常见中文名）；名字都认不出的模型
  组合不出来（可以在「调整」里指定，转换日志的「表情预设」一行列出了缺哪些），组合用固定的权重，个别模型可能偏强或偏弱。
- **其他格式**：FBX 保留英文骨骼名（套 MMD 动作需改名）；XPS 没有表情；PMX 每顶点最多 4 根骨骼权重。
- **贴图**：KTX2 / WebP 不支持，材质会退化为纯色。

---

## 八、许可与版权

- 本项目以 **GPL-3.0** 发布（见 [LICENSE](LICENSE)），原作者 SaraKale 等。
- 三维预览附带的 three.js、three-vrm 为 MIT 许可（见 `ui/viewer/vendor/`）。
- VRM meta 默认写成最保守的授权：需要署名、禁止再分发、禁止修改。
- **转换前请自行确认你有权使用该模型**。很多 MMD 模型禁止二次配布，转出的 VRM 同样不能公开分发。
