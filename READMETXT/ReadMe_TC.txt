模型轉換器

可以把各種模型格式文件轉換輸出到 .pmx文件。

支持的轉換方向：
FBX／unitypackage → PMX
VRM（0.x／1.0）→PMX
PMX → VRM（0.x / 1.0）
uemodel → PMX
PMX → uemodel
psk / pskx → PMX 
PMX →FBX
PMX → psk / pskx
XPS → PMX

如果需要macOS、Linux版本，請到這裏下載：
https://github.com/SaraKale/Model-To-PMX/releases

使用方法：
1. 雙擊運行 ModelConvert.exe
2. 把文件拖進來，或點擊拖放區選擇文件。
3. 「任務」下拉框可手動指定轉換方向，默認按擴展名自動判斷。
4. 轉換結果與日誌在左下方，模型預覽是右欄整列。

---------------------------

注意事項：

- 模型邊緣有黑色線條：本工具默認會在輸出 PMX 時關閉 MMD 輪廓線。若仍看到黑邊，
  請在 MMD / PMXEditor 裏選中全部材質，確認輪廓線已關閉併勾選「雙面描繪」。
- 仍有鏤空：先看日誌裏「繞序自動判定」那行，判定錯了用 --flip-winding；
  頭髮 / 裙擺這類薄片需要雙面渲染，用 --force-double-sided，或勾選界面的「材質強制雙面」。
- 模型背對鏡頭：FBX 裏沒有「正面朝哪」這個字段，猜錯很正常。勾選 FBX 選項頁的
  「自動判定朝向」（默認開），判定結果和依據會寫進日誌；仍然背對就勾「強制額外轉 180°」
  （命令行 --face-180）。它只在 X、Z 同時取反，頂點 / 面 / 骨骼數都不受影響。
  XPS 和 PSK 的換軸是固定映射，不受這個影響。
- 該透明的地方不透明：整塊半透、出現鬼影，多半是遊戲貼圖把 alpha 當發光遮罩用，
  用 --alpha strip（界面選「全部去除」）；日常建議用默認的 auto，它會逐張判定。
  蕾絲 / 薄紗 / 頭髮梢變成硬邊，說明真透明被削掉了，改回 auto 或選 keep。
  無論哪一檔，源貼圖原圖都不會被改寫，去 alpha 的結果寫到「名字_noalpha.png」副本。
- 身上蓋了一塊灰白大板：遊戲 FBX 裏常有一張沒有材質、沒有貼圖的 EffectMesh，
  UV 鋪滿整張圖集，看著很像「貼圖被翻錯」，其實 UV 一點沒錯。
  本工具默認跳過所有沒掛材質的網格，日誌會打印「skip 網格名」。
- 報錯 FileNotFoundError：Windows 默認限制路徑總長 260 字符，路徑太深就會報這個錯，
  而報錯裏那串路徑看著完全正常。本工具已自動改用擴展長度形式處理，
  不需要改註冊表、不需要管理員權限、也不需要重啟。舊版本請更新到最新版；
  臨時辦法是把文件放到靠近盤符根目錄的地方（例如 D:\model\）再轉。
  注意這不是中文路徑的問題：中文、日文、空格、方括號都沒問題，只跟總長度有關。
- MMD 提示「無法載入編碼為 UTF16 的 PMX 文件」：這是中文漢化版的翻譯錯誤，意思正好相反。
  MMD 只認 UTF-16LE，看到這句說明文件其實是 UTF-8。本工具輸出的 PMX 一律是 UTF-16LE。
- PmxEditor 報「アンカーデータの読み込みに失敗しました」：這是 PmxEditor 的報錯，
  和 PMX 文件本身無關 —— 它把模型旁邊的同名 .psk 當成自己的錨點數據去解析了。
  點確定即可，模型照常加載。想徹底不彈：把 PMX 挪到別的文件夾或改個名，
  或在 PmxEditor 裏關掉 [ファイル] → [アンカーデータの自動読み込み／保存]。
