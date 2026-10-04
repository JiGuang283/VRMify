模型转换器

可以把各种模型格式文件转换输出到 .pmx文件。

支持的转换方向：
FBX / unitypackage → PMX
VRM（0.x / 1.0） → PMX 
PMX → VRM（0.x / 1.0）
uemodel → PMX
PMX → uemodel
psk / pskx → PMX 
PMX → FBX
PMX → psk / pskx
XPS → PMX

如果需要macOS、Linux版本，请到这里下载：
https://github.com/SaraKale/Model-To-PMX/releases

使用方法：
1. 双击运行 ModelConvert.exe
2. 把文件拖进来，或点击拖放区选择文件。
3. 「任务」下拉框可手动指定转换方向，默认按扩展名自动判断。
4. 转换结果与日志在左下方，模型预览是右栏整列。

---------------------------

注意事项：

- 模型边缘有黑色线条：本工具默认会在输出 PMX 时关闭 MMD 轮廓线。若仍看到黑边，
  请在 MMD / PMXEditor 里选中全部材质，确认轮廓线已关闭并勾选「双面描绘」。
- 仍有镂空：先看日志里「绕序自动判定」那行，判定错了用 --flip-winding；
  头发 / 裙摆这类薄片需要双面渲染，用 --force-double-sided，或勾选界面的「材质强制双面」。
- 模型背对镜头：FBX 里没有「正面朝哪」这个字段，猜错很正常。勾选 FBX 选项页的
  「自动判定朝向」（默认开），判定结果和依据会写进日志；仍然背对就勾「强制额外转 180°」
  （命令行 --face-180）。它只在 X、Z 同时取反，顶点 / 面 / 骨骼数都不受影响。
  XPS 和 PSK 的换轴是固定映射，不受这个影响。
- 该透明的地方不透明：整块半透、出现鬼影，多半是游戏贴图把 alpha 当发光遮罩用，
  用 --alpha strip（界面选「全部去除」）；日常建议用默认的 auto，它会逐张判定。
  蕾丝 / 薄纱 / 头发梢变成硬边，说明真透明被削掉了，改回 auto 或选 keep。
  无论哪一档，源贴图原图都不会被改写，去 alpha 的结果写到「名字_noalpha.png」副本。
- 身上盖了一块灰白大板：游戏 FBX 里常有一张没有材质、没有贴图的 EffectMesh，
  UV 铺满整张图集，看着很像「贴图被翻错」，其实 UV 一点没错。
  本工具默认跳过所有没挂材质的网格，日志会打印「skip 网格名」。
- 报错 FileNotFoundError：Windows 默认限制路径总长 260 字符，路径太深就会报这个错，
  而报错里那串路径看着完全正常。本工具已自动改用扩展长度形式处理，
  不需要改注册表、不需要管理员权限、也不需要重启。旧版本请更新到最新版；
  临时办法是把文件放到靠近盘符根目录的地方（例如 D:\model\）再转。
  注意这不是中文路径的问题：中文、日文、空格、方括号都没问题，只跟总长度有关。
- MMD 提示「无法载入编码为 UTF16 的 PMX 文件」：这是中文汉化版的翻译错误，意思正好相反。
  MMD 只认 UTF-16LE，看到这句说明文件其实是 UTF-8。本工具输出的 PMX 一律是 UTF-16LE。
- PmxEditor 报「アンカーデータの読み込みに失敗しました」：这是 PmxEditor 的报错，
  和 PMX 文件本身无关 —— 它把模型旁边的同名 .psk 当成自己的锚点数据去解析了。
  点确定即可，模型照常加载。想彻底不弹：把 PMX 挪到别的文件夹或改个名，
  或在 PmxEditor 里关掉 [ファイル] → [アンカーデータの自動読み込み／保存]。
- 拖放仅在 Windows 下可用，其他系统请用「浏览」按钮选择文件。
- 设置文件 config.json 记录语言 / 界面缩放 / 任务方向 / 各选项，删除后会以默认值重建。

已知限制：

- FBX：二进制 FBX 7.x 与 ASCII FBX 都能读。
- 贴图：.dds（DXT1 / DXT3 / DXT5）能原生解码；KTX2 / WebP 不支持，会退化为纯色。
- 物理：PMX 刚体 / 关节 ↔ VRM SpringBone 不会互相转换。
- 材质效果：.sph / .spa / toon 贴图在 VRM 侧不保留；所有方向写出的 PMX 都不使用 Toon。
- 骨骼名：FBX 转换保留英文骨骼名（Hips、Spine …），直接套 MMD 现成动作（.vmd）
  匹配不上，需在 PMXEditor 里批量改为日文标准名。可以使用我制作的 PE 骨骼整理插件：
https://www.bilibili.com/video/BV1MfYX6JEhE/
https://www.youtube.com/watch?v=zZj-I4YgIDY
https://www.nicovideo.jp/watch/sm46811122
  PSK 走的是另一条路：Bip001 那套 3dsMax Biped 命名默认就转成 MMD 标准日文名，
  英文原名写进骨骼的英文名备注；XPS 同理，按规则改名，认不出来的保留原名。
- 表情：源模型没有 BlendShape / morph 时，PMX 表情也为 0，需手工创建。
- PSK / PSKX：PMX 的 IK / 付与(继承) / 刚体 / 关节 / UV 表情 / 材质表情在 PSK 里
  没有对应结构，一律跳过；PMX 顶点最多 4 根骨骼权重，源文件有 5~6 根时转一圈会丢。
  .psk 这个扩展名同时被 PmxEditor 用作锚点数据（见上）。
- XPS / XNALara：只读二进制 .xps，更老的纯文本变体不支持，会明确报错。
  该格式不存 BlendShape，所以导出的 PMX 表情数为 0。

---------------------------

图标设计和测试：
沙拉酱、小桃子

反馈：
如果仍然有问题，请到下面与我联系反馈并提供样本文件。
https://github.com/SaraKale
https://space.bilibili.com/4197121
https://www.aplaybox.com/u/269621856
https://x.com/SaraKale9

---------------------------

更新历史：

2026-10-05 v1.1.6：修复路径长度限制；修复 unitypackage 解包时元数据文件（asset.meta / pathname / preview.png）互相覆盖的问题。
2026-10-02 v1.1.5：增加版本检测，修复部分 VRM→PMX 转换后拖入MMD闪退问题。
2026-10-01 v1.1.4：新增 XPS → PMX 选项。
2026-09-27 v1.1.3：修复 uemodel 转换PMX后比例问题，UE选项增加自动对齐到参考模型选项。
2026-09-25 v1.1.2：新增 PMX →FBX，PMX → psk / pskx选项，输出文件会输出到同名文件夹。
2026-09-24 v1.1.1：新增 psk / pskx →PMX 选项，并去除toon。
2026-09-23 v1.1.0：修复部分 FBX→PMX 转换问题
2026-09-21 v1.0.9：修复 uemodel（ZSTD）→PMX 转换问题
2026-09-19 v1.0.8：增加 .uemodel 文件互转功能，修复部分问题。
2026-09-18 v1.0.7：修复部分 VRM→PMX 转换问题
2026-09-18 v1.0.5-v1.0.6：修复 PMX→VRM 转换问题
2026-09-18 v1.0.4：修正MMD载入UTF-16报错的问题，修正转换按钮的逻辑，增加文件列表框。
2026-09-17 v1.0.3：修复unitypackage的骨骼倾倒问题
2026-09-15 v1.0.2：修复模型预览界面
2026-09-13 v1.0.1：修复模型预览界面
2026-09-13 v1.0.0：发布

by:SaraKale