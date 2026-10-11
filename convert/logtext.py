# -*- coding: utf-8 -*-
"""转换日志的英文版。转换器里的日志直接写中文句子；界面是英文时按这里的对照表翻译。

    logtext.to_en(msg) → 英文；对不上的返回 None（界面照常显示中文）

TEMPLATES 的中文句式和代码里的 log(...) / raise 一一对应（tests/test_logtext.py 扫描源码，漏了会报错），
句式里的 %d / %s / %.1f 匹配成参数。参数是程序生成的中文（「需要反转」「左」、另一条句式）时再翻译，
模型里的名字（骨骼、材质、文件名）原样保留。英文句式里用 {0} {1} … 引用参数，顺序可以和中文不同。
"""
import re

TEMPLATES = {
    # ---- 流程（pipeline / server / parallel）
    "→ %s": "→ {0}",
    "· %s": "· {0}",
    "输出目录：%s": "Output folder: {0}",
    "解压出 %d 个文件，其中模型 %d 个：%s": "Extracted {0} files, {1} of them models: {2}",
    "解压内层压缩包 %s": "Extracting the inner archive {0}",
    "用 %s 解压": "Extracting with {0}",
    "%s共有 %d 个模型，并行转换 %d 个": "{1} models {0}, converting {2} at a time",
    "压缩包中": "in the archive",
    "包中": "in the package",
    "解包 %s：%d 个文件，%d 个 FBX": "Unpacked {0}: {1} files, {2} FBX",
    "读取 FBX：%s": "Reading FBX: {0}",
    "PMX → VRM：%s": "PMX → VRM: {0}",
    "跳过 %s：%s（如需转换，请在「转换」页关闭「只转换人物」）":
        "Skipped {0}: {1} (to convert it anyway, turn off \"Characters only\" on the Convert tab)",
    "未识别到人形骨架，可能是道具": "no humanoid skeleton found, probably a prop",
    "头部没有网格，可能只有服装或配件": "nothing is attached to the head, probably only clothes or accessories",
    "手部没有网格，可能只有服装或配件": "nothing is attached to the hands, probably only clothes or accessories",
    "%s 转换失败：%s": "{0} failed: {1}",
    "没有执行": "not run",
    "已应用保存的调整（%d 项）": "Applied the saved Tune edits ({0})",
    "已写出 %s": "Wrote {0}",
    "无法启动子进程，改为逐个转换": "Couldn't start worker processes, converting one at a time",
    "完成，用时 %.1f 秒": "Done in {0} s",
    "并行转换 %d 个文件（共 %d 个）": "Converting {0} files at a time ({1} in total)",
    "已停止，%d 个文件未转换": "Stopped, {0} files not converted",
    "预览：未找到人物模型，忽略「只转换人物」生成预览":
        "Preview: no character found, previewing without \"Characters only\"",
    "调整写入 %s 失败：%s": "Writing the Tune edits to {0} failed: {1}",
    "调整已写入 %s": "Tune edits written to {0}",
    "缩略图已自动生成并写入 %s": "Thumbnail generated and written to {0}",
    # ---- 错误（raise，界面显示在文件下面）
    "包中没有 FBX 文件": "The package has no FBX files",
    "无法转换此文件：%s": "Can't convert this file: {0}",
    "压缩包中没有可转换的模型（PMX / FBX / uemodel / psk / xps）":
        "The archive has no models to convert (PMX / FBX / uemodel / psk / xps)",
    "不支持的输入：%s": "Unsupported input: {0}",
    "无法创建输出目录 %s：%s": "Can't create the output folder {0}: {1}",
    "此 PMX 没有可用的面数据": "This PMX has no faces to convert",
    "不是 VRM 文件": "Not a VRM file",
    "不是支持的压缩包：%s": "Not a supported archive: {0}",
    "既不是二进制 FBX，也解析不出 ASCII FBX 节点：%s": "Neither a binary FBX nor readable as ASCII FBX: {0}",
    "不是 PMX 文件：%s": "Not a PMX file: {0}",
    "PMX 解析后仍有 %d 字节未读完（已读 %d / 共 %d），说明某一段的结构与写入程序不一致，请反馈此模型文件。":
        "{0} bytes left after reading the PMX ({1} of {2} read): a section doesn't match what the writing "
        "program produced. Please report this model file.",
    "改写后回读校验失败，未写入：%s": "Verification after rewriting failed, nothing written: {0}",
    "%s读取「%s」时越过文件末尾（偏移 %d / 文件 %d 字节）。可能是某一段的结构与写入程序不一致，请反馈此模型文件。":
        "{0}reading \"{1}\" went past the end of the file (offset {2} of {3} bytes). A section may not match what "
        "the writing program produced. Please report this model file.",
    "%s「%s」长度 %d 超出文件剩余字节（偏移 %d / %d）。": "{0}\"{1}\" length {2} is past the end of the file (offset {3} / {4}).",
    "PMX 解析错位（段落：%s）：": "PMX out of step (section: {0}): ",
    "头部": "header", "顶点": "vertices", "面": "faces", "贴图": "textures", "材质": "materials", "骨骼": "bones",
    "表情": "morphs", "表示枠": "display frames", "刚体": "rigid bodies", "关节": "joints",
    "顶点[%d]": "vertex [{0}]", "材质[%d]": "material [{0}]", "骨骼[%d]": "bone [{0}]", "表情[%d]": "morph [{0}]",
    "表示枠[%d]": "display frame [{0}]", "刚体[%d]": "rigid body [{0}]", "关节[%d]": "joint [{0}]",
    "该模型是 PMX 2.1 且含 %d 个 SoftBody，本转换器暂不支持 SoftBody。":
        "This is a PMX 2.1 model with {0} SoftBodies; SoftBody isn't supported yet.",
    "未知的 PMX 表情类型 %d": "Unknown PMX morph type {0}",
    "不是 UEFormat 文件（魔数不是 UEFORMAT）：%s": "Not a UEFormat file (magic isn't UEFORMAT): {0}",
    "不是模型文件（Identifier=%r）：%s": "Not a model file (Identifier={0}): {1}",
    "UEFormat 版本 %d 高于本库支持的 %d：%s": "UEFormat version {0} is newer than the supported {1}: {2}",
    "解析失步：消费 %d 字节，文件 %d 字节（差 %d）": "Parsing went out of step: read {0} bytes of {1} (off by {2})",
    "不支持的压缩格式 %r：%s": "Unsupported compression {0}: {1}",
    "该 .uemodel 是 ZSTD 压缩体，但运行环境缺少 zstandard 库，无法解压：%s":
        "This .uemodel is ZSTD compressed but the zstandard library is missing: {0}",
    "ZSTD 解压失败：%s（%s）": "ZSTD decompression failed: {0} ({1})",
    "GZIP 解压失败：%s（%s）": "GZIP decompression failed: {0} ({1})",
    "不是 GLB/VRM 文件：%s": "Not a GLB / VRM file: {0}",
    "GLB 里没有 JSON chunk": "The GLB has no JSON chunk",
    "此文件中没有网格数据（PNTS / VTXW 为空）": "The file has no mesh data (PNTS / VTXW are empty)",
    "此文件中没有面数据（FACE 块为空）": "The file has no faces (the FACE block is empty)",
    "此 .uemodel 中没有 LOD 数据": "This .uemodel has no LODs",
    "此文件中没有网格数据": "The file has no mesh data",
    "骨骼数异常（%d），文件可能不是 XPS 或已损坏": "Bad bone count ({0}): not an XPS file, or damaged",
    "网格数异常（%d）": "Bad mesh count ({0})",
    "文件太小，不是 XPS 模型": "The file is too small to be an XPS model",
    "文件中没有骨骼数据": "The file has no bones",
    "文件中没有网格数据": "The file has no meshes",
    "文件在偏移 %d 处提前结束（还需 %d 字节，仅剩 %d）": "The file ends early at offset {0} ({1} more bytes needed, {2} left)",
    "网格 %d 的 UV 层数/贴图数异常（%d / %d）": "Mesh {0} has a bad UV layer / texture count ({1} / {2})",
    "网格 %d 的顶点数异常（%d）": "Mesh {0} has a bad vertex count ({1})",
    "网格 %d 的三角面数异常（%d）": "Mesh {0} has a bad triangle count ({1})",
    "XPS 头部异常（countOfUnknownInts=%d）": "Bad XPS header (countOfUnknownInts={0})",
    "字符串长度编码异常（偏移 %d）": "Bad string length encoding (offset {0})",
    "顶点权重根数异常（%d）": "Bad number of vertex weights ({0})",
    # ---- PMX → VRM
    "PMX：顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d · 表情 %d":
        "PMX: {0} vertices · {1} triangles · {2} bones · {3} materials · {4} morphs",
    "权重：左右对称的 %d 对顶点，右侧主要绑在 %s（%s），左侧却绑在 %s（%s），动作时这部分会变形。可能是模型的权重错误，建议在 PmxEditor 中检查":
        "Weights: in {0} mirrored vertex pairs the right side follows {1} ({2}) but the left follows {3} ({4}), "
        "so this part will deform when moving. Probably a weight mistake in the model; check it in PmxEditor",
    "绕序自动判定（手性修正后）：与法线同向 %d 面 / 反向 %d 面 → %s":
        "Winding (after the handedness fix): {0} faces agree with the normals / {1} oppose → {2}",
    "需要反转": "flip",
    "保持不变": "keep",
    "反转": "flipped",
    "无": "none",
    "Z 轴取反": "Z mirrored",
    "X 轴取反": "X mirrored",
    "缩放：%.5f（MMD 单位 → 米）· 手性修正：%s · 绕序：%s":
        "Scale: {0} (MMD units → metres) · handedness fix: {1} · winding: {2}",
    "物理：VRM 0.x 不支持 SpringBone，头发和裙摆不会摆动": "Physics: VRM 0.x has no SpringBone, hair and skirts won't sway",
    "视线：左右 %.1f° · 下 %.1f° · 上 %.1f°（%s）": "Gaze: sideways {0}° · down {1}° · up {2}° ({3})",
    "视线：%s": "Gaze: {0}",
    "按眼球结构计算，瞳孔最多移动约 %d%% 瞳孔宽度": "from the eyeball shape, the pupil moves at most about {0}% of its width",
    "算出 %.1f°，限制到 %.0f°": "{0}° computed, limited to {1}°",
    "眼骨上没有瞳孔顶点，使用默认值 10°": "no pupil vertices on the eye bones, using the default 10°",
    "眼骨上的顶点不像一只眼睛（宽 %.2f、支点到表面 %.2f，两眼相距 %.2f），使用默认值 10°":
        "the eye bone vertices don't look like an eye (width {0}, pivot to surface {1}, eyes {2} apart), "
        "using the default 10°",
    "网格：按材质分为 %d 个部分": "Mesh: {0} parts by material",
    "网格：按表情分为 %d 个（%s）": "Mesh: split into {0} by morphs ({1})",
    "%s：%d 个表情": "{0}: {1} morphs",
    "已写出 %s（%.2f MB）· 节点 %d · morph target %d · 表情 %d":
        "Wrote {0} ({1} MB) · {2} nodes · {3} morph targets · {4} expressions",
    "骨骼：已去掉 %d 个不起作用的节点（%s），保留 %d 个": "Bones: removed {0} nodes that do nothing ({1}), {2} kept",
    "无权重且不属于 humanoid、物理的骨，包括中间的控制骨":
        "unweighted, not humanoid or physics, including the intermediate control bones",
    "IK、末端骨、已合并的付与骨等无权重且不属于 humanoid、物理的骨":
        "IK, tip and merged append bones and others that are unweighted and not humanoid or physics",
    "表情：补充视线表情 %s（由转动眼骨烘焙，lookAt 仍使用骨骼）":
        "Expressions: added gaze expressions {0} (baked from turning the eye bones; lookAt still uses the bones)",
    # ---- 骨骼（skeleton / humanoid）
    "朝向：模型%s，已绕 Y 轴旋转 %d° 校正（左右肩 / 胯不在 X 轴上）":
        "Facing: the model {0}, turned {1}° about Y (shoulders / hips weren't along X)",
    "背向前方": "faces backwards",
    "朝向侧面": "faces sideways",
    "付与骨合并：%d 根（%s）→ 权重并入付与親": "Append bones merged: {0} ({1}) → weights moved to their append parents",
    "付与骨合并：%s 与付与親的位置略有偏差，合并后绕付与親的位置转动":
        "Append bones merged: {0} sat slightly off their append parents and now turn about the parents' positions",
    "hips：已移至骨盆，下移 %.1f cm（原与 spine 相距 %.1f mm）": "hips: moved down to the pelvis by {0} cm (was {1} mm from spine)",
    "骨骼：模型缺少 %s，已生成占位骨骼": "Bones: the model has no {0}, added a placeholder bone",
    "骨骼：%s 与 %s 对应同一根骨骼，已忽略 %s": "Bones: {0} and {1} are the same bone, {2} ignored",
    "骨骼层级：%s（%s）不在 %s（%s）之下，已移至其下（原父骨骼 %s）":
        "Bone hierarchy: {0} ({1}) wasn't under {2} ({3}), moved there (parent was {4})",
    "骨骼层级：%s（%s）无法修正，已从 humanoid 中移除": "Bone hierarchy: {0} ({1}) can't be fixed, removed from the humanoid",
    "骨骼层级：%s（%s）无法修正，部分软件可能无法加载": "Bone hierarchy: {0} ({1}) can't be fixed, some programs may not load it",
    "T-pose：%s 上臂 %.1f° · 前臂 %.1f°": "T-pose: {0} upper arm {1}° · forearm {2}°",
    "左": "left",
    "右": "right",
    "肩": "shoulder", "上臂": "upper arm", "前臂": "forearm", "手": "hand",
    "大腿": "thigh", "小腿": "lower leg", "脚": "foot", "脚趾": "toes",
    # ---- 物理（springbone / autophys）
    "物理：模型不含物理刚体，已按骨骼名和骨架结构自动添加 %d 根摆动骨（头发、裙摆、袖子、飘带等）和 %d 个身体碰撞体":
        "Physics: the model has no rigid bodies, so {0} swaying bones (hair, skirts, sleeves, ribbons …) and "
        "{1} body colliders were added from the bone names and the skeleton",
    "SpringBone：%d 条链（裙摆类 %d、头发等 %d）· 碰撞体 %d · 碰撞组 %d":
        "SpringBone: {0} chains ({1} skirt-like, {2} hair and others) · {3} colliders · {4} collider groups",
    "SpringBone：MMD 物理骨 %d 根 → SpringBone 关节 %d 个（头发等每节至少 %.0f cm、裙摆至少 %.0f cm，中间的骨骼刚性跟随）":
        "SpringBone: {0} MMD physics bones → {1} SpringBone joints (at least {2} cm per segment for hair, "
        "{3} cm for skirts; the bones in between follow rigidly)",
    "SpringBone：按设置跳过 %d 条下半身物理链（裙摆等）": "SpringBone: skipped {0} lower-body chains (skirts …) as set",
    "SpringBone：按设置跳过 %d 条下半身物理链，没有可输出的链": "SpringBone: skipped {0} lower-body chains as set, no chains left",
    "SpringBone：%d 条短链（不足 %.0f cm，四肢上不足 %.0f cm）不转换，以免末端高频抖动":
        "SpringBone: {0} short chains (under {1} cm, under {2} cm on limbs) left out to avoid jittery tips",
    "SpringBone：%d 条链的末端没有子骨骼，已补充末端节点": "SpringBone: {0} chains had no bone past their end, tip nodes added",
    "SpringBone：静止姿势下与碰撞体重叠，已%s": "SpringBone: overlapping colliders at rest, so {0}",
    "取消 %d 处链与碰撞体的碰撞": "turned off {0} chain–collider pairs",
    "缩小 %d 个节点的碰撞半径": "shrank the hit radius of {0} nodes",
    "SpringBone：%d 个链间横向关节（裙摆等）无法表达，已舍弃%s":
        "SpringBone: {0} sideways joints between chains (skirts …) can't be expressed and were dropped{1}",
    "；已按裙片朝向修正 %d 处裙摆与腿的碰撞方向，减少裙片被腿分开":
        "; {0} skirt–leg collisions were adjusted to the skirt panels' direction so the legs split the skirt less",
    "已按裙片朝向修正 %d 处裙摆与腿的碰撞方向，减少裙片被腿分开":
        "{0} skirt–leg collisions were adjusted to the skirt panels' direction so the legs split the skirt less",
    "SpringBone：已按裙片朝向修正 %d 处裙摆与腿的碰撞方向":
        "SpringBone: {0} skirt–leg collisions adjusted to the skirt panels' direction",
    # ---- 表情
    "表情预设：%s": "Preset expressions: {0}",
    "%s · 未找到：%s": "{0} · not found: {1}",
    "表情：导出 %d 个 morph target（%s存储）": "Expressions: {0} morph targets ({1})",
    "稀疏": "sparse",
    "密集": "dense",
    "表情：%d 个包含材质变化（转为材质颜色绑定）": "Expressions: {0} change materials (written as material colour binds)",
    "表情：%d 个 UV 表情转为贴图偏移": "Expressions: {0} UV morphs written as texture offsets",
    "表情：%d 处 UV 表情不是整个材质统一平移 / 缩放，VRM 无法表示，已跳过":
        "Expressions: {0} UV morphs don't move or scale a whole material evenly, which VRM can't express; skipped",
    "表情：补充 hideHighlight（%s）": "Expressions: added hideHighlight ({0})",
    "表情：补充 hideHighlight（隐藏材质 %s）": "Expressions: added hideHighlight (hides the materials {0})",
    "表情：未找到眼睛高光材质或关闭高光的表情，未补充 hideHighlight":
        "Expressions: no eye highlight material or highlight-off morph found, hideHighlight not added",
    "表情：补充 tongue（%s）": "Expressions: added tongue ({0})",
    # ---- 材质 / 贴图
    "材质透明模式：不透明 %d · 裁剪（MASK）%d · 半透明（BLEND）%d":
        "Material transparency: {0} opaque · {1} cutout (MASK) · {2} semi-transparent (BLEND)",
    "材质：%d 个材质的贴图有透明部分，但模型只用到不透明的区域，按不透明处理":
        "Materials: {0} materials' textures have transparent parts the model doesn't use; treated as opaque",
    "材质：%d 个加算球面贴图转为 MToon 的 matcap": "Materials: {0} additive sphere maps became MToon matcaps",
    "材质：%d 个材质的乘算球面贴图（.sph）在 MToon 中没有对应项，已忽略":
        "Materials: {0} multiplicative sphere maps (.sph) have no MToon equivalent and were left out",
    "贴图：未安装 Pillow，缩小贴图和 JPEG 存储未生效": "Textures: Pillow isn't installed, so resizing and JPEG storage were skipped",
    "贴图：%d 张瘦身（%s%s）%.1f MB → %.1f MB": "Textures: {0} slimmed ({1}{2}) {3} MB → {4} MB",
    "最大边长 %d": "max size {0}",
    "不透明转 JPEG": "opaque ones as JPEG",
    "、不透明转 JPEG": ", opaque ones as JPEG",
    # ---- FBX → PMX
    "FBX 版本 %d · 对象 %d 个": "FBX version {0} · {1} objects",
    "模型 %d 个（其中骨骼 %d）· 几何体 %d · 材质 %d": "{0} models ({1} bones) · {2} geometries · {3} materials",
    "顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d · 表情 %d · 缩放 %.4f":
        "{0} vertices · {1} triangles · {2} bones · {3} materials · {4} morphs · scale {5}",
    "大小 %.2f MB（顶点索引 %d 字节）": "Size {0} MB ({1}-byte vertex indices)",
    "朝向：%s%s": "Facing: {0}{1}",
    "，旋转 180°": ", turned 180°",
    "未启用自动判定，按设置%s": "auto-detection off, {0} as set",
    "旋转 180°": "turned 180°",
    "不旋转": "not turned",
    "未找到脚尖骨骼": "no toe bones found",
    "脚尖骨骼（%s）朝向 X 轴，旋转 180° 无法修正": "the toe bones ({0}) point along X, turning 180° can't fix that",
    "按脚尖骨骼（%s）判定正面朝 %s": "the toe bones ({0}) show the front faces {1}",
    "脸部网格过少": "too few face meshes",
    "脸部网格前后偏移不明显": "the face meshes don't sit clearly to the front or back",
    "按脸部网格的位置判定正面朝 %s": "the face meshes show the front faces {0}",
    "%s，无法自动判定，按设置%s": "{0}, can't decide automatically, {1} as set",
    "绕序：%s": "Winding: {0}",
    "与顶点法线相反，已反转": "opposite to the vertex normals, flipped",
    "与顶点法线一致": "matches the vertex normals",
    "跳过无材质的网格 %d 个：%s": "Skipped {0} meshes without materials: {1}",
    "%s（%d 个三角面）": "{0} ({1} triangles)",
    "跳过无材质的网格 %s（%d 个三角面，通常是特效面片）": "Skipped the mesh {0} without a material ({1} triangles, usually an effect plane)",
    "贴图：已复制 %d / %d 个文件到 textures 文件夹": "Textures: copied {0} / {1} files to the textures folder",
    "贴图：%d 个引用的文件未找到（可稍后放入 textures 文件夹）":
        "Textures: {0} referenced files not found (they can be put into the textures folder later)",
    "贴图：已去除 %d 张贴图的透明通道": "Textures: removed the alpha channel of {0} textures",
    "贴图透明通道：去除 %d 张，保留 %d 张，%d 张本身不透明": "Texture alpha: removed in {0}, kept in {1}, {2} have none",
    "贴图：%d 张去除透明通道失败，已使用原图": "Textures: removing the alpha channel failed for {0}, the originals were used",
    "替换网格：显示 %s，%s 默认隐藏并做成切换表情": "Alternative meshes: showing {0}; {1} hidden by default, with morphs to switch",
    "居中：偏移 (%.2f, %.2f, %.2f)": "Centred: offset ({0}, {1}, {2})",
    "表情：由 %d 个 BlendShape 生成 %d 个顶点表情": "Morphs: {1} vertex morphs from {0} BlendShapes",
    "表情：共 %d 个，按上限只导出前 %d 个": "Morphs: {0} in total, only the first {1} exported (limit)",
    # ---- PSK / XPS / uemodel → PMX，PMX → FBX
    "提示：同一目录下有同名的 %s。PmxEditor 打开此 PMX 时会尝试将其作为「アンカーデータ」（锚点 / 权重数据）加载，并提示「アンカーデータの読み込みに失敗しました。」，点击确定即可，不影响模型。如需避免，可移动或重命名 PMX，或在 PmxEditor 中关闭[ファイル]→[アンカーデータの自動読み込み／保存]。":
        "Note: there is a {0} with the same name in the folder. PmxEditor will try to load it as anchor data when "
        "opening this PMX and report that it failed; click OK, the model is fine. To avoid it, move or rename the "
        "PMX, or turn off automatic anchor data loading in PmxEditor's File menu.",
    "读取 %s：顶点 %d · 楔 %d · 三角面 %d · 材质 %d · 骨骼 %d · 权重 %d":
        "Read {0}: {1} vertices · {2} wedges · {3} triangles · {4} materials · {5} bones · {6} weights",
    "表情 %d 个 · 附加 UV %d 套 · %s": "{0} morphs · {1} extra UV sets · {2}",
    "含法线": "with normals",
    "不含法线（将自动计算）": "no normals (they will be computed)",
    "骨骼：%d 根%s": "Bones: {0}{1}",
    "（其中 %d 根改为 MMD 标准日文名，原英文名保留在英文名中）":
        " ({0} renamed to the standard MMD Japanese names, the original names kept as English names)",
    "材质：%d 个 · 关联到贴图 %d 个%s": "Materials: {0} · {1} with textures{2}",
    "（源目录中没有同名贴图，导出为无贴图模型）": " (no matching textures in the source folder, exported without textures)",
    "已写出 %s（%.2f MB）· 顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d · 表情 %d":
        "Wrote {0} ({1} MB) · {2} vertices · {3} triangles · {4} bones · {5} materials · {6} morphs",
    "已写出 %s（%.2f MB）· 顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d":
        "Wrote {0} ({1} MB) · {2} vertices · {3} triangles · {4} bones · {5} materials",
    "网格：%d 个楔的点索引越界，已改用第 0 个点": "Mesh: {0} wedges pointed past the vertex list, now use vertex 0",
    "缩放：源高 %.2f 单位 × %.6f → %.2f（MMD 标准身高 20）": "Scale: source height {0} units × {1} → {2} (MMD standard height 20)",
    "缩放：源高 %.3f 单位 × %.6f → %.2f（MMD 标准身高 20）": "Scale: source height {0} units × {1} → {2} (MMD standard height 20)",
    "缩放：保持源尺寸（scale=1.0），源高 %.2f 单位": "Scale: kept as is (scale=1.0), source height {0} units",
    "缩放：保持源尺寸（scale=1.0），源高 %.3f 单位": "Scale: kept as is (scale=1.0), source height {0} units",
    "缩放：源高 %.2f 单位 × %.6f → %.2f": "Scale: source height {0} units × {1} → {2}",
    "缩放：源高 %.3f 单位 × %.6f → %.2f": "Scale: source height {0} units × {1} → {2}",
    "缩放：源高 %.2f cm × %.6f → %.2f": "Scale: source height {0} cm × {1} → {2}",
    "居中：偏移 (%.3f, %.3f, %.3f)": "Centred: offset ({0}, {1}, {2})",
    "居中：按参考模型包围盒（偏移 (%.3f, %.3f, %.3f)）": "Centred on the reference model's bounding box (offset ({0}, {1}, {2}))",
    "权重：%d 个顶点没有权重，已绑定到第 0 根骨骼": "Weights: {0} vertices had no weights, bound to bone 0",
    "权重：已丢弃 %d 条第 5 根及以后的骨骼权重（PMX 每个顶点最多 4 根）":
        "Weights: dropped {0} weights beyond the 4th bone per vertex (PMX allows 4)",
    "提示：%d 条第 5 及以后的骨骼权重被丢弃（PMX 最多 4 根）": "Note: dropped {0} weights beyond the 4th bone (PMX allows 4)",
    "表情：%d 个（顶点表情）": "Morphs: {0} (vertex morphs)",
    "足 IK：已补充 %d 根（足ＩＫ / つま先ＩＫ，父骨骼 センター）": "Leg IK: added {0} bones (足ＩＫ / つま先ＩＫ under センター)",
    "足 IK：源骨架中没有完整的腿部骨链，未补充 IK 骨": "Leg IK: the source skeleton has no complete leg chains, no IK bones added",
    "绕序自动判定：与法线同向 %d 面 / 反向 %d 面 → %s": "Winding: {0} faces agree with the normals / {1} oppose → {2}",
    "绕序：缺少法线数据，按坐标轴反射统一反转（保持正面朝外）":
        "Winding: no normals, flipped everywhere for the axis mirroring (front faces stay outside)",
    "绕序：法线数据不完整，按坐标轴反射统一反转（保持正面朝外）":
        "Winding: incomplete normals, flipped everywhere for the axis mirroring (front faces stay outside)",
    "绕序：%d 个顶点没有法线，判定时使用平均法线": "Winding: {0} vertices had no normals, the average normal was used",
    "已写出 %s（ASCII FBX 7.4 · %.2f MB）": "Wrote {0} (ASCII FBX 7.4 · {1} MB)",
    "FBX 导出失败：%s": "FBX export failed: {0}",
    "读取 %s：骨骼 %d 根 · 网格 %d 个 · 顶点 %d · 三角面 %d": "Read {0}: {1} bones · {2} meshes · {3} vertices · {4} triangles",
    "骨骼：%d 根%s%s": "Bones: {0}{1}{2}",
    "；%d 根 unused 占位骨单独放进 unused 表示枠": "; {0} unused placeholder bones put into their own unused display frame",
    "%d 根 unused 占位骨单独放进 unused 表示枠": "{0} unused placeholder bones put into their own unused display frame",
    "贴图：源目录中未找到 %s": "Textures: {0} not found in the source folder",
    "对齐：匹配 %d 根骨骼，其中 %d 根位置一致（残差 ≤ %.4f cm）": "Alignment: matched {0} bones, {1} in the same place (residual ≤ {2} cm)",
    "对齐：平移 (%.4f, %.4f, %.4f) cm": "Alignment: moved by ({0}, {1}, {2}) cm",
    "UEFormat v%d · %s · 骨骼 %d · LOD %d": "UEFormat v{0} · {1} · {2} bones · {3} LODs",
    "使用 LOD[%d] %s：顶点 %d · 三角面 %d · 材质 %d · 表情 %d · UV %d":
        "Using LOD[{0}] {1}: {2} vertices · {3} triangles · {4} materials · {5} morphs · {6} UV sets",
    "骨骼：%d 根（原始 %d 根）": "Bones: {0} ({1} originally)",
    "材质：%d 个 · 关联到贴图 %d 个 · 拷贝贴图 %d 张%s": "Materials: {0} · {1} with textures · {2} textures copied{3}",
    "（注意：%d 个材质的面区间不连续）": " (note: {0} materials have non-contiguous face ranges)",
    "对齐：部件的 %d 根骨骼中没有与参考模型同名或相近的骨骼，已跳过对齐":
        "Alignment: none of the part's {0} bones match the reference model by name, skipped",
    "对齐：位置一致的骨骼只有 %d 根（共匹配 %d 根 · 最大残差 %.4f cm），结果不可靠，已跳过":
        "Alignment: only {0} bones in the same place ({1} matched · max residual {2} cm), unreliable, skipped",
    "对齐：参考模型的骨架跨度 %.1f cm，小于本文件的 %.1f cm，参考模型本身可能是部件（参考与部件可能选反了），已跳过对齐":
        "Alignment: the reference skeleton spans {0} cm, less than this file's {1} cm; the reference may itself be "
        "a part (reference and part swapped?), skipped",
    "提示：该文件有 %d 个 LOD，只导出第 %d 个": "Note: the file has {0} LODs, only LOD {1} exported",
    "对齐后：源高 %.2f cm → %.2f cm（缩放不变）": "After alignment: source height {0} cm → {1} cm (scale unchanged)",
    "对齐：已按 %s 对齐（源坐标整体平移）": "Alignment: aligned to {0} (source coordinates moved as a whole)",
    "对齐失败（匹配 %d / %d），按模型自身原点导出": "Alignment failed ({0} / {1} matched), exported at the model's own origin",
    "对齐失败：%s（按模型自身原点导出）": "Alignment failed: {0} (exported at the model's own origin)",
    "FBX：%d 顶点 · %d 三角面 · %d 骨骼 · %d 材质（ASCII 7.4，%s-up，前方 %s）%s":
        "FBX: {0} vertices · {1} triangles · {2} bones · {3} materials (ASCII 7.4, {4}-up, front {5}){6}",
    "· %d 个顶点表情写入 BlendShape": " · {0} vertex morphs written as BlendShapes",
    "FBX：%d 个 Cluster · %d 个表情 Shape · 引用贴图 %d 张 · %.2f MB":
        "FBX: {0} clusters · {1} morph shapes · {2} textures referenced · {3} MB",
    "PSK：顶点 %d · 楔 %d · 面 %d · 骨骼 %d · 材质 %d · 权重 %d%s":
        "PSK: {0} vertices · {1} wedges · {2} faces · {3} bones · {4} materials · {5} weights{6}",
    " · 表情 %d": " · {0} morphs",
    "提示：%d 个楔的点索引越界，已夹回合法范围": "Note: {0} wedges pointed past the vertex list, clamped",
    "提示：顶点数 %d 超过 16 位索引上限，已改用 FACE3200": "Note: {0} vertices exceed 16-bit indices, FACE3200 used",
    "提示：%d 个面的楔索引越界（文件可能显示为破面）": "Note: {0} faces point past the wedge list (the file may show holes)",
    "XPS 格式：旧版 XNALara 二进制格式（无 Generic Item 2 头）": "XPS format: old XNALara binary (no Generic Item 2 header)",
    "XPS 格式：Generic Item 2 · 版本 %d.%d · 作者 %s": "XPS format: Generic Item 2 · version {0}.{1} · author {2}",
    "XPS 格式：旧版格式含切线数据（转换时忽略）": "XPS format: old format with tangents (ignored)",
    "网格 %d 有 %d 个三角面索引越界，已跳过": "Mesh {0}: {1} triangles point past the vertex list, skipped",
}
# 转换失败时日志里的整段 traceback、程序本身的英文消息：不翻译