- 拖放僅在 Windows 下可用，其他系統請用「瀏覽」按鈕選擇文件。
- 設置文件 config.json 記錄語言 / 界面縮放 / 任務方向 / 各選項，刪除後會以默認值重建。

已知限制：

- FBX：二進制 FBX 7.x 與 ASCII FBX 都能讀。
- 貼圖：.dds（DXT1 / DXT3 / DXT5）能原生解碼；KTX2 / WebP 不支持，會退化為純色。
- 物理：PMX 剛體 / 關節 ↔ VRM SpringBone 不會互相轉換。
- 材質效果：.sph / .spa / toon 貼圖在 VRM 側不保留；所有方向寫出的 PMX 都不使用 Toon。
- 骨骼名：FBX 轉換保留英文骨骼名（Hips、Spine …），直接套 MMD 現成動作（.vmd）
  匹配不上，需在 PMXEditor 裏批量改為日文標準名。可以使用我製作的 PE 骨骼整理插件：
https://www.bilibili.com/video/BV1MfYX6JEhE/
https://www.youtube.com/watch?v=zZj-I4YgIDY
https://www.nicovideo.jp/watch/sm46811122
  PSK 走的是另一條路：Bip001 那套 3dsMax Biped 命名默認就轉成 MMD 標準日文名，
  英文原名寫進骨骼的英文名備註；XPS 同理，按規則改名，認不出來的保留原名。
- 表情：源模型沒有 BlendShape / morph 時，PMX 表情也為 0，需手工創建。
- PSK / PSKX：PMX 的 IK / 付與(繼承) / 剛體 / 關節 / UV 表情 / 材質表情在 PSK 裏
  沒有對應結構，一律跳過；PMX 頂點最多 4 根骨骼權重，源文件有 5~6 根時轉一圈會丟。
  .psk 這個擴展名同時被 PmxEditor 用作錨點數據（見上）。
- XPS / XNALara：只讀二進制 .xps，更老的純文本變體不支持，會明確報錯。
  該格式不存 BlendShape，所以導出的 PMX 表情數為 0。

---------------------------

圖標設計和測試：
沙拉酱、小桃子

反饋：
如果仍然有問題，請到下面與我聯系反饋併提供樣本文件。
https://github.com/SaraKale
https://space.bilibili.com/4197121
https://www.aplaybox.com/u/269621856
https://x.com/SaraKale9

---------------------------

更新歷史：

2026-10-05 v1.1.6：修復路徑長度限製；修復 unitypackage 解包時元數據文件（asset.meta / pathname / preview.png）互相覆蓋的問題。
2026-10-02 v1.1.5：增加版本檢測，修復部分 VRM→PMX 轉換後拖入MMD閃退問題。
2026-10-01 v1.1.4：新增 XPS → PMX 選項。
2026-09-27 v1.1.3：修復 uemodel 轉換PMX後比例問題，UE選項增加自動對齊到參考模型選項。
2026-09-25 v1.1.2：新增 PMX →FBX，PMX → psk / pskx選項，輸出文件會輸出到同名文件夾。
2026-09-24 v1.1.1：新增 psk / pskx →PMX 選項，併去除toon。
2026-09-23 v1.1.0：修復部分 FBX→PMX 轉換問題
2026-09-21 v1.0.9：修復 uemodel（ZSTD）→PMX 轉換問題
2026-09-19 v1.0.8：增加 .uemodel 文件互轉功能，修復部分問題。
2026-09-18 v1.0.7：修復部分 VRM→PMX 轉換問題
2026-09-18 v1.0.5-v1.0.6：修復 PMX→VRM 轉換問題
2026-09-18 v1.0.4：修正MMD載入UTF-16報錯的問題，修正轉換按鈕的邏輯，增加文件列表框。
2026-09-17 v1.0.3：修復unitypackage的骨骼傾倒問題
2026-09-15 v1.0.2：修復模型預覽界面
2026-09-13 v1.0.1：修復模型預覽界面
2026-09-13 v1.0.0：發布

by:SaraKale