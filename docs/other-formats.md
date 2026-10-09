# 其它格式 → PMX

FBX、unitypackage、uemodel、psk / pskx、xps 先转成 PMX，再走 [PMX → VRM](pmx-to-vrm.md)。图形界面里默认一路转到 VRM，
中间的 PMX 和解包出来的资源放在临时目录，输出目录只有 VRM；「输出」页选「只转 PMX」时输出 PMX 和 `textures/`。

转出的 PMX 也能直接在 MMD / PmxEditor 里用：文本编码是 MMD 要求的 UTF-16LE，材质不使用 toon，每个顶点最多 4 根
骨骼权重，贴图拷进 `textures/`。

## FBX / unitypackage

```bash
python convert/fbx2pmx.py "model.fbx" -o "model.pmx"
python convert/fbx2pmx.py "model.fbx" --info                      # 只看结构，不转换
python formats/unitypackage_unpack.py "pack.unitypackage" --list  # 列出包里的文件
python formats/unitypackage_unpack.py "pack.unitypackage" -o out  # 解包
```

| 参数 | 说明 |
|---|---|
| `--scale mmd` / `raw` / 倍率 | 默认缩放到 MMD 标准身高（约 20 单位）；`raw` 保持 FBX 原尺寸 |
| `--alpha auto` / `keep` / `strip` | 贴图透明通道：默认逐张判定，`keep` 原样保留，`strip` 一律去掉 |
| `--no-auto-facing` / `--face-180` | 关掉朝向自动判定 / 在判定结果上再转 180° |
| `--no-morphs` / `--max-morphs N` | 不导出表情 / 最多导出 N 个 |
| `--keep-untextured-meshes` | 保留没有材质的网格（默认跳过） |
| `--no-center` | 不把模型移到原点、落到地面 |
| `--no-flip-z` | 不做右手系 → 左手系转换（调试用） |

- **格式**：二进制和 ASCII FBX 7.x，不需要 FBX SDK。网格、UV、法线、蒙皮权重、骨骼层级、BlendShape 表情都会转换；
  四边形自动三角化，绕序按法线自动判定。
- **朝向**：FBX 没有「正面朝哪」的字段，按脚尖骨骼方向判定，没有脚尖时看脸部网格的位置。判错时用 `--face-180`。
- **贴图透明**：MMD 把贴图 alpha 当作材质透明度，而游戏贴图的 alpha 常常存的是别的数据（高光、遮罩），留着会让整块
  模型变透明。`auto` 按 alpha 的分布判断是真透明还是数据遮罩；去掉 alpha 的贴图另存为 `<名字>_noalpha`，不改原文件。
- **效果片**：没有材质的网格多是游戏里的特效面片，转出来会是一块盖在身上的灰板，默认跳过。
- **Unity 资源**：FBX 里没写贴图文件名时（unitypackage 里常见），按 Unity 的材质文件（.mat）找回贴图。
- **命名**：英文骨名（HumanIK / Mixamo / VRoid / Blender / 3ds Max 风格）和英文表情名（VRChat、VRoid、`MTH_A` 等）
  在转 VRM 时自动对应；PMX 里保留原来的英文骨名，所以直接套 MMD 动作（按日文骨名匹配）需要先改名。
- **替换网格**：同一位置的几套网格（手势、张嘴的头）只显示一套，其余做成切换用的表情。
- **unitypackage**：本质是 gzip 打包的 tar，界面里直接加即可；包里有几套服装时会同时转。只有衣服 / 配件、没有身体的
  模型在开启「只转换人物」时跳过。
- FBX 里没有物理（刚体、关节）。转 VRM 时会按骨骼名自动补 SpringBone。

## uemodel（UEFormat）

FortnitePorting / FModel 导出的 `.uemodel`（UEFormat v1～v10）。

```bash
python convert/uemodel2pmx.py "model.uemodel" -o "model.pmx"
python convert/uemodel2pmx.py "part.uemodel" --align-to "body.uemodel"   # 把拆开的部件放回整身模型上
```

