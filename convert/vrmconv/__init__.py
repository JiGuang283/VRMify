# -*- coding: utf-8 -*-
"""PMX → VRM 转换的各个阶段（入口是 convert/pmx2vrm.py 的 convert()）。

  humanoid     MMD 骨骼名 → VRM 骨位、付与骨合并、层级修正、A-pose → T-pose
  skeleton     骨骼预处理流程、glTF 节点树、占位骨
  mesh         顶点权重、按材质切 primitive、写 accessor
  materials    MToon 材质（toon 渐变 → 阴影色 / 明暗交界，线性颜色，透明判定）
  expressions  顶点 / 材质表情 → morph target 与 VRM 表情
  springbone   PMX 刚体物理 → VRMC_springBone
  vrm_ext      VRMC_vrm / VRM 0.x 扩展本体
  mathutil     3x3 矩阵小工具

依赖 formats/ 下的 pmxio、vrmio（作为顶层模块导入，调用方负责把 formats/ 放进 sys.path）。
"""
