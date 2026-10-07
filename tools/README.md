# 工具

开发和批量处理用的脚本，不随程序打包。转换器本身（`convert/`、`formats/`）只用 Python 标准库；
这里的检查工具另需 `jsonschema`（`pip install -r requirements-dev.txt`）和 Node.js。

## 批量转换 `batch_convert.py`

```bash
python tools/batch_convert.py model -o vrm
python tools/batch_convert.py model -o vrm --only 穗穗 爱弥斯 --variants spring
```

* 模型目录下每个子文件夹算一个角色，递归找所有 `.pmx`；文件夹里的 `.zip` 中的 PMX 也会解出来一起转
  （在临时目录里解压，不改动模型目录）。
* 默认输出两份：`<输出>/有SpringBone/<角色>/` 和 `<输出>/无SpringBone/<角色>/`。
* 「<名字>_by_<游戏>_<哈希>」形式的文件夹名会整理成「<游戏>—<名字>」。
* `--report 结果.json` 保存每个模型的日志。

## 静态检查 `qa/check_vrm.py`

```bash
python tools/qa/check_vrm.py vrm          # 递归检查目录下所有 .vrm
python tools/qa/check_vrm.py a.vrm -v     # 通过的也打印摘要
```

检查 humanoid 结构、网格、表情绑定、SpringBone 链，以及 **VRM 官方 JSON Schema**
（第一次运行时从 vrm-c/vrm-specification 下载固定版本，缓存在 `qa/.schema-cache/`）。
UniVRM 对不合规数据很少容错——比如材质颜色绑定少一个分量、BlendShape 重名，都会让 Unity 里的
导入直接中断（表现为卡在加载中），这些都会被它报出来。有错误时退出码为 1。

## 动态测试 `qa/dynamics.py`

```bash
cd tools/qa/harness && npm install        # 第一次
python tools/qa/dynamics.py vrm/有SpringBone -j 4
python tools/qa/dynamics.py a.vrm --fps 30 --jitter 0.3 --motions idle,walk_real,run,leg_raise
```

用 three.js + three-vrm 在 Node 里无界面地跑 SpringBone（和 UniVRM 同一套规则），测：

| 指标 | 含义 | 参考范围（VRoid / 官方示例模型） |
|---|---|---|
| 静止偏移 | 站着不动 2 秒后弹簧末端离绑定姿势的距离 | < 5 cm |
| hf | 末端 8 Hz 以上的颤动能量 / 链长 ×1000（平均 / 最大） | 头发 2～16，裙摆 3～58 |
| swing | 末端最大摆幅 / 链长 | — |
| 穿腿 | 裙摆关节落在模型自己腿部碰撞体里的比例（含静止时的重叠，和 idle 对比看） | — |

动作：`idle`（放下手臂、轻微晃动）、`walk_real` / `run`（带根运动的走路 / 跑步）、`leg_raise`
（抬腿 70°）等，定义在 `qa/harness/motions.mjs`。超出参考范围的数值标「!」，`--strict` 时返回 1。
