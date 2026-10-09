# PSK / PSKX → PMX 转换说明

Unreal（ActorX）导出的 `.psk` / `.pskx` 网格 → MMD 用的 PMX 2.0，纯 Python 标准库实现。
不需要 Blender、不需要 Unreal Editor、不需要 mmd_tools，直接读二进制。

参考实现：<https://github.com/DarklightGames/io_scene_psk_psa>

## 工具清单

| 文件 | 作用 |
|---|---|
| `formats/pskio.py` | `.psk` / `.pskx` 读取（chunk 解析、骨骼世界矩阵、权重、表情、附加 UV） |
| `convert/psk2pmx.py` | **PSK / PSKX → PMX 2.0 转换器** |
| `formats/pmxio.py` | 完整 PMX 2.0 读写 |
| `formats/fbxout.py` | ASCII FBX 7.4 写出器（`--fbx` 用） |
| `convert/pmx_check.py` | 校验 PMX 结构 + 软件渲染预览图（正面视图） |
| `ui/app.py` | 图形界面（根目录的 `启动.bat`），把 `.psk` / `.pskx` 加进去即可 |

## 常用命令

路径带空格或中文，一定要加双引号。

```powershell
# 图形界面：把 .psk / .pskx 加进去；只要 PMX 时在「输出」页选「只转 PMX」
python ui/app.py

# 命令行
python convert/psk2pmx.py "model.psk"  -o "model.pmx"
python convert/psk2pmx.py "model.pskx" -o "model.pmx"

# 保留原始英文骨名、不补足 IK 骨
python convert/psk2pmx.py "model.psk" -o "model.pmx" --raw-bone-names --no-ik

# 顺手导出一份 ASCII FBX（方便在 Blender / Maya 里对照）
python convert/psk2pmx.py "model.psk" -o "model.pmx" --fbx

# 校验 + 出正面预览图
python convert/pmx_check.py "model.pmx"
python convert/pmx_check.py "model.pmx" --bones
```

### 参数

| 参数 | 说明 |
|---|---|
| `--scale mmd` | 默认。按包围盒高度归一到 MMD 标准身高（20 单位） |
| `--scale raw` / `--scale 0.14` | 保持原始尺寸 / 手动指定倍数 |
| `--edge` | 给材质开启 MMD 轮廓线（默认关闭） |
| `--force-double-sided` | 材质强制双面描绘（薄片头发 / 裙摆常用） |
| `--no-textures` | 不找贴图，导出白模 |
| `--keep-alpha` | 保留贴图 alpha（默认按材质去 alpha） |
| `--no-add-uv` | 丢弃 `EXTRAUVS*` 附加 UV |
| `--raw-bone-names` | 骨骼保留原始英文名（默认转 MMD 日文标准名，原名写进英文名备注） |
| `--no-ik` | 不补 MMD 足 IK 骨（默认按腿部骨链补 `左足ＩＫ` / `左つま先ＩＫ`） |
| `--no-morphs` | 不导出表情（默认把 MRPH 顶点位移转成 PMX 顶点表情） |
| `--fbx` | 在 PMX 旁边同时写一份 ASCII FBX |
| `--name` | 指定模型名 |

## 转换映射关系

| PSK / PSKX | PMX |
|---|---|
| `PNTS0000` | 顶点位置 |
| `VTXW0000` | 楔（点索引 + UV + 材质号）→ PMX 顶点 + UV |
| `FACE0000` / `FACE3200` | 三角形（`FACE3200` 是紧凑的 uint32 三连） |
| `MATT0000` | 材质（按材质名在源目录找同名贴图；找不到就是白模） |
| `REFSKELT` | 骨骼（位置 + 父子关系） |
| `RAWWEIGHTS` | 顶点权重（按影响数选 BDEF1 / BDEF2 / BDEF4） |
| `VTXNORMS` | 顶点法线（没有时按换轴反射一律反转绕序） |
| `VERTEXCOLOR` | 顶点色（PMX 无对应字段，仅用于预览着色） |
| `EXTRAUVS*` | PMX 附加 UV（`--no-add-uv` 可关） |
| `MRPHINFO` / `MRPHDATA` | PMX 顶点表情（位移增量摊到该点对应的所有楔上） |
| 骨架腿链 | 自动补 MMD 的 `左足ＩＫ` / `左つま先ＩＫ`（`--no-ik` 可关） |

## 坐标系与朝向（重要）

PSK 和 PMX 的轴约定**手性相反**，所以换轴必须做**一次反射**：

| 格式 | 左手 / 上 / 正面 |
|---|---|
| PSK / PSKX（Unreal，右手系） | `+X` 左手 · `+Z` 上 · **`-Y` 正面** |
| PMX（MMD，左手系） | `+X` 左手 · `+Y` 上 · **`-Z` 正面** |

