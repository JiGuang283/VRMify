# -*- coding: utf-8 -*-
"""转换日志的英文版（convert/logtext.py）：源码里每条中文日志 / 报错都要有翻译，翻译要对。"""
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ("convert", "formats"):
    _d = os.path.join(ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)
import logtext  # noqa: E402

LOG_CALLS = {"log", "_log", "_l", "lg", "say", "warn", "_warn", "note", "_note"}
PRINTS = {"fbx2pmx.py"}          # 转换时 print() 被转成日志（pipeline._LogWriter）
SKIP = {"app.py"}                 # 启动器的控制台提示，不进日志
# 几段拼起来的日志：句式在 TEMPLATES 里拆成了几条（「表情预设：%s」+「%s · 未找到：%s」）
COMPOSITE = {"表情预设：%s%s"}


def _cjk(s):
    return bool(logtext._CJK.search(s))


def _template(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _template(node.left)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (_template(node.left) or "%s") + (_template(node.right) or "%s")
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "%s" for v in node.values)
    return None


def source_messages():
    """→ [(位置, 句式, [参数里的中文片段])]：转换器和界面服务器里的 log(...) 和 raise X("...")。"""
    out = []
    for d in ("convert", "formats", "ui"):
        for dp, _, fs in os.walk(os.path.join(ROOT, d)):
            if "vendor" in dp or "__pycache__" in dp:
                continue
            for fn in fs:
                if not fn.endswith(".py") or fn in SKIP:
                    continue
                path = os.path.join(dp, fn)
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                names = LOG_CALLS | ({"print"} if fn in PRINTS else set())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                        arg = node.exc.args[0]
                    elif isinstance(node, ast.Call) and node.args and \
                            (node.func.id if isinstance(node.func, ast.Name) else
                             getattr(node.func, "attr", None)) in names:
                        arg = node.args[0]
                    else:
                        continue
                    t = _template(arg)
                    if not t or not _cjk(t):
                        continue
                    frags = [c.value for c in ast.walk(arg) if isinstance(c, ast.Constant)
                             and isinstance(c.value, str) and _cjk(c.value) and c.value != t]
                    out.append(("%s:%d" % (os.path.relpath(path, ROOT), node.lineno), t.strip(), frags))
    return out


class LogTextTest(unittest.TestCase):
    def test_every_message_has_a_translation(self):
        known = {k.strip() for k in logtext.TEMPLATES}
        missing = []
        for where, t, frags in source_messages():
            if t not in known and t not in COMPOSITE:
                missing.append("%s  %s" % (where, t))
            for f in frags:
                # 拼接用的片段（「视线：」+ note）随整句检查；带占位符或单独成词的要在表里
                if f.strip() not in known and not any(f in k for k in known | COMPOSITE):
                    missing.append("%s  片段 %s" % (where, f))
        self.assertEqual(missing, [], "logtext.TEMPLATES 里缺这些句式：\n" + "\n".join(missing))

    def test_templates_are_consistent(self):
        """英文里引用的参数个数和中文占位符个数一致。"""
        import re
        for zh, en in logtext.TEMPLATES.items():
            n = len([m for m in logtext._PH.findall(zh) if m != "%%"])
            used = {int(i) for i in re.findall(r"\{(\d+)\}", en)}
            self.assertTrue(used <= set(range(n)), zh)
            self.assertEqual(len(used), n, zh)

    def test_translations(self):
        cases = {
            "[艾尔莎-人类形态] 网格：按表情分为 2 个（身体：13 个表情、脸：181 个表情）":
                "[艾尔莎-人类形态] Mesh: split into 2 by morphs (身体: 13 morphs, 脸: 181 morphs)",
            "绕序自动判定（手性修正后）：与法线同向 16 面 / 反向 3892 面 → 需要反转":
                "Winding (after the handedness fix): 16 faces agree with the normals / 3892 oppose → flip",
            "T-pose：左 上臂 37.0° · 前臂 0.0°": "T-pose: left upper arm 37.0° · forearm 0.0°",
            "表情预设：无 · 未找到：happy、blink": "Preset expressions: none · not found: happy, blink",
            "SpringBone：308 个链间横向关节（裙摆等）无法表达，已舍弃；已按裙片朝向修正 273 处裙摆与腿的碰撞方向，减少裙片被腿分开":
                "SpringBone: 308 sideways joints between chains (skirts …) can't be expressed and were dropped; "
                "273 skirt–leg collisions were adjusted to the skirt panels' direction so the legs split the skirt less",
            "已写出 奥黛塔.vrm（26.26 MB）· 节点 551 · morph target 70 · 表情 74":
                "Wrote 奥黛塔.vrm (26.26 MB) · 551 nodes · 70 morph targets · 74 expressions",
            "ValueError: 不是 VRM 文件": "ValueError: Not a VRM file",
        }
        for zh, en in cases.items():
            self.assertEqual(logtext.to_en(zh), en)
        self.assertIsNone(logtext.to_en("没有这句日志"))
        self.assertIsNone(logtext.to_en("plain English"))


if __name__ == "__main__":
    unittest.main()
