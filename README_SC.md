# Model-To-VRM（纯 Python）

把 MMD 的 **PMX 模型转成 VRM 1.0 / 0.x**，可以直接导入 Unity（UniVRM）、VSeeFace、three-vrm 等支持 VRM 的软件和游戏。
FBX / unitypackage、uemodel（UEFormat）、psk / pskx（Unreal ActorX）、xps（XNALara）这些格式可以先转成 PMX，再转 VRM。

**全部用 Python 标准库实现**：不需要 Blender、Unity、mmd_tools、UniVRM，直接读写二进制格式。

[English](README.md) | [简体中文](README_SC.md) | [繁體中文](README_TC.md) | [日本語](README_JP.md)

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
| **表情** | 顶点表情 → morph target（稀疏存储，文件小）；常用表情自动对应 VRM 预设（眨眼、あいうえお、喜怒哀乐）；材质表情 → 材质颜色绑定（显示 / 隐藏部件） |
| **物理** | PMX 刚体 / 关节 → VRM 1.0 SpringBone：头发、裙摆、飘带、袖子按原生 VRM 的手感换算，碰撞体来自 PMX 的跟随骨骼刚体 |
| **兼容性** | 输出通过 VRM 官方 JSON Schema 校验，避开 UniVRM 导入时会中断的数据（BlendShape 重名、颜色分量缺失等） |

物理和材质的换算细节见 [Document/PMX转VRM_使用说明.md](Document/PMX转VRM_使用说明.md)。

### 其他格式 → PMX

| 输入 | 说明 |
|---|---|
| FBX / unitypackage | 二进制和 ASCII FBX 7.x；自动判定朝向、处理贴图 alpha 遮罩、跳过游戏里的效果片网格 |
| uemodel（UEFormat v1–v10） | FortnitePorting / FModel 导出的中间格式；可按整身模型对齐拆开的部件 |
| psk / pskx（Unreal ActorX） | 带权重、MRPH 顶点表情、附加 UV；Bip001 骨名转 MMD 日文标准名 |
| xps（XNALara 二进制） | 自动换轴、骨名转日文标准名、补足 IK、保留贴图 alpha |

这些格式转出的 PMX 可以再转 VRM。说明见 [FBX转PMX](Document/FBX转PMX_使用说明.md)、[PSK转PMX](Document/PSK转PMX_使用说明.md)。

### 三维预览

用 three-vrm（和 UniVRM 同一套 MToon / SpringBone 规则）查看转换结果：待机 / 走路 / 跑步 / 抬腿动作、
可调的灯光和投影、表情滑块、截图。离线可用。

---

## 二、环境要求

- **Python 3.10+**（需自带 tkinter；Windows 官方安装包默认包含）
- **可选**：`Pillow`（加速旧界面里的贴图解码）、`pywebview`（三维预览用独立窗口，不装就用系统浏览器）
- 拖放文件进窗口依赖 Windows 原生接口；其他系统用「浏览」按钮选择文件

---

## 三、使用方法

### 图形界面

```bash
python main.py
```

1. 把文件拖进窗口（或点拖放区选择）。拖入只会载入并预览，**点「开始转换」才会转换**。
2. 「任务」默认按扩展名自动判断：`.pmx` → VRM，其他格式 → PMX。
3. 「VRM 选项」页可选 VRM 版本（1.0 / 0.x）和是否导出表情。
4. 转换完点「三维预览（three-vrm）」查看结果。

默认输出到源文件旁的 `<文件名>_<日期>_<序号>` 子文件夹，重复转换不会覆盖。

### 命令行

路径含空格或中文时加双引号。

