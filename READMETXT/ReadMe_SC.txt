Model-To-VRM

把 MMD 的 PMX 模型转换成 VRM 1.0 / 0.x，可以导入 Unity（UniVRM）、VSeeFace 等支持 VRM 的软件和游戏。
FBX / unitypackage、uemodel、psk / pskx、xps 可以先转成 PMX，再转 VRM。

支持的转换方向：
PMX → VRM（1.0 / 0.x）：骨骼、表情、MToon 材质、SpringBone 物理（仅 1.0）
FBX / unitypackage → PMX
uemodel → PMX
psk / pskx → PMX
XPS → PMX

使用方法：
1. 双击运行 ModelConvert.exe
2. 把文件拖进来，或点击拖放区选择文件。拖入只会载入预览，点「开始转换」才会转换。
3. 「任务」下拉框默认按扩展名自动判断：.pmx 转成 VRM，其他格式转成 PMX。
4. 「VRM 选项」页可选 VRM 版本（1.0 / 0.x）。
5. 转换完成后点「三维预览（three-vrm）」查看效果：动作、物理、灯光、表情都和导入软件后一致。

---------------------------

注意事项：

- 导入 Unity / 游戏时卡在加载中：多半是读到了不合规的数据。程序的日志在
  %USERPROFILE%\AppData\LocalLow\<公司名>\<产品名>\Player.log，里面有具体错误。
- 脸偏白、没有阴影：VRoid 等原生 VRM 的脸本来就基本是平涂的，阴影主要是头发的投影（粉色）。
  可以在三维预览里调灯光角度和投影检查。
- 头发 / 裙摆抖动或穿模：物理是近似换算，大幅抬腿时裙摆和大腿根部有少量重叠，原生模型同样如此。
  导入 Unity 后可以在 UniVRM 的 SpringBone 里逐条调整。
- 仍有镂空：先看日志里「绕序自动判定」那行，判定错了用 --flip-winding；
  头发 / 裙摆这类薄片需要双面渲染，用 --force-double-sided，或勾选界面的「材质强制双面」。
- FBX 转出来背对镜头：勾选 FBX 选项页的「自动判定朝向」（默认开），仍然背对就勾「强制额外转 180°」。
- FBX 该透明的地方不透明：整块半透、出现鬼影时贴图透明通道选「全部去除」；
  蕾丝 / 发梢变成硬边时选「自动判定」或「保留原样」。源贴图不会被改写。
- 报错 FileNotFoundError：Windows 默认限制路径总长 260 字符。本工具已自动处理；
  旧版本可以把文件放到靠近盘符根目录的地方（例如 D:\model\）再转。
- MMD 提示「无法载入编码为 UTF16 的 PMX 文件」：中文汉化版的翻译错误，意思正好相反。
  MMD 只认 UTF-16LE，本工具输出的 PMX 一律是 UTF-16LE。
- PmxEditor 报「アンカーデータの読み込みに失敗しました」：PmxEditor 把模型旁的同名 .psk
  当成锚点数据了，点确定即可，模型照常加载。
- 拖放仅在 Windows 下可用，其他系统请用「浏览」按钮选择文件。
- 设置文件 config.json 记录语言 / 界面缩放 / 各选项，删除后会以默认值重建。

已知限制：

- VRM 0.x 不输出 SpringBone 物理。
- PMX 里链与链之间的横向物理关节（裙摆常见）在 SpringBone 里无法表达，会舍弃。
- 乘算球面贴图（.sph）在 MToon 里没有对应项，会忽略。
- 贴图：.dds（DXT1 / DXT3 / DXT5）能解码；KTX2 / WebP 不支持，会退化为纯色。
- FBX 转换保留英文骨骼名，直接套 MMD 动作（.vmd）需要先改成日文标准名；XPS 没有表情。

版权：
VRM 的授权信息默认写成最保守的「需要署名、禁止再分发、禁止修改」。
转换前请确认你有权使用该模型；很多 MMD 模型禁止二次配布，转出的 VRM 同样不能公开分发。

---------------------------

本项目派生自 SaraKale 的 Model-To-PMX（GPL-3.0），2026-10 起修改为专门转换 VRM 的版本。
原项目：https://github.com/SaraKale/Model-To-PMX
原项目图标设计和测试：沙拉酱、小桃子

---------------------------

更新历史：

2026-10 Model-To-VRM：重做 PMX → VRM（骨骼映射与 T-pose、表情、按 toon 换算的 MToon 阴影、
  PMX 物理 → SpringBone、通过 VRM 官方 Schema 校验）；新增 three-vrm 三维预览；
  删除 VRM → PMX、PMX → uemodel / psk / FBX 输出。

原项目 Model-To-PMX 更新历史：
2026-10-05 v1.1.6：修复路径长度限制；修复 unitypackage 解包时元数据文件互相覆盖的问题。
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