| 参数 | 说明 |
|---|---|
| `--scale fixed` / `mmd` / 倍率 | 默认 `fixed`：按 UE 厘米统一换算，几个部件保持相对大小；`mmd` 每个文件各自归一到 20 单位 |
| `--align-to 整身.uemodel` | 按 `*_adjust` 骨求平移，把部件对齐到整身模型（界面里在「输入格式」页选参考模型） |
| `--lod N` | 用第几级 LOD |
| `--keep-alpha` | 保留贴图 alpha（默认去掉：UE 贴图的 alpha 多是数据遮罩） |
| `--no-textures` / `--no-add-uv` | 不关联贴图 / 不写附加 UV |
| `--edge` / `--force-double-sided` | 开启轮廓线 / 材质双面 |
| `--fbx` | 同时写一份 ASCII FBX |

贴图不在 `.uemodel` 里：按导出目录里的材质实例 `MI_<材质名>.json` 找基础色贴图。表情（MORPHTARGETS）转成顶点表情。

## psk / pskx（Unreal ActorX）

```bash
python convert/psk2pmx.py "model.pskx" -o "model.pmx"
```

| 参数 | 说明 |
|---|---|
| `--scale mmd` / `raw` / 倍率 | 默认归一到 20 单位 |
| `--raw-bone-names` | 骨骼保留原始英文名（默认转 MMD 日文标准名，原名写进英文名字段） |
| `--no-ik` | 不补 MMD 的足 IK 骨 |
| `--no-morphs` | 不导出表情 |
| `--keep-alpha` | 保留贴图 alpha |
| `--no-textures` / `--no-add-uv` | 不找贴图 / 不写附加 UV |
| `--edge` / `--force-double-sided` | 开启轮廓线 / 材质双面 |
| `--fbx` | 同时写一份 ASCII FBX |

- **骨骼**：3ds Max Biped 的 `Bip001*` 命名转成 MMD 标准日文名（Bip001Pelvis → センター、Bip001LThigh → 左足 …），
  对应不上的保留原名；按腿部骨链补 `左足ＩＫ` / `左つま先ＩＫ`，在 MMD 里可以直接摆姿势。
- **表情**：MRPH 顶点位移转成 PMX 顶点表情；附加 UV（EXTRAUVS）写成 PMX 附加 UV。
- **贴图**：按材质名在源文件目录里找同名图片，找不到时是白模（日志会提示）。
- 参考实现：[io_scene_psk_psa](https://github.com/DarklightGames/io_scene_psk_psa)。

## xps（XNALara）

```bash
python convert/xps2pmx.py "model.xps" -o "model.pmx"
```

| 参数 | 说明 |
|---|---|
| `--scale mmd` / `raw` / 倍率 | 默认归一到 20 单位 |
| `--raw-bone-names` / `--no-ik` | 保留英文骨名 / 不补足 IK |
| `--show-unused` | `unused` 占位骨也放进主表示枠（默认单独一组） |
| `--one-sided` | 材质不默认双面 |
| `--no-textures` / `--edge` / `--fbx` | 不拷贴图 / 开启轮廓线 / 同时写 FBX |

XPS 的英文描述式骨名（`arm left elbow`、`leg right knee` …）转成 MMD 标准日文名；贴图 alpha 保留（XPS 的 alpha 就是
头发、睫毛的透明度）。XPS 没有表情。

## 常见问题

**PmxEditor 打开时提示「アンカーデータの読み込みに失敗しました」**：PmxEditor 会自动加载和模型同名的 `.psk` 当作
锚点数据，而 `.psk` 也是 Unreal 网格的扩展名。点确定即可，模型不受影响；把 PMX 换个名字或文件夹，或者关掉
`[ファイル] → [アンカーデータの自動読み込み／保存]` 就不会再弹。

**MMD 提示无法载入 PMX**：MMD 只读 UTF-16LE 编码的 PMX。本工具输出的都是 UTF-16LE；别处来的文件可以用
`python formats/pmxio.py "旧文件.pmx"` 转换编码（`-o` 指定输出，`--in-place` 覆盖原文件）。

**模型背对镜头 / 躺倒**：FBX 用 `--face-180`；其它格式的轴约定是固定的，遇到特殊的源文件可以在 PmxEditor 里绕 Y 转 180°。
