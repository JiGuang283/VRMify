# -*- coding: utf-8 -*-
"""版本号（唯一来源）。界面顶栏、exe 的「属性 → 详细信息」和发布包的文件名都从这里取；
发版时打的 tag（v0.1.0）必须和它一致，tools/build_release.py --expect-version 会检查。"""
__version__ = "0.1.3"