```bash
# PMX → VRM 1.0（默认带 SpringBone）
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"

# VRM 0.x（老软件用；0.x 不输出 SpringBone）
python convert/pmx2vrm.py "model.pmx" --spec 0x --title "名字" --author "作者"

# 三维预览
python gfx/web_preview.py "model.vrm"
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
| `--no-spring-center` | 不以模型根节点为 center（默认设置，人物整体移动时头发不被甩） |
| `--outline` | 按 PMX 轮廓线设置写 MToon 描边（默认不描边） |
| `--force-double-sided` | 所有材质双面 |
| `--flip-winding` | 强制反转三角绕序（默认自动判定） |
| `--dense-morphs` / `--max-morphs N` | 表情用密集存储 / 只导出前 N 个 |
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

### 批量转换和检查

`tools/` 下有批量转换、VRM 结构 / 官方 Schema 检查、SpringBone 动态测试，用法见 [tools/README.md](tools/README.md)：

```bash
python tools/batch_convert.py model -o vrm           # 每个角色输出带 / 不带 SpringBone 两份
python tools/qa/check_vrm.py vrm                     # 需要 pip install jsonschema
python tools/qa/dynamics.py vrm                      # 需要 Node.js
```

---

## 四、目录结构

```
Model-To-VRM/
├── main.py                 图形界面入口
├── main.spec               PyInstaller 打包配置
├── convert/
│   ├── pmx2vrm.py          PMX → VRM 入口（convert() 与命令行）
│   ├── vrmconv/            PMX → VRM 的各个阶段
│   │   ├── humanoid.py     骨骼名映射、付与骨合并、层级修正、T-pose
│   │   ├── skeleton.py     骨骼预处理、glTF 节点树、占位骨
│   │   ├── mesh.py         顶点权重、按材质切网格
│   │   ├── materials.py    MToon 材质（toon 换算、透明判定）
│   │   ├── expressions.py  表情
│   │   ├── springbone.py   物理 → SpringBone
│   │   └── vrm_ext.py      VRM 扩展本体
│   ├── fbx2pmx.py  uemodel2pmx.py  psk2pmx.py  xps2pmx.py   其他格式 → PMX
│   └── pmx_check.py        PMX 校验
├── formats/                格式读写（pmxio、vrmio(GLB)、fbx_reader、uemodelio、pskio、xpsio …）
├── gfx/
│   ├── preview.py          界面内的实时预览（纯 Python 软光栅）
│   ├── web_preview.py      三维预览（本地服务器 + 窗口）
│   └── web/                三维预览页面，vendor/ 里是 three.js、three-vrm 离线副本
└── tools/                  批量转换和质量检查（不随程序打包）
```

模块之间用裸名导入（`import pmxio`），`main.py` 运行时会把 `formats/`、`convert/`、`gfx/` 放进 `sys.path`。

---

## 五、打包

```bash
pip install pyinstaller
pyinstaller main.spec
```

**务必用 `main.spec`**：子目录模块是运行时才加进 `sys.path` 的，PyInstaller 的静态分析看不到，
spec 里配好了 `pathex`、`hiddenimports` 和三维预览的页面资源（`gfx/web`）。改了源码要重新打包。

推送形如 `v1.2.3` 的 tag 会触发 [GitHub Actions](.github/workflows/build-app.yml)，在 Windows / macOS / Linux
（x64 与 ARM）上自动打包并上传到草稿 Release。

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
- **物理**：PMX 里链与链之间的横向关节（裙摆常见）在 SpringBone 里无法表达，会舍弃。
- **材质**：乘算球面贴图（.sph）、共享 toon07～10 没有对应项；描边默认不输出。
- **表情预设**：按 MMD 标准表情名对应，`にこり`（笑眉）这类只动眉毛的表情映射到 happy 时变化不明显。
- **其他格式**：FBX 保留英文骨骼名（套 MMD 动作需改名）；XPS 没有表情；PMX 每顶点最多 4 根骨骼权重。
- **贴图**：KTX2 / WebP 不支持，材质会退化为纯色。

---

## 八、许可与版权

- 本项目以 **GPL-3.0** 发布（见 [LICENSE](LICENSE)），原作者 SaraKale 等。
- 三维预览附带的 three.js、three-vrm 为 MIT 许可（见 `gfx/web/vendor/`）。
- VRM meta 默认写成最保守的授权：需要署名、禁止再分发、禁止修改。
- **转换前请自行确认你有权使用该模型**。很多 MMD 模型禁止二次配布，转出的 VRM 同样不能公开分发。
