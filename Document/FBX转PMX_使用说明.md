# FBX → PMX 转换说明

这套脚本不需要 Blender、不需要 Autodesk FBX SDK、不需要 mmd_tools，纯 Python 标准库
（3.10+）即可运行。

## 工具清单

| 文件 | 作用 |
|---|---|
| `main.py` | **图形界面入口**（根目录），FBX / unitypackage / VRM / PMX / uemodel / PSK / PSKX 都能拖进去转；右上角可切换语言 |
| `formats/unitypackage_unpack.py` | 解包 `.unitypackage`（本质是 gzip tar） |
| `formats/fbx_reader.py` | 二进制 FBX（7.x）解析库，被下面两个脚本调用 |
| `convert/fbx2pmx.py` | **FBX → PMX 2.0 转换器** |
| `formats/fbx_probe.py` | 探查 FBX 结构（网格 / 骨骼 / 蒙皮清单） |
| `convert/pmx_check.py` | 校验 PMX 结构 + 软件渲染预览图（正面视图） |
| `formats/pmxio.py` | 完整 PMX 读写库（VRM 互转用） |
| `formats/vrmio.py` | GLB/glTF 容器读写 + 图片编解码 |
| `formats/uemodelio.py` | UEFormat `.uemodel` 读写（v1–v10） |
| `formats/pskio.py` | Unreal ActorX `.psk` / `.pskx` 读取 |
| `convert/pmx2vrm.py` | **PMX → VRM 转换器**，见 `PMX转VRM_使用说明.md` |
| `convert/uemodel2pmx.py` | **uemodel（UEFormat）→ PMX 转换器** |
| `convert/psk2pmx.py` | **PSK / PSKX（Unreal ActorX）→ PMX 转换器**，见 `PSK转PMX_使用说明.md` |
| `gfx/preview.py` | 实时 3D 正面预览控件（GUI 调用） |

> 早期的 `PMX转换器.py` 已改名为 `main.py`，命令行入口统一为 `python main.py`。

## 常用命令

路径带空格或中文，一定要加双引号。

```powershell
# 0. 图形界面（拖进去就能转）
python main.py

# 1. 解包 unitypackage（--list 只列内容不解包）
python formats/unitypackage_unpack.py "Sexy Sailor_Sapphy_v1.00.unitypackage" --list
python formats/unitypackage_unpack.py "Sexy Sailor_Sapphy_v1.00.unitypackage"

# 2. 先看看 FBX 里有什么
python formats/fbx_probe.py "model.fbx"

# 3. 转换成 PMX
python convert/fbx2pmx.py "model.fbx" -o "model.pmx"

# 4. 校验并生成预览图
python convert/pmx_check.py "model.pmx"
python convert/pmx_check.py "model.pmx" --bones    # 叠加骨骼位置，检查是否与网格对齐
```

`fbx2pmx.py` 的可选参数：

| 参数 | 说明 |
|---|---|
| `--scale mmd` | 默认。自动缩放到 MMD 标准身高（约 20 单位） |
| `--scale raw` | 保持 FBX 原始尺寸（米） |
| `--scale 12.5` | 手动指定缩放倍数 |
| `--no-flip-z` | 不做右手系→左手系转换（默认会转） |
| `--alpha keep\|auto\|strip` | 贴图透明通道处理，默认 `auto` |
| `--no-auto-facing` | 关闭朝向自动判定，用默认单轴反射（Z） |
| `--face-180` | 在判定结果之上再强制多转 180° |
| `--keep-untextured-meshes` | 保留无材质的网格（默认跳过） |
| `--info` | 只打印结构信息，不转换 |

## 转换映射关系

| FBX | PMX |
|---|---|
| `Geometry` 顶点 / `PolygonVertexIndex` | 顶点表 + 三角形（四边形自动扇形三角化） |
| `LayerElementUV`（ByPolygonVertex / IndexToDirect） | UV0（V 轴已翻转 1-v） |
| `LayerElementNormal` | 顶点法线 |
| `Model(type=Mesh)` ← `Material` 连接 | 每个网格一个材质绘制段 |
| `Deformer/Skin` + `Cluster` 的 Indexes/Weights | 骨骼权重（按影响数选 BDEF1 / BDEF2 / BDEF4） |
| `Model(type=LimbNode)` 层级 | 骨骼（父子关系、位置） |
| 1 个显示枠 | 列出全部骨骼 |

