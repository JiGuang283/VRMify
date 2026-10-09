# -*- coding: utf-8 -*-
"""pmx2vrm.py - 把 PMX 模型转成 VRM（glTF 2.0 / GLB 容器），纯 Python 标准库。

不需要 Blender、不需要 mmd_tools、不需要 Unity。

坐标约定（数值均指文件里存的原始数字）
--------
PMX（MMD）   正面 -Z，角色左手侧 +X，Y 向上（DirectX 左手系，逆时针缠绕）
VRM 0.x      正面 -Z，角色左手侧 -X，Y 向上（glTF 右手系、逆时针为正面）
VRM 1.0      正面 +Z，角色左手侧 +X，Y 向上

所以：
  * PMX（DirectX 左手系）与 glTF（右手系）手性相反：同一组数字直接
    解释是镜像人形，必须反射一个轴——转 0.x 取反 X；转 1.0 取反 Z。
  * 两种情况三角绕序都要反转（反射把「外面看逆时针」颠倒了）。
  * 尺寸：MMD 模型约 20 单位高，VRM 用米，默认按 1.6 m 归一。

用法：
    python pmx2vrm.py model.pmx -o model.vrm
    python pmx2vrm.py model.pmx --spec 0x --title "名字" --author "作者"
"""
import argparse
import os
import sys

# 让 `python convert/pmx2vrm.py ...` 也能直接跑：脚本自己所在目录（convert/）和同级的
# formats/ 都要进 sys.path。以前这里只加了 convert/，于是 `import pmxio` 之类会
# ModuleNotFoundError —— 而 README 里给的命令行用法就是这么调的。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "formats")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import pmxio  # noqa: E402
import vrmio  # noqa: E402
from vrmconv import autophys, expressions, mesh, morphbake, skeleton, textures, vrm_ext  # noqa: E402
from vrmconv.materials import MaterialBuilder  # noqa: E402
from vrmconv.springbone import _build_springs  # noqa: E402