_PH = re.compile(r"%(?:\([^)]*\))?[-+ #0]*\d*(?:\.\d+)?[dsfrgx]|%%")


def _compile(zh):
    out, pos = [], 0
    for m in _PH.finditer(zh):
        out.append(re.escape(zh[pos:m.start()]))
        ph = m.group()
        if ph == "%%":
            out.append("%")
        elif ph[-1] == "d":
            out.append(r"(-?\d+)")
        elif ph[-1] in "fg":
            out.append(r"(-?[\d.]+(?:e[-+]?\d+)?|nan|inf)")
        else:
            out.append(r"(.*?)")
        pos = m.end()
    out.append(re.escape(zh[pos:]))
    return re.compile("".join(out), re.S)


# 固定文字多的先试：「已写出 %s」不能抢在「已写出 %s（%.2f MB）· …」前面
_RULES = sorted(((_compile(zh.strip()), en) for zh, en in TEMPLATES.items() if _PH.search(zh)),
                key=lambda r: -len(_PH.sub("", r[0].pattern)))
_FIXED = {zh.strip(): en for zh, en in TEMPLATES.items() if not _PH.search(zh)}
_ERROR = re.compile(r"([A-Za-z_][\w.]*(?:Error|Exception|Warning)): (.*)", re.S)
_CJK = re.compile("[　-〿぀-ヿ一-鿿＀-￯]")


