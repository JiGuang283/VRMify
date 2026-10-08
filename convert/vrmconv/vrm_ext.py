# -*- coding: utf-8 -*-
"""VRM 扩展本体：VRM 1.0 的 VRMC_vrm、VRM 0.x 的 VRM（meta / humanoid / firstPerson / lookAt / 表情）。"""


def _look_deg(look):
    """视线最大转角（morphbake.eye_look_range 的结果；没有时左右上下都 10°）。"""
    return look or {"lookLeft": 10.0, "lookRight": 10.0, "lookDown": 10.0, "lookUp": 10.0}


def _degmap(xr, yr):
    return {"curve": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.0],
            "xRange": xr, "yRange": yr}


def _pick(meta, key, allowed, default):
    v = meta.get(key)
    return v if v in allowed else default


def build_vrm1(meta, title, human, mesh_node, expr, look=None):
    """VRMC_vrm。meta：用户给的作者 / 许可等；expr：expressions.build_expressions 的结果。

    默认值和以前一致：谁都可以「扮演」（avatarPermission=everyone，不然有的应用拒绝加载），
    其余取保守值（仅个人非商用、禁止再分发和修改、要求署名）——转换的大多是别人的模型，
    放宽要由用户在界面里明确选。
    """
    author = meta.get("author") or ""
    vrm_ext = {
        "specVersion": "1.0",
        "humanoid": {"humanBones": {k: {"node": v}
                                    for k, v in human.items()}},
        "meta": {
            "name": title,
            "version": meta.get("version") or "1",
            "authors": [author] if author else ["unknown"],
            "copyrightInformation": meta.get("copyright") or "",
            "contactInformation": meta.get("contact") or "",
            "thirdPartyLicenses": "",
            # thumbnailImage 规范要求是图片索引（整数）；没有缩略图就不写，
            # 写 null 过不了 VRM 1.0 schema 校验。缩略图由 vrm_edit 写入。
            "licenseUrl": meta.get("license_url") or "https://vrm.dev/licenses/1.0/",
            "avatarPermission": _pick(meta, "avatar_permission",
                                      ("onlyAuthor", "onlySeparatelyLicensedPerson", "everyone"),
                                      "everyone"),
            "allowExcessivelyViolentUsage": False,
            "allowExcessivelySexualUsage": False,
            "commercialUsage": _pick(meta, "commercial_usage",
                                     ("personalNonProfit", "personalProfit", "corporation"),
                                     "personalNonProfit"),
            "allowPoliticalOrReligiousUsage": False,
            "allowAntisocialOrHateUsage": False,
            "creditNotation": _pick(meta, "credit_notation", ("required", "unnecessary"), "required"),
            "allowRedistribution": bool(meta.get("allow_redistribution", False)),
            "modification": _pick(meta, "modification",
                                  ("prohibited", "allowModification", "allowModificationRedistribution"),
                                  "prohibited"),
        },
        "firstPerson": {"meshAnnotations": [{"node": mesh_node,
                                             "type": "auto"}]},
    }
    if expr["preset"] or expr["custom"]:
        vrm_ext["expressions"] = {}
        if expr["preset"]:
            vrm_ext["expressions"]["preset"] = expr["preset"]
        if expr["custom"]:
            vrm_ext["expressions"]["custom"] = expr["custom"]
    if "leftEye" in human or "rightEye" in human:
        d = _look_deg(look)
        vrm_ext["lookAt"] = {
            "type": "bone",
            "offsetFromHeadBone": [0.0, 0.06, 0.0],
            "rangeMapHorizontalInner": {"inputMaxValue": 90.0, "outputScale": d["lookLeft"]},
            "rangeMapHorizontalOuter": {"inputMaxValue": 90.0, "outputScale": d["lookLeft"]},
            "rangeMapVerticalDown": {"inputMaxValue": 90.0, "outputScale": d["lookDown"]},
            "rangeMapVerticalUp": {"inputMaxValue": 90.0, "outputScale": d["lookUp"]},
        }
    return vrm_ext


def build_vrm0(meta, title, human, expr, material_props, look=None):
    """VRM 0.x 的 VRM 扩展。material_props：materials.MaterialBuilder.props_0x。"""
    return {
        "exporterVersion": "pmx2vrm.py",
        "specVersion": "0.0",
        "meta": {
            "title": title,
            "version": meta.get("version") or "1",
            "author": meta.get("author") or "",
            "contactInformation": meta.get("contact") or "",
            "reference": "",
            "allowedUserName": {"everyone": "Everyone",
                                "onlySeparatelyLicensedPerson": "ExplicitlyLicensedPerson",
                                "onlyAuthor": "OnlyAuthor"}.get(
                                    meta.get("avatar_permission"), "Everyone"),
            "violentUssageName": "Disallow",
            "sexualUssageName": "Disallow",
            "commercialUssageName": ("Allow" if meta.get("commercial_usage") in
                                     ("personalProfit", "corporation") else "Disallow"),
            "otherPermissionUrl": "",
            "licenseName": "Other",
            "otherLicenseUrl": meta.get("license_url") or "",
        },
        "humanoid": {
            "humanBones": [{"bone": k, "node": v, "useDefaultValues": True}
                           for k, v in human.items()],
            "armStretch": 0.05, "legStretch": 0.05,
            "upperArmTwist": 0.5, "lowerArmTwist": 0.5,
            "upperLegTwist": 0.5, "lowerLegTwist": 0.5,
            "feetSpacing": 0.0, "hasTranslationDoF": False,
        },
        "firstPerson": {
            "firstPersonBone": human.get("head", 0),
            "firstPersonBoneOffset": {"x": 0.0, "y": 0.06, "z": 0.0},
            "meshAnnotations": [{"firstPersonFlag": "Auto", "mesh": 0}],
            "lookAtTypeName": ("Bone" if ("leftEye" in human or
                                          "rightEye" in human)
                               else "BlendShape"),
            "lookAtHorizontalInner": _degmap(90, _look_deg(look)["lookLeft"]),
            "lookAtHorizontalOuter": _degmap(90, _look_deg(look)["lookLeft"]),
            "lookAtVerticalDown": _degmap(90, _look_deg(look)["lookDown"]),
            "lookAtVerticalUp": _degmap(90, _look_deg(look)["lookUp"]),
        },
        "blendShapeMaster": {"blendShapeGroups": expr["blend_groups"]},
        "secondaryAnimation": {"boneGroups": []},
        "materialProperties": material_props,
    }
