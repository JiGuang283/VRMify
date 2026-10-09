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
| 张开 | 相邻裙摆链末端比静止时多分开的距离（平均 / 90% / 最大，cm）；腿把裙子从中间劈开时变大 | 原 PMX 的 MMD 物理：走路 4～15 |

动作：`idle`（放下手臂、轻微晃动）、`walk_real` / `run`（带根运动的走路 / 跑步）、`leg_raise`
（抬腿 70°）等，定义在 `qa/harness/motions.mjs`。超出参考范围的数值标「!」，`--strict` 时返回 1。

## 回归对比 `regress.py`

改转换代码前后，用本机的真实模型确认输出有没有变、变在哪里：

```bash
python tools/regress.py record model/角色A model/角色B/某.pmx   # 记下基准（默认 VRM 1.0，--spec 0x 记 0.x）
python tools/regress.py check                                   # 用当前代码重转、校验、对比基准
python tools/regress.py check --code ../旧版本的代码目录          # 用另一份代码转（比如 git worktree）
```

* 基准在 `tests/.golden/`（不进仓库：模型许可不允许再分发，衍生数据也只留在本机），记录 glTF JSON 全文和二进制块的 sha256。
* `check` 对每个样本：转换 → `check_vrm` 结构和 Schema 校验 → 列出 JSON 里不同的路径（最多 20 条）、二进制块是否一致。
  有错误或差异时退出码为 1。变化是否符合预期要自己判断，确认后重新 `record`。

## 表情截图 `qa/snapshots.py`

```bash
python tools/qa/snapshots.py a.vrm -o a.png                     # 全身、脸、表情预设、口型、眨眼、视线四个方向
python tools/qa/snapshots.py 旧/a.vrm 新/a.vrm -o 对比.png       # 每个输入一行，上下对照
python tools/qa/snapshots.py model/角色/角色.pmx -o a.png         # 不是 VRM 的先用当前代码转一份（临时目录）
python tools/qa/snapshots.py a.vrm --custom --cols 12 -o a.png  # 再加上自定义表情，每段 12 列
```

用无界面的 Edge / Chrome（`--browser` 指定路径）加载界面预览用的 `ui/viewer/viewer-core.js` 来拍，
和程序里看到的一致；WebGL 走软件渲染，不依赖显卡，一个模型几秒。模型没有的表情格子标「无」。
改表情相关的代码前后各拍一张对照，比只看 `regress.py` 的 JSON 差异直观。

## 单元测试 `tests/`

```bash
python -m unittest discover -s tests -v
```

不需要真实模型：`tests/synth.py` 合成一个小的 MMD 人形 PMX（标准骨名、头发物理、几个表情、两张贴图），
测转换（1.0 / 0.x、元信息、结果确定性、贴图瘦身）、转换后调整（`vrm_edit` 的套用 / 还原 / 幂等）和 zip 输入。
装了 jsonschema 时顺带做官方 Schema 校验；没装 Pillow 时跳过贴图瘦身的测试。

## 程序图标 `make_icon.py`

```bash
python tools/make_icon.py      # → ui/icon.ico（exe 图标）+ ui/web/icon.png（页面图标），需要 Pillow
```

圆角蓝底 + 白色菱形，和界面左上角的「◆」一致；改颜色、形状就改这个脚本再重新生成。

## 打包发布 `build_release.py`

```bash
python tools/build_release.py                          # → dist/VRMify-v<版本>-win64.zip
python tools/build_release.py --expect-version v0.1.0  # tag 和 ui/version.py 不一致时报错（Actions 里这么调用）
```

按 `VRMify.spec` 打包，把 `LICENSE`、README 放进程序文件夹，再压成绿色版 zip。GitHub Actions 的
`release.yml` 用的也是它，流程见 README 的「开发」一节。

## 冒烟测试 `smoke_exe.py`

```bash
python tools/smoke_exe.py dist/VRMify-v0.1.2-win64.zip   # 发布 zip、解开的程序文件夹或 exe 都行
```

单元测试测源码，这里测**打包以后**的 exe：用临时数据目录启动 `--no-open`，通过它自己的 HTTP 接口
加入合成的 PMX / zip / FBX / unitypackage 并转换，检查版本号、页面和预览库、每个输出过 `check_vrm`、
PMX 原生预览和调整面板接口。PyInstaller 漏打模块或页面资源时会在这里失败（退出码 1，并打印 exe 的输出）。
`release.yml` 打包后自动跑，不通过就不建 Release。
