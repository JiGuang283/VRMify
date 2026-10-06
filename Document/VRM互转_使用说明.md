# VRM ↔ PMX 互转说明

纯 Python 标准库实现，不依赖 Blender、mmd_tools、Unity、UniVRM、pygltflib。
（GitHub 上那个 `aia7520-gif/pmx2vrm` 是 Blender + mmd_tools + VRM 插件的方案，
需要装 Blender 和两个插件；这里直接读写二进制，不需要任何外部程序。）

## 工具清单

| 文件 | 作用 |
|---|---|
| `formats/pmxio.py` | 完整 PMX 2.0 读写（含表情 offset、IK、付与、刚体、关节） |
| `formats/vrmio.py` | GLB/glTF 容器读写 + PNG 编码 + BMP/TGA 解码 |
| `convert/pmx2vrm.py` | **PMX → VRM（1.0 / 0.x）** |
| `convert/vrm2pmx.py` | **VRM（1.0 / 0.x）→ PMX** |
| `gfx/preview.py` | 实时 3D 正面预览控件（GUI 调用） |

## 常用命令

```powershell
# PMX → VRM 1.0（默认）
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"

# PMX → VRM 0.x（老软件/老 SDK 才需要）
python convert/pmx2vrm.py "model.pmx" --spec 0x --title "名字" --author "作者"

# VRM → PMX（贴图会自动导出到 PMX 同目录）
python convert/vrm2pmx.py "model.vrm" -o "model.pmx"

# 校验 + 渲染预览
python convert/pmx_check.py "model.pmx"
```

`pmx2vrm.py` 主要参数：

| 参数 | 说明 |
|---|---|
| `--spec 1.0` / `--spec 0x` | VRM 规范版本，默认 1.0 |
| `--scale auto` | 默认。归一化到 1.6 m；也可给具体倍率 |
| `--rotate auto` | 默认按版本决定；`none` / `y180` 可强制 |
| `--flip-winding` | 强制反转三角形绕序（默认**自动判定**，通常不需要） |
| `--force-double-sided` | 所有材质强制双面（仍有镂空时的兜底） |
| `--keep-pose` | 保留原始姿势；默认把 A-pose 手臂抬成 VRM 要求的 T-pose |
| `--outline` | 按 PMX 的轮廓线设置写出 MToon 描边（默认不描边） |
| `--dense-morphs` | 表情用密集存储（默认稀疏存储，文件小很多） |
| `--max-morphs N` | 只导出前 N 个表情；`0` = 完全不导出 |
| `--title` / `--author` | 写进 VRM meta |

`vrm2pmx.py` 主要参数：`--scale`（默认归一化到 20 MMD 单位）、`--rotate`、
`--flip-winding`、`--edge`（开启 MMD 轮廓线，默认关闭）、
`--force-double-sided`、`--name`。

## 坐标系（这部分决定了模型朝向对不对）

| 格式 | 正面 | 角色左手侧 | 上 | 手性 |
|---|---|---|---|---|
| PMX（MMD） | **-Z** | **+X** | +Y | 左手系（DirectX） |
| VRM 0.x（glTF） | **-Z** | **-X** | +Y | 右手系 |
| VRM 1.0（glTF） | **+Z** | **+X** | +Y | 右手系 |

因此（2026-09-18 起的实现）：

* PMX 与 glTF 手性相反，同一组数字直接搬过去是**镜像人形**，必须反射一个轴：
  * PMX → VRM 0.x：X 取反。
  * PMX → VRM 1.0：Z 取反。
  * 「绕 Y 轴旋转 180°」是纯旋转，改不了手性，转出来左右是反的——别改回去。
* 反射会把「从外面看逆时针」颠倒成顺时针，所以**绕序要反转**。
  转换器用「反射后的数据」做「几何面法线 vs 顶点法线」投票自动判定，
  日志里会打出 `绕序自动判定（手性修正后）：与法线同向 N 面 / 反向 M 面 → 需要反转`，
  正常情况下就是判「需要反转」。
* 尺寸：MMD 约 20 单位 ≈ 1.6 m，所以换算倍率约 0.08（VRM 侧 1 单位 = 1 米）。
  表情（morph target）的偏移量同样要乘这个倍率。

### 绕序判定的实测依据

坐标系的结论不要靠推理，用数据说话，两个都很好测：

1. **正面朝向**：用脚尖骨骼减脚踝骨骼。
   云岫飞袂：`左つま先.Z − 左足ＩＫ.Z = −1.195` → 脚尖朝 −Z → PMX 正面 −Z。
   真实 VRM 0.x（VRoid 素体 / KizunaAI）：`leftToes.Z − leftFoot.Z = −0.095` → 同样 −Z。
2. **绕序**：拿每个三角形的几何法线（`(v1−v0)×(v2−v0)`）去点乘顶点法线，投票。
   抽样 6 个模型（YYBMiku / Rikka / 夕刻ロベル / シエン / ScaleMMD / 云岫飞袂）
   都是压倒性同向（例如 47340 : 202）→ 从外面看逆时针，与 glTF 一致。