# ---------------------------------------------------------------- 主流程 --
def convert(pmx_path, vrm_path, spec="1.0", scale_mode="auto",
            rotate="auto", reverse_winding="auto", meta=None,
            max_morphs=None, log=None, force_double_sided=False,
            tpose=True, outline=False, sparse_morphs=True,
            spring=True, spring_skirt=True, spring_stiffness=1.0,
            spring_gravity=0.0, spring_gravity_skirt=0.1, spring_center=True,
            spring_opts=None, tex_max=0, tex_jpeg=False, auto_spring=True, gaze=True,
            combo_expr=True, model=None, raw_weights=None, tex_alpha=None, threads=4):
    """PMX → VRM，返回统计 dict。log(msg, tag=None) 用于输出进度。

    各阶段在 vrmconv/ 里：skeleton（骨骼、T-pose、节点）→ springbone（物理）→
    mesh + materials（网格、MToon）→ expressions（表情）→ vrm_ext（VRM 扩展）。

    auto_spring：模型完全没有物理刚体时，按骨骼名 / 结构自动补（vrmconv/autophys）。
    tex_max / tex_jpeg：贴图最大边长（0 = 原样）/ 不透明贴图改存 JPEG（需要 Pillow，见 vrmconv/textures）。
    gaze：有左右眼骨时补 lookUp / lookDown / lookLeft / lookRight 表情（转眼骨烘焙成顶点偏移，
        见 vrmconv/morphbake；模型自己有对应表情时不补）。
    combo_expr：情绪预设（happy / angry / sad / relaxed / surprised）组合眉、眼、嘴的表情，
        像 VRoid 一样整张脸一起变（见 vrmconv/expressions.COMBO）；False 时每个预设只对应一个表情。
    meta：title / author / version / contact / copyright / license_url / avatar_permission /
        commercial_usage / credit_notation / allow_redistribution / modification（见 vrm_ext）。
    model / raw_weights / tex_alpha：调用方已经有的，省得再算一遍——读好的 PMX（pmxio.read_pmx，
        会被改动）、不带付与别名的顶点权重（mesh.vertex_weights(..., {})）、贴图透明统计
        {normcase 绝对路径: vrmio.png_alpha_stats 的结果}（FBX 转换判断透明时已经解过贴图）。
    threads：同时处理几张贴图（解码、瘦身）。

    reverse_winding:
        "auto"（默认）——用「几何面法线 vs 顶点法线」投票自动判定；
        True / False   ——手动强制反转 / 不反转。

    手性说明（2026-09-18 重写）：PMX（MMD / DirectX 左手系）数值上
    脸朝 -Z、左手在 +X；同一组数字按 glTF 右手系直接解释是镜像人形，
    必须做一次镜面反射（0.x 取反 x；1.0 取反 z）。反射会把
    「从外面看逆时针」颠倒成顺时针，所以 auto 绕序判定（用反射后的
    数据投票）正常会判「反转」。以前不做反射（或用 y180 纯旋转，
    旋转改不了手性）会让转出的模型左右翻转。
    """
    # 确保输出目录存在（CLI 直接调用时尤其容易漏建）
    _odir = os.path.dirname(os.path.abspath(vrm_path))
    try:
        os.makedirs(_odir, exist_ok=True)
    except OSError as e:
        raise OSError("无法创建输出目录 %s：%s" % (_odir, e))

    def _log(msg, tag=None):
        if log is not None:
            try:
                log(msg, tag)
            except TypeError:
                log(msg)

    if model is None:
        model = pmxio.read_pmx(pmx_path)
    skeleton.fix_facing(model, _log)
    base_dir = os.path.dirname(os.path.abspath(pmx_path))
    verts = model["vertices"]
    bones = model["bones"]
    faces = model["faces"]
    materials = model["materials"]
    is1 = str(spec).startswith("1")

    # ---- 缩放
    if scale_mode in ("auto", "mmd", None, ""):
        ys = [v["pos"][1] for v in verts] or [0.0]
        height = max(ys) - min(ys)
        scale = (1.6 / height) if height > 1e-6 else 0.08
    else:
        scale = float(scale_mode)

    # ---- 手性修正（2026-09-18 重写，别改回「绕 Y 旋转 180°」）
    # PMX（左手系）数值：脸 -Z、左手 +X（实测小食Taberu：
    # 両目 Z=-0.75，左腕 X=+1.34 / 右腕 X=-1.34）。
    # glTF（右手系）里合法人形：脸 -Z ⇒ 左手 -X；脸 +Z ⇒ 左手 +X。
    # 所以必须反射一个轴：0.x（脸 -Z）取反 x；1.0（脸 +Z）取反 z。
    # y180 是纯旋转，改不了手性 → 转出的 VRM 左右翻转（实测 Blender 可见）。
    # rotate="none" 仅用于调试（原样输出，左右是反的）。
    if rotate in ("auto", "y180", None, ""):      # y180 为旧值，视同 auto
        mx, mz = (1.0, -1.0) if is1 else (-1.0, 1.0)
    else:                                          # "none"
        mx, mz = 1.0, 1.0

    def xf(p):
        x, y, z = p
        return (x * mx * scale, y * scale, z * mz * scale)

    def xf_dir(p):
        x, y, z = p
        return (x * mx, y, z * mz)

    def unit(left_amount, up_amount, front_amount):
        if is1:                       # VRM 1.0：左 +X、前 +Z
            return (left_amount, up_amount, front_amount)
        return (-left_amount, up_amount, -front_amount)

    _log("PMX：顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d · 表情 %d"
         % (len(verts), len(faces) // 3, len(bones), len(materials),
            len(model["morphs"])))

    # ---- 骨骼预处理（都在 PMX 坐标里做，最后统一过 xf）：付与骨合并、humanoid、T-pose
    bs = skeleton.prepare_bones(bones, is1, _log)
    # 骨骼表情烘焙用不带别名的权重，网格用带别名的：先算前者，后者只重算挂在别名骨上的顶点
    if raw_weights is None:
        raw_weights = mesh.vertex_weights(verts, len(bones), {})
    vweights = mesh.vertex_weights(verts, len(bones), bs.alias, base=raw_weights)
    glob, bpos, vpos, vnrm, vmat = skeleton.apply_tpose(verts, bones, bs, vweights, tpose, _log)

    # ---- 缠绕顺序自动判定：必须用「镜像后」的数据投票。
    # 镜像反射会把「从外面看逆时针」颠倒成顺时针，所以正常情况下
    # auto 在这里会判定为「需要反转」——这不是回到老 bug，
    # 老 bug 是「没有反射时硬编码反转」。
    if reverse_winding in ("auto", None, ""):
        flip, ag, dis = pmxio.detect_winding(
            [xf(p) for p in vpos], [xf_dir(n) for n in vnrm], faces)
        reverse_winding = bool(flip)
        _log("绕序自动判定（手性修正后）：与法线同向 %d 面 / 反向 %d 面 → %s"
             % (ag, dis, "需要反转" if flip else "保持不变"))
    else:
        reverse_winding = bool(reverse_winding)

    _log("缩放 %.5f（MMD 单位 → 米）· 手性修正 %s · 绕序%s"
         % (scale,
            ("z 取反" if is1 else "x 取反") if (mx, mz) != (1.0, 1.0) else "无",
            "反转" if reverse_winding else "保持不变"))

    # ---- 骨骼 → glTF 节点；humanoid 缺的必需骨位补占位
    tree = skeleton.NodeTree(bones, bs, bpos, xf)
    human = tree.humanoid(bs.human_b, bs.required, unit, _log)
    tree.lower_hips(human, _log)

    # ---- PMX 物理 → SpringBone（只写 VRM 1.0 的 VRMC_springBone）
    spring_ext = None
    if spring and is1 and auto_spring and autophys.needed(model):
        autophys.synthesize(model, bs.parents, bs.alias, bs.human_b, _log)
    if spring and is1:
        spring_ext = _build_springs(
            model, bs.parents, bs.alias, bs.human_b, glob, bpos, scale, xf,
            tree.bone_node, tree.node_pos, tree.add_node,
            include_lower=spring_skirt, stiffness_mul=spring_stiffness,
            gravity=spring_gravity, gravity_lower=spring_gravity_skirt,
            set_parent=tree.set_parent, cloth=(vnrm, vweights, xf_dir), log=_log,
            **(spring_opts or {}))
    elif spring and model.get("rigid_bodies"):
        _log("提示：VRM 0.x 暂不转换 SpringBone（物理），头发/裙摆不会摆动", "warn")
    nodes, top_nodes = tree.gltf_nodes()

    # ---- 视线转角：按眼球几何算（lookAt 和补的视线表情都用它）
    look, note = None, None
    if "leftEye" in bs.human_b or "rightEye" in bs.human_b:
        look, note = morphbake.eye_look_range(model, vweights, bs.parents,
                                              [bs.human_b.get("leftEye"), bs.human_b.get("rightEye")])
    if look:
        _log("视线：左右 %.1f° · 下 %.1f° · 上 %.1f°（%s）"
             % (look["lookLeft"], look["lookDown"], look["lookUp"], note))
    elif note:
        _log("视线：" + note, "warn")

    # ---- 逐材质构建网格（材质表情会影响材质的 alphaMode，表情要先整理）
    gb = vrmio.GLBBuilder()
    gb.samplers.append(vrmio.default_sampler())
    gaze_extra = _gaze(model, bs.human_b, is1, gaze, _log, look)
    flat = expressions._flatten_morphs(model["morphs"], model, gaze_extra, weights=raw_weights)
    gaze_split = morphbake.split_gaze(flat, {n for n, _ in gaze_extra})
    # max_morphs: None = 全部；0 = 不导出；n = 只取前 n 个
    if max_morphs is not None and max_morphs >= 0:
        flat = flat[:int(max_morphs)]
    mats = MaterialBuilder(model, base_dir, gb, is1, flat, outline=outline,
                           force_double_sided=force_double_sided,
                           tex_opt=textures.Optimizer(tex_max, tex_jpeg), known_alpha=tex_alpha)
    mats.prefetch(threads)
    prims, mat_gidx = mesh.build_primitives(model, xf, xf_dir, vpos, vnrm, vweights,
                                            reverse_winding, mats.make)
    _log("网格：%d 个 primitive（材质）" % len(prims))
    mats.log_summary(_log)
    if not prims:
        raise RuntimeError("这个 PMX 没有可用的面数据")
    mesh.write_accessors(gb, prims)

    # ---- 表情 → morph target + VRM expression
    expr = expressions.build_expressions(flat, prims, mats, mat_gidx, gb, xf, vmat,
                                         is1, sparse_morphs, _log, combo=combo_expr,
                                         hidden={d for _, d, _ in gaze_split.values()})
    target_names = expr["target_names"]
    _gaze_binds(expr, gaze_split, look, is1)

    # ---- 按表情分网格（每个网格只带动得到它的 morph target）
    groups = mesh.group_meshes(prims, len(target_names))
    gltf_meshes, where = mesh.build_meshes(gb, prims, groups, target_names,
                                           model["name"] or "body", sparse_morphs)
    if len(gltf_meshes) > 1:
        _log("网格：按表情分成 %d 个（%s）" % (len(gltf_meshes), " / ".join(
            "%s %d" % (m["name"], len(m.get("extras", {}).get("targetNames", ()))) for m in gltf_meshes)))

    inv = []
    for i in range(len(bones)):
        p = tree.node_pos(tree.bone_node(i))      # 节点位置（hips 下移过）
        inv.extend([1.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    -p[0], -p[1], -p[2], 1.0])
    if not inv:
        inv = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    ibm = gb.add_accessor(inv, vrmio.FLOAT, "MAT4", minmax=False)

    mesh_nodes = []
    taken = {n.get("name") for n in nodes}
    for k, m in enumerate(gltf_meshes):
        mesh_nodes.append(len(nodes))
        # 网格按材质起名，可能和骨骼重名（「髪」）：Unity 里按名字找物体的会找错，加后缀
        nm = expressions.uniq_name("mesh" if len(gltf_meshes) == 1 else m["name"], taken)
        taken.add(nm)
        nodes.append({"name": nm, "mesh": k, "skin": 0})
    root_index = len(nodes)
    nodes.append({"name": "root",
                  "children": list(top_nodes) + mesh_nodes})

    joints = [tree.bone_node(i) for i in range(len(bones))]
    if not joints:
        joints = [0]

    gltf = {
        "asset": {"version": "2.0", "generator": "VRMify"},
        "scene": 0,
        "scenes": [{"nodes": [root_index]}],
        "nodes": nodes,
        "meshes": gltf_meshes,
        "skins": [{"joints": joints, "inverseBindMatrices": ibm,
                   "skeleton": root_index}],
        "materials": mats.materials,
        "accessors": gb.accessors,
        "bufferViews": gb.bufferViews,
        "buffers": [{"byteLength": len(gb.bin)}],
    }
    if gb.images:
        gltf["images"] = gb.images
        gltf["textures"] = gb.textures
        gltf["samplers"] = gb.samplers

    # 绑定的 morph target 序号 → 各网格里的；1.0 的 node 指向网格节点，0.x 的 mesh 指向网格号
    mesh.remap_binds(expr, where, mesh_nodes, is1)

    # ---- VRM 扩展
    meta = meta or {}
    title = meta.get("title") or model["name"] or "PMX Model"
    if is1:
        gltf["extensionsUsed"] = ["VRMC_vrm", "VRMC_materials_mtoon"]
        gltf["extensions"] = {"VRMC_vrm": vrm_ext.build_vrm1(meta, title, human,
                                                             mesh_nodes, expr, look)}
        if spring_ext:
            if spring_center:
                # center = 根节点：在模型自身的空间里模拟。应用里人物整体移动（走路的
                # 根运动、平移）时，头发/裙摆不会因惯性被甩、往后拖压到腿上，只对身体本身
                # 的动作有反应。实测走路时头发摆幅 0.73→0.15、胸部配饰 0.28→0.05、
                # 裙摆穿进大腿的顶点 10→3。规范要求 center 是链首关节或其祖先，根节点满足
                for sp in spring_ext["springs"]:
                    sp["center"] = root_index
            gltf["extensionsUsed"].append("VRMC_springBone")
            gltf["extensions"]["VRMC_springBone"] = spring_ext
    else:
        gltf["extensionsUsed"] = ["VRM"]
        gltf["extensions"] = {"VRM": vrm_ext.build_vrm0(meta, title, human, expr,
                                                        mats.props_0x, look, len(gltf_meshes))}

    size = vrmio.write_glb(gltf, gb.bin, vrm_path)
    n_expr = (len(expr["preset"]) + len(expr["custom"])) if is1 else len(expr["blend_groups"])
    _log("已写出 %s（%.2f MB）· 节点 %d · morph target %d · 表情 %d"
         % (os.path.basename(vrm_path), size / 1048576.0, len(nodes),
            len(target_names), n_expr), "ok")
    return {"nodes": len(nodes), "primitives": len(prims), "meshes": len(gltf_meshes),
            "morphs": len(target_names), "bytes": size, "scale": scale,
            "vertices": sum(p["vcount"] for p in prims)}