三角形绕序会自动判定：用几何面法线与导入的顶点法线做一致性投票，方向相反则整体反绕，
保证法线朝外。

## 本次转换结果

源文件 `Sexy Sailor_Sapphy.fbx`（FBX 7400，二进制，32 位偏移）：

| 项目 | 数值 |
|---|---|
| 顶点 | 47640 |
| 三角形 | 74926 |
| 骨骼 | 196 |
| 材质 | 18 |
| 表情 morph | 0 |
| 刚体 / 关节 | 0 / 0 |
| 文件大小 | 2.58 MB |
| 缩放 | 14.4674（原高 1.382 m → 20 单位） |

校验结果：字节数精确匹配、无越界索引、权重和全部为 1、材质面覆盖完整。

## 必须知道的三个限制

**1. 源 FBX 里没有头部网格。** 这不是转换丢失。`UV SKMZ`（皮肤）的 Y 范围是
0.670~1.087，只到肩膀；最高的部件是帽子 `UVACC hat`（到 1.369）。而骨架里却有
`Head`、`Eye_L`、`Eye_R` 骨骼。也就是说这个包是「身体 + 服装」，头部/脸需要另一个
资源包。在 MMD 里加载会看到一个无头的身体。

**2. 没有表情。** 源 FBX 的 `BlendShape` 数量为 0，所以 PMX 的 morph 也是 0。
要眨眼、做口型，只能在 PMXEditor 里手工建。

**3. 没有贴图和物理。** FBX 里没有 `Texture`/`Video` 节点，unitypackage 也没打包图片，
所以材质是纯色白模。另外裙子有 68 根骨骼（`skirt_00_00` ~ `skirt_16_03`），
在 MMD 里不会自己飘，需要在 PMXEditor 里配刚体 + 关节。

## 关于骨骼命名

MMD 的动作数据（.vmd）按骨骼名匹配，日文标准名（`センター`、`上半身`、`左腕` 等）
才能套用现成动作。这份 PMX 保留了 FBX 的英文名（`Hips`、`Spine`、`Left arm` …），
模型本身能正常显示和手动摆姿势，但直接套 MMD 动作是匹配不上的。
需要的话可以在 PMXEditor 里批量改名。

> 注：PSK / PSKX 走的是另一条路 —— Bip001（3ds Max Biped）那套命名默认就转成
> MMD 标准日文名，英文原名写进骨骼的英文名备注字段。详见 `PSK转PMX_使用说明.md`。

## 关于 Toon

本工具写出的 PMX **一律不使用 Toon**：材质是 `toon_flag=0` + `toon=-1`，
在 PMXEditor / MMD 里显示为「なし」。

⚠️ 别把 toon 字段写反 —— PMX 材质的 toon 字段宽度由「共有Toonフラグ」决定：

| flag | 含义 | 字段 |
|---|---|---|
| `1` | 共享 / 内建 toon | **1 字节编号**，引用 MMD 安装目录 `Data/` 下的 `toon01.bmp` … `toon10.bmp`，**编号 0 就是 `toon01.bmp`** |
| `0` | 本模型纹理表里的贴图 | 纹理索引宽度，**`-1` = なし（不使用）** |

「编号 0 = `toon00.bmp` = 不使用 toon」是流传很广的误解：**MMD 的 `Data/` 目录里根本没有
`toon00.bmp`**（只有 `toon01` … `toon10`），mmd_tools 源码里也写死了
`toon%02d.bmp % (shared + 1)`。所以写 `flag=1, toon=0` 等于给每个材质硬套了一层
`toon01.bmp`。本工具 2026-09-24 之前的版本就是这个写法，已修正。

## PmxEditor 报「アンカーデータの読み込みに失敗しました。」

点确定后模型照常显示，**不影响模型**。成因是 PmxEditor 的「锚点数据」也用 `.psk` 扩展名：

- PmxEditor 打开 `xxx.pmx` 时，会去找**同名**的 `xxx.psk` 当它的「アンカーデータ（锚点/权重数据）」自动加载
  （菜单 `[ファイル] → [アンカーデータの自動読み込み／保存]`，见其 readme）；
- 而 `.psk` 又是 Unreal ActorX 网格的扩展名。

所以只要你把 PSK 转出的 PMX 和源 `.psk` 放在同一目录且同名，PmxEditor 必然弹这一句。
规避方法：把 PMX 挪到别的文件夹 / 改个名 / 在 PmxEditor 里关掉该菜单项。
详见 `PSK转PMX_使用说明.md`。
