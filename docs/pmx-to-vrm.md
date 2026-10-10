# PMX → VRM 转换说明

PMX → VRM 的转换规则和命令行用法。转换器只用 Python 标准库，直接读写二进制格式，不依赖 Blender、
mmd_tools、Unity 或 UniVRM。

## 命令行

```bash
# VRM 1.0（默认）
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"

# VRM 0.x（只有老软件需要；0.x 不输出 SpringBone）
python convert/pmx2vrm.py "model.pmx" --spec 0x --title "名字" --author "作者"

# 检查输出（结构 + VRM 官方 JSON Schema，需要 jsonschema）
python tools/qa/check_vrm.py "model.vrm"
```

| 参数 | 说明 |
|---|---|
| `--spec 1.0` / `--spec 0x` | VRM 版本，默认 1.0 |
| `--scale auto` / 倍率 | 默认把身高归一到 1.6 m |
| `--keep-pose` | 保留原姿势（默认把 A-pose 手臂抬成 T-pose） |
| `--keep-control-bones` | 保留没有权重的中间骨（全ての親 / センター / グルーブ / 腰 等；默认去掉，层级像 VRoid）。IK、末端骨这类不起作用的骨总会去掉 |
| `--flip-winding` | 强制反转三角形绕序（默认自动判定） |
| `--force-double-sided` | 所有材质双面 |
| `--outline` | 按 PMX 的轮廓线设置写 MToon 描边（默认不描边） |
| `--no-spring` / `--no-spring-skirt` | 不转物理 / 不转挂在下半身的链（通常是裙摆） |
| `--spring-stiffness k` | SpringBone 硬度整体倍率，默认 1.0 |
| `--spring-gravity g` / `--spring-gravity-skirt g` | 头发等 / 裙摆的重力，默认 0 / 0.1 |
| `--no-auto-spring` | 模型没有物理刚体时不自动补 |
| `--no-spring-center` | 不以模型根节点为 SpringBone center |
| `--no-combo-expr` | 情绪预设不组合眉 / 眼 / 嘴，每个预设只对应一个表情 |
| `--no-gaze` | 不补视线表情 |
| `--no-extra-expr` | 不补 hideHighlight / tongue 表情 |
| `--dense-morphs` / `--max-morphs N` | 表情用密集存储 / 只导出前 N 个（0 = 不导出） |
| `--tex-max N` / `--tex-jpeg` | 贴图最大边长 / 不透明贴图存 JPEG（需要 Pillow） |
| `--title` / `--author` | 写进 VRM meta |

在 Python 里调用：`pmx2vrm.convert(pmx_path, vrm_path, spec="1.0", ...)`，参数和上表对应，返回统计信息。

## 坐标系

| 格式 | 正面 | 角色左手侧 | 上 | 手性 |
|---|---|---|---|---|
| PMX（MMD） | -Z | +X | +Y | 左手系 |
| VRM 0.x（glTF） | -Z | -X | +Y | 右手系 |
| VRM 1.0（glTF） | +Z | +X | +Y | 右手系 |

- PMX 和 glTF 手性相反，同一组数字直接搬过去是镜像人形，所以要反射一个轴：转 0.x 时 X 取反，转 1.0 时 Z 取反。
  绕 Y 旋转 180° 是纯旋转，改不了手性，转出来左右是反的。
- 反射会把「从外面看逆时针」变成顺时针，所以绕序通常要反转。转换器用反射后的数据，拿每个三角形的几何法线和
  顶点法线投票自动判定，日志里有一行「绕序自动判定」。
- 尺寸：MMD 模型约 20 单位高，默认归一到 1.6 m；表情偏移按同样的倍率缩放。

模型镂空时先看「绕序自动判定」那行：判错了（反向面占多数）加 `--flip-winding`；模型本身是薄片几何
（头发、裙摆、飘带）依赖双面显示的，加 `--force-double-sided`。

## 映射