本工具用的换轴是：

```
(x, y, z)_psk  →  (x, z, y)_pmx        # 行列式 = -1，正好把右手系翻成左手系
```

这样三条约定同时满足：左手 `+X → +X`、上 `+Z → +Y`、正面 `-Y → -Z`。

> ⚠️ 别写成 `(x, z, -y)`：那是纯旋转（行列式 +1），改不了手性，会把正面 `-Y` 送到 `+Z`
> —— 模型在 MMD 里就是**背对镜头**。这个 bug 在 2026-09-24 修掉过，别再改回去。

因为绕序会跟着反射一起翻，所以「没有 `VTXNORMS` 法线数据」时的兜底也是**反转**，
而不是保持原样（不然没带法线的 psk 会整片背面朝外）。

验证方法（不需要打开 MMD）：在 PMX 里找脸 / 眼睛类材质，其顶点 **Z 重心应该为负**。
实测对照：`Anastasya.pmx` 的 `Eye` 材质 Z 重心 = −1.28，`Face` = −1.31；本工具转出的
模型同样为负值即正确。

## 骨骼命名

PSK 常见的是 3ds Max Biped 的 `Bip001*` 命名。默认会映射成 MMD 标准日文名，
**英文原名写进骨骼的英文名备注字段**，两边都不丢：

| PSK | PMX 日文名 | 英文名备注 |
|---|---|---|
| `Bip001` | `グルーブ` | `Bip001` |
| `Bip001Pelvis` | `センター` | `Bip001Pelvis` |
| `Bip001Spine` | `上半身` | `Bip001Spine` |
| `Bip001Neck` | `首` | `Bip001Neck` |
| `Bip001Head` | `頭` | `Bip001Head` |
| `Bip001LClavicle` | `左肩` | `Bip001LClavicle` |
| `Bip001LHand` | `左手首` | `Bip001LHand` |
| `Bip001LThigh` | `左足` | `Bip001LThigh` |
| … | … | … |

映射不到的骨骼保留原名。想全部保留英文名用 `--raw-bone-names`。

> 注意：源骨架里通常**没有** `足ＩＫ` / `つま先ＩＫ`，本工具会按腿链自动补上，
> 否则在 MMD 里没法用 IK 摆姿势。

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

**点确定后模型照常显示，不影响模型。** 但 PSK 用户几乎必然会碰到，所以单独说明。

原因是 **`.psk` 这个扩展名被两个完全不同的东西占用了**：

| 用途 | 说明 |
|---|---|
| **Unreal ActorX 网格** | 本工具的**输入**格式 |
| **PmxEditor 的「アンカーデータ」** | PmxEditor 的锚点/权重编辑数据，**不能存进 PMX**，只能单独存成 `.psk`（PmxEditor 称之为「PMX スケルトン」） |

PmxEditor 打开 `xxx.pmx` 时，会去找**同名**的 `xxx.psk` 并自动加载 —— 见它的菜单
`[ファイル] → [アンカーデータの自動読み込み／保存]`，readme 原文：

> `[アンカーデータの自動読み込み／保存]` - モデルファイル名と同名のアンカーデータファイル(\*.psk)がある場合自動読み込み

所以「`R2T1FeiBiMd10011.psk` 转出 `R2T1FeiBiMd10011.pmx`」这种最自然的命名，
会让 PmxEditor 拿那个 Unreal 网格去当锚点数据解析，当然失败。

**规避方法（任选其一）：**

1. 把输出的 PMX 挪到别的文件夹，或者改个名；
2. 在 PmxEditor 里关掉 `[ファイル] → [アンカーデータの自動読み込み／保存]`；
3. 不管它 —— 点确定就完事了。

本工具在转换时如果检测到输出 PMX 旁边有同名的 `.psk` / `.pskx`，会在日志里主动提示。

## 已知限制

1. **朝向是固定映射**：没有 FBX 那样的「自动判定朝向」开关（PSK 的轴约定是明确的）。
   万一源文件的轴约定和常规不同导致背对镜头，请在 PMXEditor 里绕 Y 转 180°。
2. **贴图按材质名找**：在源文件所在目录（以及输出目录）里找与材质名同名的图片文件；
   找不到就导出白模，日志会提示 `关联到贴图 0 个`。
3. **最多 4 根骨骼权重**：PMX 的限制，第 5 及以后的权重会被丢弃，日志会提示条数。
4. **物理**：PSK 里没有刚体 / 关节信息，输出的 PMX 也没有，裙子头发不会自己飘。
5. **贴图格式**：DDS / KTX2 / WebP 会被跳过（材质退化为纯色）。