def _arg(s):
    """参数：程序生成的中文翻成英文，模型里的名字原样。列表（、/ ；）逐项处理：
    「身体：13 个表情、头绳：1 个表情」不能整个当成一句「%s：%d 个表情」。"""
    if not s or not _CJK.search(s):
        return s
    if s in _FIXED:
        return _FIXED[s]
    for sep, out in (("；", "; "), ("、", ", ")):
        if sep in s:
            # 整句对得上、拆开的各项一个都对不上（「按脚尖骨骼（Toe.L、Toe.R）判定…」）就按整句
            parts = s.split(sep)
            whole = _translate(s)
            if whole is not None and not any(_CJK.search(p) and _translate(p) is not None for p in parts):
                return whole
            return out.join(_arg(p) for p in parts)
    t = _translate(s)
    if t is not None:
        return t
    m = _ERROR.fullmatch(s)
    if m:
        return "%s: %s" % (m.group(1), _arg(m.group(2)))
    return s


def _translate(s):
    if s in _FIXED:
        return _FIXED[s]
    for rx, en in _RULES:
        m = rx.fullmatch(s)
        if m:
            return en.format(*(_arg(g) for g in m.groups()))
    return None


def to_en(msg):
    """一条日志的英文（开头的「[模型名] 」保留）；对照表里没有的返回 None。"""
    if not msg or not _CJK.search(msg):
        return None
    m = re.match(r"((?:\[[^\]\n]*\] )*)(.*)", msg, re.S)
    prefix, body = m.group(1), m.group(2).strip()
    if "\n" in body:                                 # traceback 之类
        return None
    t = _translate(body)
    if t is None:
        e = _ERROR.fullmatch(body)
        if e:
            inner = _translate(e.group(2))
            t = inner and "%s: %s" % (e.group(1), inner)
    return prefix + t if t is not None else None