| PMX | VRM / glTF |
|---|---|
| 骨骼层级 | glTF 节点（静止姿势只有平移），`skin.inverseBindMatrices` |
| 顶点权重 BDEF1/2/4、SDEF、QDEF | `JOINTS_0` + `WEIGHTS_0`（每顶点最多 4 根，已归一化）；SDEF 按线性混合 |
| 材质 | MToon：1.0 写 `VRMC_materials_mtoon`，0.x 写 `materialProperties` |
| 加算球面贴图（.spa） | MToon matcap |
| 贴图 | 嵌入 GLB；PNG / JPEG 原样，其它格式转成 PNG |
| 顶点表情（含グループ） | morph target（默认稀疏存储）+ VRM 表情 |
| 材质表情 | 材质颜色绑定（显示 / 隐藏部件） |
| 骨骼表情 | 在静止姿势下摆骨、按线性蒙皮算出顶点位移，烘焙成 morph target |
| UV 表情 | 整个材质一起缩放 / 平移的 → 贴图偏移绑定；做不到的跳过并提示 |
| 左右眼骨 | humanoid leftEye / rightEye，lookAt 用骨骼 |
| 物理刚体 + 关节 | `VRMC_springBone`（仅 1.0） |
| 跟随骨骼的刚体 | SpringBone 碰撞体（球 / 胶囊） |

### 骨骼

MMD 标准骨名对应 VRM humanoid：下半身 → hips、上半身 → spine、上半身2 → chest、首 → neck、頭 → head、
腕 / ひじ / 手首 → UpperArm / LowerArm / Hand、足 / ひざ / 足首 / つま先 → UpperLeg / LowerLeg / Foot / Toes、
目 → Eye（`両目` 是双眼共用的控制骨，不映射）。手指按版本区分：

| MMD | VRM 1.0 | VRM 0.x |
|---|---|---|
| 親指０ / １ / ２ | ThumbMetacarpal / Proximal / Distal | ThumbProximal / Intermediate / Distal |
| 親指１ / ２（没有 ０ 的模型） | ThumbProximal / Distal | ThumbProximal / Intermediate |
| 人指・中指・薬指・小指 １ / ２ / ３ | Proximal / Intermediate / Distal | 同左 |

常见的英文骨名（Unity / Mixamo / VRoid / Blender 风格）也能对应。转换时自动修正：

- **付与骨合并**：VRM 没有付与。付与回転率 1、和付与親同位置的骨（足D / ひざD / 足首D 等，腿部网格的权重
  常在它们上面）连同权重和子骨并入付与親；付与率不是 1 的（腕捩、腰キャンセル 等）不动。
- **层级**：VRM 要求每个 humanoid 骨在规范父骨位的子树里。MMD 的上半身和下半身是兄弟骨，转换时把上半身改挂到
  下半身下，静止姿势下顶点不动。
- **hips 位置**：MMD 的下半身和上半身从同一点出发，hips 和 spine 几乎重合。hips 离 spine 太近时把它移到骨盆
  （大腿根往上），和 VRoid 的比例接近；只挪节点，网格和权重不变。
- **T-pose**：上臂、前臂对齐到水平，子骨和顶点（含法线、表情偏移）按蒙皮权重一起转。
- **占位骨**：缺少规范必需的骨（以及 chest、neck，很多程序默认它们存在）时补 `vrm_xxx` 占位节点，并保证层级合法。
- **去掉无效骨**：既不带权重、也不是 humanoid 骨或物理关节 / 碰撞体，下面也没有这类骨的节点（IK、末端骨、合并掉的
  付与骨、操作中心等）不写进 VRM。「精简控制骨」（默认开，`--keep-control-bones` 关闭）再去掉没有权重的中间骨（全ての親 / センター /
  グルーブ / 腰 / 肩P 等），子骨挂到最近的保留骨上，层级和 VRoid 一样是 Root > Hips。都只删节点，网格和动作不变。

### 材质

- **透明模式**：根据漫反射 alpha 和贴图 alpha 的分布自动选。只有边缘抗锯齿的用 MASK（裁剪），半透明像素多的用
  BLEND；会被材质表情隐藏的不透明材质用 MASK。贴图只统计材质的 UV 用到的区域，几个部件共用的图集、拼着用不到的
  半透明图层不会让不透明的部件变成半透明。BLEND 材质按 PMX 材质顺序设置绘制顺序。
