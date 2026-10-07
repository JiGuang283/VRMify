Model-To-VRM

把 MMD 的 PMX 模型轉換成 VRM 1.0 / 0.x，可以匯入 Unity（UniVRM）、VSeeFace 等支援 VRM 的軟體和遊戲。
FBX / unitypackage、uemodel、psk / pskx、xps 可以先轉成 PMX，再轉 VRM。

支援的轉換方向：
PMX → VRM（1.0 / 0.x）：骨骼、表情、MToon 材質、SpringBone 物理（僅 1.0）
FBX / unitypackage → PMX
uemodel → PMX
psk / pskx → PMX
XPS → PMX

使用方法：
1. 雙擊執行 ModelConvert.exe
2. 把檔案拖進來，或點擊拖放區選擇檔案。拖入只會載入預覽，按「開始轉換」才會轉換。
3. 「任務」下拉選單預設依副檔名自動判斷：.pmx 轉成 VRM，其他格式轉成 PMX。
4. 「VRM 選項」頁可選 VRM 版本（1.0 / 0.x）。
5. 轉換完成後按「三維預覽（three-vrm）」檢視效果：動作、物理、燈光、表情都和匯入軟體後一致。

---------------------------

注意事項：

- 匯入 Unity / 遊戲時卡在載入中：多半是讀到了不合規的資料。程式的日誌在
  %USERPROFILE%\AppData\LocalLow\<公司名>\<產品名>\Player.log，裡面有具體錯誤。
- 臉偏白、沒有陰影：VRoid 等原生 VRM 的臉本來就幾乎是平塗的，陰影主要是頭髮的投影（粉色）。
  可以在三維預覽裡調整燈光角度和投影檢查。
- 頭髮 / 裙擺抖動或穿模：物理是近似換算，大幅抬腿時裙擺和大腿根部有少量重疊，原生模型同樣如此。
  匯入 Unity 後可以在 UniVRM 的 SpringBone 裡逐條調整。
- 仍有鏤空：先看日誌裡「繞序自動判定」那行，判定錯了用 --flip-winding；
  頭髮 / 裙擺這類薄片需要雙面渲染，用 --force-double-sided，或勾選介面的「材質強制雙面」。
- FBX 轉出來背對鏡頭：勾選 FBX 選項頁的「自動判定朝向」（預設開），仍然背對就勾「強制額外轉 180°」。
- FBX 該透明的地方不透明：整塊半透、出現鬼影時貼圖透明通道選「全部去除」；
  蕾絲 / 髮梢變成硬邊時選「自動判定」或「保留原樣」。來源貼圖不會被改寫。
- 錯誤 FileNotFoundError：Windows 預設限制路徑總長 260 字元。本工具已自動處理；
  舊版本可以把檔案放到靠近磁碟根目錄的地方（例如 D:\model\）再轉。
- MMD 提示「無法載入編碼為 UTF16 的 PMX 檔案」：中文漢化版的翻譯錯誤，意思正好相反。
  MMD 只認 UTF-16LE，本工具輸出的 PMX 一律是 UTF-16LE。
- PmxEditor 報「アンカーデータの読み込みに失敗しました」：PmxEditor 把模型旁的同名 .psk
  當成錨點資料了，按確定即可，模型照常載入。
- 拖放僅在 Windows 下可用，其他系統請用「瀏覽」按鈕選擇檔案。
- 設定檔 config.json 記錄語言 / 介面縮放 / 各選項，刪除後會以預設值重建。

已知限制：

- VRM 0.x 不輸出 SpringBone 物理。
- PMX 裡鏈與鏈之間的橫向物理關節（裙擺常見）在 SpringBone 裡無法表達，會捨棄。
- 乘算球面貼圖（.sph）在 MToon 裡沒有對應項，會忽略。
- 貼圖：.dds（DXT1 / DXT3 / DXT5）能解碼；KTX2 / WebP 不支援，會退化為純色。
- FBX 轉換保留英文骨骼名，直接套 MMD 動作（.vmd）需要先改成日文標準名；XPS 沒有表情。

版權：
VRM 的授權資訊預設寫成最保守的「需要署名、禁止再散布、禁止修改」。
轉換前請確認你有權使用該模型；很多 MMD 模型禁止二次配布，轉出的 VRM 同樣不能公開散布。

---------------------------

本專案衍生自 SaraKale 的 Model-To-PMX（GPL-3.0），2026-10 起修改為專門轉換 VRM 的版本。
原專案：https://github.com/SaraKale/Model-To-PMX
原專案圖示設計和測試：沙拉醬、小桃子

---------------------------

更新歷史：

2026-10 Model-To-VRM：重做 PMX → VRM（骨骼對應與 T-pose、表情、依 toon 換算的 MToon 陰影、
  PMX 物理 → SpringBone、通過 VRM 官方 Schema 驗證）；新增 three-vrm 三維預覽；
  刪除 VRM → PMX、PMX → uemodel / psk / FBX 輸出。

原專案 Model-To-PMX 更新歷史：
2026-10-05 v1.1.6：修復路徑長度限制；修復 unitypackage 解包時中繼資料檔案互相覆蓋的問題。
2026-10-02 v1.1.5：增加版本檢測，修復部分 VRM→PMX 轉換後拖入 MMD 閃退問題。
2026-10-01 v1.1.4：新增 XPS → PMX 選項。
2026-09-27 v1.1.3：修復 uemodel 轉換 PMX 後比例問題，UE 選項增加自動對齊到參考模型選項。
2026-09-25 v1.1.2：新增 PMX → FBX、PMX → psk / pskx 選項，輸出檔案會輸出到同名資料夾。
2026-09-24 v1.1.1：新增 psk / pskx → PMX 選項，並去除 toon。
2026-09-23 v1.1.0：修復部分 FBX → PMX 轉換問題
2026-09-21 v1.0.9：修復 uemodel（ZSTD）→ PMX 轉換問題
2026-09-19 v1.0.8：增加 .uemodel 檔案互轉功能，修復部分問題。
2026-09-18 v1.0.7：修復部分 VRM → PMX 轉換問題
2026-09-18 v1.0.5-v1.0.6：修復 PMX → VRM 轉換問題
2026-09-18 v1.0.4：修正 MMD 載入 UTF-16 報錯的問題，修正轉換按鈕的邏輯，增加檔案清單。
2026-09-17 v1.0.3：修復 unitypackage 的骨骼傾倒問題
2026-09-15 v1.0.2：修復模型預覽介面
2026-09-13 v1.0.1：修復模型預覽介面
2026-09-13 v1.0.0：發布