> 早期版本硬编码「必须反转绕序」，结果模型被背面剔除，表现就是
> **大面积镂空 + 轮廓线糊成一片黑**。现在已改为自动判定。

### 万一还有镂空

先看日志里 `绕序自动判定` 那行。如果确实判定错了（反向面占多数），
手动加 `--flip-winding`。如果是模型本身有薄片几何（头发、裙摆、飘带）
依赖双面渲染，就加 `--force-double-sided`，或勾选界面上的「材质强制双面」。

如果导入某个软件后发现**模型背对你**，把 `--rotate` 手动反过来；
如果发现**模型破面/内外翻转**，加 `--keep-winding`。

## PMX → VRM 的映射

| PMX | VRM / glTF |
|---|---|
| 骨骼层级 | glTF 节点（只有平移，无旋转无缩放，符合 VRM 的静止姿势约束） |
| 骨骼绝对坐标 | `skin.inverseBindMatrices`（纯平移的逆） |
| 顶点权重 BDEF1/2/4、SDEF、QDEF | `JOINTS_0`（ushort×4）+ `WEIGHTS_0`（float×4，已归一化）；SDEF 退化成线性混合 |
| 付与 100% 的同位置骨（足D / ひざD / 足首D 等） | 权重和子骨并入付与親（VRM 没有付与机制，见下文） |
| A-pose 手臂 | 默认重摆成 T-pose（骨骼和顶点一起转，`--keep-pose` 关闭） |
| 材质 | 1.0：glTF 材质 + `VRMC_materials_mtoon`；0.x：glTF 材质 + MToon `materialProperties`（见下文） |
| 材质颜色 | 按 MMD 着色公式 `clamp(漫反射 × 0.6 + 环境色)` 计算，阴影色取其 0.8 倍 |
| 透明 | 看漫反射 alpha **和贴图的 alpha 分布** 自动选 OPAQUE / MASK / BLEND |
| 加算球面贴图（.spa） | MToon 的 matcap（1.0 `matcapTexture`，0.x `_SphereAdd`） |
| 轮廓线 | `--outline` 时写 MToon 描边（宽度按 edge_size，颜色取 edge_color） |
| 贴图 | 嵌入 GLB。PNG/JPEG 直接透传，BMP/TGA 现转成 PNG；不支持的（DDS 等）跳过 |
| 顶点表情（含グループ展开） | morph target（默认稀疏存储）+ VRM expression |
| 材质表情 | expression 的 `materialColorBinds`（0.x：`materialValues`），用于显示/隐藏部件 |
| 表情名 | 自动对应 VRM 预设（まばたき→blink、あ→aa、怒り→angry …）；MMD 的「上」「下」是眉毛表情，不映射成 lookUp / lookDown |

骨骼名 → VRM humanoid 的对应（下半身→hips、上半身→spine、上半身2→chest、
首→neck、頭→head、左/右腕→UpperArm、左/右ひじ→LowerArm、左/右手首→Hand、
左/右足→UpperLeg、左/右ひざ→LowerLeg、左/右足首→Foot、左/右つま先→Toes、
左/右目→Eye）。`両目` 是双眼共用的控制骨，不映射。

手指按规范版本区分：

| MMD | VRM 1.0 | VRM 0.x |
|---|---|---|
| 親指０ / １ / ２ | ThumbMetacarpal / Proximal / Distal | ThumbProximal / Intermediate / Distal |
| 親指１ / ２（没有 ０ 的老模型） | ThumbProximal / Distal | ThumbProximal / Intermediate |
| 人指・中指・薬指・小指 １ / ２ / ３ | Proximal / Intermediate / Distal | 同左 |

两个版本都没有「食指～小指的 Metacarpal」，所以 `人指０` 之类不映射。

### 材质

* **透明模式**：以前只看漫反射 alpha，贴图自带透明通道的材质（头发高光层、瞳孔、
  镂空裙摆）全被写成 OPAQUE，透明区域画成实心——实测头发被盖成一片黑、眼睛全黑。
  现在会解码贴图的 alpha（纯 Python，只解 alpha 通道并抽样，2048² 约 0.3 秒）：
  半透明像素占可见像素比例 < 30%（只有抗锯齿边缘）→ MASK，否则 → BLEND。
  会被材质表情隐藏的不透明材质用 MASK，才能真正隐藏。
  BLEND 材质按 PMX 材质顺序写 `renderQueueOffsetNumber`，保持叠加层的绘制顺序。
* **MToon**：1.0 写 `VRMC_materials_mtoon`（阴影贴图 = 主贴图，`shadingToonyFactor` 0.9），
  UniVRM / three-vrm 导入后是卡通着色而不是写实 PBR。
  0.x 的 MToon 以前阴影色写死成粉色且没有阴影贴图（暗部一片粉）、透明材质的混合参数
  等于不透明、单面材质 `_CullMode` 写成了剔除正面——都已修正。
* **乘算球面贴图（.sph）**：MToon 没有对应项，会被忽略（日志里有提示）。

### 骨骼结构的自动修正