# -------------------------------------------------------------------- cli --
def _gaze(model, human_b, is1, enabled, log, look=None):
    """要补的视线表情 → [(名字, 骨骼表情 offsets)]（交给 _flatten_morphs 烘焙）。"""
    if not enabled or "leftEye" not in human_b or "rightEye" not in human_b:
        return []
    have = set()
    for mo in model["morphs"]:
        p = expressions._table_preset(mo["name"], True) or expressions._preset_en(mo["name"], True)
        if p:
            have.add(p)
    # 左右（上下）都要补时按 GAZE_MAX 烘焙，之后拆成横向 + 深度两个场、权重给出实际角度（morphbake.split_gaze）；
    # 只补一边的按实际角度烘焙
    deg = dict(look or morphbake.GAZE_DEG)
    for a, b, axis in morphbake.GAZE_PAIRS:
        if a not in have and b not in have:
            deg[a] = deg[b] = morphbake.GAZE_MAX[axis]
    out = [(n, offs) for n, offs in morphbake.gaze_offsets(human_b["leftEye"], human_b["rightEye"], deg)
           if n not in have]
    if out:
        log("表情：补视线表情 %s（转眼骨烘焙；lookAt 仍用骨骼）" % " / ".join(n for n, _ in out))
    return out


def _gaze_binds(expr, split, look, is1):
    """拆过的视线表情 → 横向场 + 深度场两个绑定，权重按实际视线角度（vrm_edit 改视线角度时同样重算）。"""
    names = expr["target_names"]
    for p, (tan, depth, axis) in split.items():
        wt, wd = morphbake.gaze_weights((look or morphbake.GAZE_DEG)[p], axis)
        slots = [(len(names) - 1 - names[::-1].index(n), w) for n, w in ((tan, wt), (depth, wd))]
        if is1:
            obj = expr["preset"].get(p)
            if obj and (obj.get("extras") or {}).get("source") == p:
                obj["morphTargetBinds"] = [{"node": 0, "index": i, "weight": w} for i, w in slots]
        else:
            for gr in expr["blend_groups"]:
                if gr["name"] == p and gr["presetName"] == p.lower():
                    gr["binds"] = [{"mesh": 0, "index": i, "weight": round(w * 100, 4)} for i, w in slots]