- **颜色**：按 MMD 的着色公式算出显示颜色；VRM 1.0 规定颜色因子是线性值，写入时从 sRGB 换算。
- **toon → 阴影**：MMD 的明暗来自 toon 贴图。阴影色取 toon 最暗端、亮部取最亮端，明暗交界的位置和宽度换算成
  MToon 的 shadingShift / shadingToony。动漫风格的皮肤 toon 换算后阴影色是浅粉、交界靠后，和 VRoid 原生模型
  的观感一致。没有 toon 的材质（通常是眼睛、眉毛、嘴）不画阴影。
- **乘算球面贴图**（.sph）在 MToon 里没有对应项，会被忽略并在日志里提示。

### 表情

- **预设对应**：按表情名对应 VRM 预设（まばたき → blink、あ → aa …）。比较时不分全角半角、大小写和分隔符，
  也认变体后缀（怒りA、まばたき2）。除了 MMD 日文名，也认 VRoid（`Fcl_ALL_Joy`）、VRChat（`vrc.v_aa`）等英文名、
  ARKit 和常见中文名（眨眼、开心 …），日文名优先。MMD 的「上」「下」是眉毛表情，不当成视线。
- **单眼眨眼**：按动的是哪一边的顶点定 blinkLeft / blinkRight，不看名字（不同模型的命名习惯不一致）。
- **情绪预设组合**：happy / angry / sad / relaxed / surprised 像 VRoid 一样组合眉、眼、嘴三个部位，例如
  angry = 怒り + ｷﾘｯ + 口角下げ。权重按 VRoid 的预设偏含蓄，方便和眨眼、口型叠加。至少两个部位找到才组合，
  原来单个对应的表情保留为自定义表情。候选表情和权重见 `convert/vrmconv/expressions.py` 的 `COMBO`；
  只有 ARKit 表情的模型用 ARKit 的组合。happy / relaxed（笑眼）设 `overrideBlink: blend`，笑眼时不再叠加眨眼。
- **退路**：名字对应不上时，眨眼用 ARKit 的 eyeBlinkLeft + eyeBlinkRight 或左右单眼合起来，口型用 ARKit 的
  jawOpen、mouthPucker 等。
- 转换日志里的「表情预设」一行列出每个预设用了哪些表情、缺哪些。
- **网格划分**：模型按表情分成几个网格（类似 VRoid 的 Face / Body / Hair），每个网格只带动得到它的 morph target。
  glTF 要求同一个网格的每个部分带同样的 morph target，而 three.js、Unity 等读取端会展开成「顶点数 × 表情数」的
  完整数组；分开后，表情多的大模型在预览和游戏里能省下几百 MB 内存。网格名取网格里最大的材质名。
- **存储**：默认用 glTF 稀疏 accessor，只存实际动到的顶点，文件小很多；个别不支持稀疏 accessor 的老软件用
  `--dense-morphs`。

### 视线

- lookAt 的最大转角按眼球几何算：MMD 模型的眼骨支点常贴着眼球，固定角度转眼看不出来，所以按「瞳孔移动约
  瞳孔宽度的 28%」反推左右角度（8°～20°），向下取 0.7 倍、向上 0.5 倍；算不出时用 10°。
- 有左右眼骨时补 lookLeft / lookRight / lookUp / lookDown 四个预设表情（转眼骨烘焙成顶点位移），给直接播放
  这几个表情的程序用；模型自己有同名表情时不补。每个方向烘焙成「横向」「深度」两个 morph target
  （`lookLeftRight_depth`、`lookUpDown_depth` 只是 morph target，不是表情），按 sin / (1 − cos) 的权重组合，
  任意角度都和直接转眼骨一致。所以在界面的「调整」里改视线角度时，这几个表情跟着变，不用重新转换。
- 补两个自定义表情，名字固定，程序按名字就能找到（模型已有同名表情时不补）：
  - `hideHighlight`：关掉眼睛高光。模型自带关闭高光的表情（「ハイライト消し」「eye hi off」等，几组高光各有一个时
    合成一个）就用它；没有就按材质名找眼睛高光（「目光」「ハイライト」「eye hi」等，头发高光除外），生成一个把这些
    面各自收成一点的表情。高光直接画在瞳孔贴图里的模型做不了，日志里会说明。
  - `tongue`：吐舌，复制模型里的「舌出し」「ぺろっ」「べー」「tongueOut」等表情。