* **付与骨合并**：很多 MMD 模型的腿部网格权重全在 `足D / ひざD / 足首D` 上，
  D 骨靠付与回転 100% 跟随 `足 / ひざ / 足首`。VRM 没有付与，而 humanoid 指向的是
  `足`，原样导出时转大腿网格完全不动。转换器把「付与回転率 1、和付与親同位置」的骨
  的权重和子骨并到付与親上（日志：`付与骨合并：N 根`）。付与率不是 1 的
  （腕捩1 的 0.25、腰キャンセル 的 -1 等）不处理。
* **层级修正**：VRM 要求每个 humanoid 骨都在规范父骨位的子树里。MMD 的 上半身 和
  下半身 是「腰」下的兄弟骨，不修的话转 hips（全身朝向）只有下半身跟着转。
  转换器会把 上半身 改挂到 下半身 下（日志：`骨骼层级：spine（上半身）不在 hips…`）。
  静止姿势下节点只有平移，改挂不影响顶点位置。
* **T-pose**：VRM 规范要求 T-pose，MMD 模型多是手臂下垂 30～40° 的 A-pose。
  默认把上臂、前臂对齐到水平 ±X，子骨与顶点（含法线、表情偏移）按蒙皮权重一起转；
  腕補助 这类付与骨按付与率跟转。
* **占位骨**：缺少必需骨骼（1.0 为 15 根，0.x 另加 chest、neck）时自动生成 `vrm_xxx`
  占位节点，挂在规范要求的父节点下；如果模型里已有本该挂在它下面的 humanoid 骨
  （比如缺 spine 但有 chest），会把那根骨一起改挂到占位骨下，保证层级合法。

## VRM → PMX 的映射

| VRM / glTF | PMX |
|---|---|
| skin joints | 骨骼（按 DFS 重排，父在前；humanoid 里登记但未蒙皮的骨骼也会保留） |
| 节点世界平移 | 骨骼位置（`tail` 指向第一个子骨骼，叶子骨骼按父子方向给偏移） |
| JOINTS_0/WEIGHTS_0 | BDEF1 / BDEF2 / BDEF4 |
| glTF 材质 | PMX 材质（diffuse 取 `baseColorFactor`，**不使用 toon**：`toon_flag=0` + `toon=-1`） |
| 嵌入贴图 | 导出成 `<pmx名>_texNN.png` 放在 PMX 同目录 |
| expression / blendShape | PMX 顶点表情（组表情会按权重叠加展开） |
| `extras.targetNames` 里没被引用的 target | 也会单独建一个 PMX 表情 |

## 已知限制

1. **不支持的贴图格式**：DDS / KTX2 / WebP 会被跳过（材质退化成纯色）。
2. **物理**：PMX 刚体、关节不会转成 VRM SpringBone（头发、裙摆在 Unity 里是僵硬的）；
   反过来 VRM 的 SpringBone / node constraint 也不会转成 PMX 刚体。
3. **材质效果**：加算球面贴图转成 matcap；乘算球面贴图、toon 贴图在 VRM 侧不保留。
   材质表情只转换漫反射色和环境色（即显示颜色和透明度），高光、轮廓线等其他项忽略。
   反方向（VRM → PMX）本工具**一律不使用 Toon**，材质写成 `toon_flag=0` + `toon=-1`
   （PMXEditor / MMD 里显示为「なし」）。

   ⚠️ 顺便记一个坑：PMX 材质的 toon 字段宽度由「共有Toonフラグ」决定，写反了会给每个材质
   硬套一层 `toon01.bmp`：

   | flag | 含义 | 字段 |
   |---|---|---|
   | `1` | 共享 / 内建 toon | **1 字节编号**，引用 MMD 的 `Data/toon01.bmp` … `toon10.bmp`，**编号 0 就是 `toon01.bmp`** |
   | `0` | 本模型纹理表里的贴图 | 纹理索引宽度，**`-1` = なし（不使用）** |

   「编号 0 = `toon00.bmp` = 不使用」是误解 —— MMD 的 `Data/` 目录里没有 `toon00.bmp`，
   mmd_tools 源码里写死了 `toon%02d.bmp % (shared + 1)`。本工具 2026-09-24 之前就是错的那个写法。
4. **骨骼表情**：PMX 的骨骼表情（kind 2）在 VRM 里没有对应物，会被忽略。
5. **T-pose 重摆**：用的是线性蒙皮（LBS），肩部权重混合区在大角度重摆时
   会有轻微收缩；物理骨（裙摆、头发）不会转成 SpringBone，所以抬腿时裙子不跟。
6. 表情默认用 glTF 稀疏 accessor 存储，只存实际动到的顶点（实测 62.7 MB → 20.1 MB）。
   个别老软件不支持稀疏 accessor 时可以加 `--dense-morphs`。

## 版权

VRM 的 meta 里带授权信息。默认写成「需要署名、禁止再分发、禁止改版」，
`allowRedistribution` / `modification` / `commercialUsage` 等字段按最保守的填。
**转换前请确认你有权使用该模型**，工具不会替你做版权检查。