def main(argv=None):
    ap = argparse.ArgumentParser(description="PMX → VRM（纯 Python）")
    ap.add_argument("pmx")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--spec", default="1.0", choices=["1.0", "0x"])
    ap.add_argument("--scale", default="auto",
                    help="auto（归一到 1.6 m）或具体倍率")
    ap.add_argument("--rotate", default="auto", choices=["auto", "none", "y180"])
    ap.add_argument("--flip-winding", action="store_true",
                    help="强制反转三角形缠绕顺序（默认自动判定）")
    ap.add_argument("--force-double-sided", action="store_true",
                    help="所有材质强制双面（模型仍有镂空时的兜底手段）")
    ap.add_argument("--keep-pose", action="store_true",
                    help="保留原始姿势（默认把 A-pose 手臂抬成 VRM 要求的 T-pose）")
    ap.add_argument("--no-spring", action="store_true",
                    help="不把 PMX 物理转成 SpringBone（默认转换，仅 VRM 1.0）")
    ap.add_argument("--no-spring-skirt", action="store_true",
                    help="跳过下半身（hips / 腿）下的物理链，通常是裙摆")
    ap.add_argument("--spring-stiffness", type=float, default=1.0,
                    help="SpringBone 硬度整体倍率（默认 1.0，越大越硬）")
    ap.add_argument("--spring-gravity", type=float, default=0.0,
                    help="SpringBone 重力（头发等，默认 0：保持 PMX 原造型；"
                         "斜着散开的长发加重力会被拉垂）")
    ap.add_argument("--spring-gravity-skirt", type=float, default=0.1,
                    help="下半身（裙摆）SpringBone 重力（默认 0.1，抬腿时裙片落回腿上）")
    ap.add_argument("--no-auto-spring", action="store_true",
                    help="模型没有物理刚体时不自动补（默认按骨骼名给头发、裙摆、袖子等加 SpringBone）")
    ap.add_argument("--no-combo-expr", action="store_true",
                    help="情绪预设不组合眉 / 眼 / 嘴（默认组合，像 VRoid；关掉时每个预设只对应一个 PMX 表情）")
    ap.add_argument("--no-gaze", action="store_true",
                    help="不补视线表情（默认有左右眼骨时补 lookUp / lookDown / lookLeft / lookRight）")
    ap.add_argument("--no-spring-center", action="store_true",
                    help="SpringBone 不设 center（默认以模型根节点为 center，人物整体移动时"
                         "头发/裙摆不会被甩）")
    ap.add_argument("--outline", action="store_true",
                    help="按 PMX 的轮廓线设置写出 MToon 描边（默认不描边）")
    ap.add_argument("--dense-morphs", action="store_true",
                    help="表情用密集存储（默认稀疏存储，文件小很多）")
    ap.add_argument("--max-morphs", type=int, default=None,
                    help="只导出前 N 个表情（0 = 不导出，默认全部）")
    ap.add_argument("--tex-max", type=int, default=0,
                    help="贴图最大边长（如 2048 / 1024，默认 0 = 原样；需要 Pillow）")
    ap.add_argument("--tex-jpeg", action="store_true",
                    help="不透明贴图改存 JPEG（文件小很多；需要 Pillow）")
    ap.add_argument("--title", default=None)
    ap.add_argument("--author", default=None)
    a = ap.parse_args(argv)

    out = a.out or (os.path.splitext(a.pmx)[0] + ".vrm")
    meta = {}
    if a.title:
        meta["title"] = a.title
    if a.author:
        meta["author"] = a.author
    st = convert(a.pmx, out, spec=("1.0" if a.spec == "1.0" else "0.x"),
                 scale_mode=a.scale, rotate=a.rotate,
                 reverse_winding=(True if a.flip_winding else "auto"),
                 meta=meta, max_morphs=a.max_morphs,
                 force_double_sided=a.force_double_sided,
                 tpose=not a.keep_pose, outline=a.outline,
                 spring=not a.no_spring, spring_skirt=not a.no_spring_skirt,
                 spring_stiffness=a.spring_stiffness,
                 spring_gravity=a.spring_gravity,
                 spring_gravity_skirt=a.spring_gravity_skirt,
                 spring_center=not a.no_spring_center, auto_spring=not a.no_auto_spring,
                 sparse_morphs=not a.dense_morphs,
                 tex_max=a.tex_max, tex_jpeg=a.tex_jpeg, gaze=not a.no_gaze, combo_expr=not a.no_combo_expr,
                 log=lambda msg, tag=None: print(msg))
    print("完成：%s（%.2f MB）" % (out, st["bytes"] / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