### 物理 → SpringBone（仅 VRM 1.0）

目标是在 UniVRM、three-vrm 里的手感接近原生 VRM 模型，而不是复刻 MMD 的 Bullet 物理。参数按 VRM 官方和 VRoid
示例模型在同一套动作（待机、转身、走路、跑步、抬腿）下的表现校准。

- **拆链**：挂了物理刚体的骨从「自己动、父骨不动」处开始沿子骨走成链，分叉处另起一条；链尾补 `xxx_tail` 节点。
- **分类**：挂在 hips 下、或从躯干垂到胯部以下的链算裙摆类；挂在手臂、腿上的算四肢配饰；其余（头发、胸前饰品等）
  算头发类。
- **抽稀**：MMD 物理链切得很细，原生发束一般 3～7 节。头发类每节至少约 12 cm；裙摆类保留得密一些，因为
  SpringBone 只在关节点检测碰撞，节太稀腿会从关节之间穿过去。
- **硬度**：SpringBone 的 stiffness 不随节长缩放，所以按「每帧回正的角度」换算：头发类软、裙摆类硬（撑住造型、
  少穿模）、挂在手臂上的链介于两者之间，并限制在 2～10 之间。
- **阻力和重力**：阻力 0.6；重力头发类 0（加了会把散开的发型拉垂），裙摆类 0.1（抬腿后裙片落回腿上）。
- **短链不模拟**：总长不足 5 cm 的链和很短的四肢配饰（鞋带、手肘吊坠）跟着骨骼刚性移动，否则只会在末端高频颤动。
- **center = 模型根节点**：在模型自身的空间里模拟，人物整体移动（走路的根运动）时头发、裙摆不会被甩到身后、
  压到腿上；需要「走路时头发往后飘」时用 `--no-spring-center`。
- **碰撞**：跟随骨骼的刚体转成碰撞体，按 PMX 的碰撞组 / 掩码决定每条链和哪些碰撞体碰撞。静止时就压在碰撞体
  里的那几节缩小半径，免得一开始模拟就被弹开；挂在手臂上的链不碰撞。
- **链间横向关节**：PMX 裙摆常用横向关节把相邻的链连成一片，SpringBone 无法表达，只能舍弃（日志提示数量）。
  为了不让腿从两条链之间把裙子劈开，每条裙摆链配一份朝裙片法线方向平移过的腿部碰撞体，被腿碰到时整片沿法线
  方向移动。
- **模型没有物理时**（FBX 等转来的模型常见）：按骨骼名和骨架结构自动补头发、裙摆、袖子、飘带、尾巴的链，
  身体自动加碰撞体（`--no-auto-spring` 关掉）。

手感不合适时，先用 `--spring-stiffness` 整体调；导入 Unity 后也可以在 UniVRM 里逐条调，或者用界面的「调整」面板。

## 已知限制

1. **VRM 0.x 不输出 SpringBone**。
2. **物理**是近似换算：链间横向关节舍弃，整片裙子的联动不如原 PMX；腿抬得很高时裙摆最上一圈和大腿根部会有少量
   重叠（原生 VRM 模型同样如此）。
3. **材质**：乘算球面贴图、共享 toon07～10 没有对应；材质表情只转换显示颜色和透明度。
4. **骨骼表情**按静止姿势和线性蒙皮烘焙，不经过 IK；权重在 0 和 1 之间时和 MMD 里略有差别（MMD 是旋转插值）。
5. **T-pose** 用线性蒙皮重摆，肩部权重混合区在大角度时会轻微收缩。
6. **表情预设**按名字对应和组合；名字都认不出的模型组合不出来，可以在界面的「调整」里手动指定。

## 授权信息

VRM meta 默认写成最保守的授权：需要署名、禁止再分发、禁止修改、仅限个人非商业使用，可以在界面的「VRM 信息」
里改。**转换前请确认你有权使用该模型**，工具不会替你做版权检查。
