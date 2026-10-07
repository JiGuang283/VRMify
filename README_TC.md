# Model-To-VRM（純 Python）

把 MMD 的 **PMX 模型轉成 VRM 1.0 / 0.x**，可以直接匯入 Unity（UniVRM）、VSeeFace、three-vrm 等支援 VRM 的軟體和遊戲。
FBX / unitypackage、uemodel（UEFormat）、psk / pskx（Unreal ActorX）、xps（XNALara）這些格式可以先轉成 PMX，再轉 VRM。

**全部用 Python 標準函式庫實作**：不需要 Blender、Unity、mmd_tools、UniVRM，直接讀寫二進位格式。

[English](README.md) | [简体中文](README_SC.md) | [繁體中文](README_TC.md) | [日本語](README_JP.md)

> 本專案衍生自 [SaraKale/Model-To-PMX](https://github.com/SaraKale/Model-To-PMX)（GPL-3.0）。
> 2026-10 起修改：重做了 PMX → VRM（骨骼、表情、MToon 材質、SpringBone 物理），
> 刪除了 VRM → PMX、PMX → uemodel / psk / FBX 等輸出方向。

---

## 一、功能

### PMX → VRM

| 方面 | 做法 |
|---|---|
| **骨骼** | 自動把 MMD 標準骨骼名（`下半身`、`左腕`、`左人指２` …）對應到 VRM humanoid；付與 100% 的 D 骨（`足D` 等）併入付與親；修正 humanoid 層級；缺少的必要骨位補上佔位骨 |
| **姿勢** | A-pose 手臂自動抬成 VRM 要求的 T-pose（骨骼和頂點一起轉） |
| **座標** | PMX（左手系）→ glTF（右手系）做一次鏡面反射，繞序自動判定，模型不會左右翻轉或鏤空 |
| **材質** | MToon。陰影色、明暗交界依 PMX 的 toon 貼圖換算（皮膚是粉色陰影，和原生 VRoid 一致）；顏色寫線性值；依貼圖 alpha 分布自動選不透明 / 裁切 / 半透明；加算球面貼圖 → matcap |
| **表情** | 頂點表情 → morph target（稀疏儲存，檔案小）；常用表情自動對應 VRM 預設（眨眼、あいうえお、喜怒哀樂）；材質表情 → 材質顏色綁定（顯示 / 隱藏部件） |
| **物理** | PMX 剛體 / 關節 → VRM 1.0 SpringBone：頭髮、裙擺、飄帶、袖子依原生 VRM 的手感換算，碰撞體來自 PMX 的跟隨骨骼剛體 |
| **相容性** | 輸出通過 VRM 官方 JSON Schema 驗證，避開 UniVRM 匯入時會中斷的資料（BlendShape 重名、顏色分量缺失等） |

物理和材質的換算細節見 [Document/PMX转VRM_使用说明.md](Document/PMX转VRM_使用说明.md)（簡體中文）。

### 其他格式 → PMX

| 輸入 | 說明 |
|---|---|
| FBX / unitypackage | 二進位和 ASCII FBX 7.x；自動判定朝向、處理貼圖 alpha 遮罩、跳過遊戲裡的效果片網格 |
| uemodel（UEFormat v1–v10） | FortnitePorting / FModel 匯出的中介格式；可依整身模型對齊拆開的部件 |
| psk / pskx（Unreal ActorX） | 含權重、MRPH 頂點表情、附加 UV；Bip001 骨名轉 MMD 日文標準名 |
| xps（XNALara 二進位） | 自動換軸、骨名轉日文標準名、補足 IK、保留貼圖 alpha |

這些格式轉出的 PMX 可以再轉 VRM。

### 三維預覽

用 three-vrm（和 UniVRM 同一套 MToon / SpringBone 規則）檢視轉換結果：待機 / 走路 / 跑步 / 抬腿動作、
可調的燈光和投影、表情滑桿、截圖。離線可用。

---

## 二、環境需求

- **Python 3.10+**（需內建 tkinter；Windows 官方安裝包預設包含）
- **選用**：`Pillow`（加速舊介面的貼圖解碼）、`pywebview`（三維預覽用獨立視窗，不裝就用系統瀏覽器）
- 拖放檔案進視窗依賴 Windows 原生介面；其他系統請用「瀏覽」按鈕選擇檔案

---

## 三、使用方法

### 圖形介面

```bash
python main.py
```

1. 把檔案拖進視窗（或點拖放區選擇）。拖入只會載入並預覽，**按「開始轉換」才會轉換**。
2. 「任務」預設依副檔名自動判斷：`.pmx` → VRM，其他格式 → PMX。
3. 「VRM 選項」頁可選 VRM 版本（1.0 / 0.x）和是否匯出表情。
4. 轉換完按「三維預覽（three-vrm）」檢視結果。

預設輸出到來源檔旁的 `<檔名>_<日期>_<序號>` 子資料夾，重複轉換不會覆蓋。

### 命令列

路徑含空格或中文時加雙引號。

```bash
# PMX → VRM 1.0（預設帶 SpringBone）
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"

# VRM 0.x（舊軟體用；0.x 不輸出 SpringBone）
python convert/pmx2vrm.py "model.pmx" --spec 0x --title "名字" --author "作者"

# 三維預覽
python gfx/web_preview.py "model.vrm"
```

`pmx2vrm.py` 的常用參數：

| 參數 | 說明 |
|---|---|
| `--spec 1.0\|0x` | VRM 版本，預設 1.0 |
| `--scale auto\|倍率` | 預設把身高正規化到 1.6 m |
| `--keep-pose` | 保留原姿勢（預設 A-pose → T-pose） |
| `--no-spring` / `--no-spring-skirt` | 不轉物理 / 不轉裙擺類物理 |
| `--spring-stiffness k` | SpringBone 硬度整體倍率，預設 1.0 |
| `--spring-gravity g` / `--spring-gravity-skirt g` | 頭髮等 / 裙擺的重力，預設 0 / 0.1 |
| `--no-spring-center` | 不以模型根節點為 center（預設設定，人物整體移動時頭髮不會被甩） |
| `--outline` | 依 PMX 輪廓線設定寫 MToon 描邊（預設不描邊） |
| `--force-double-sided` | 所有材質雙面 |
| `--flip-winding` | 強制反轉三角繞序（預設自動判定） |
| `--dense-morphs` / `--max-morphs N` | 表情用密集儲存 / 只匯出前 N 個 |
| `--title` / `--author` | 寫進 VRM meta |

其他格式 → PMX：

```bash
python convert/fbx2pmx.py "model.fbx" -o "model.pmx"            # --alpha keep|auto|strip、--face-180
python convert/uemodel2pmx.py "model.uemodel" -o "model.pmx"    # --fbx 同時寫一份 FBX
python convert/psk2pmx.py "model.pskx" -o "model.pmx"           # --raw-bone-names、--no-ik
python convert/xps2pmx.py "model.xps" -o "model.pmx"            # --show-unused、--one-sided
python convert/pmx_check.py "model.pmx"                         # PMX 驗證 + 預覽圖
python formats/unitypackage_unpack.py "pack.unitypackage"       # 解包 unitypackage
```

### 批次轉換和檢查

`tools/` 下有批次轉換、VRM 結構 / 官方 Schema 檢查、SpringBone 動態測試，用法見 [tools/README.md](tools/README.md)：

```bash
python tools/batch_convert.py model -o vrm           # 每個角色輸出帶 / 不帶 SpringBone 兩份
python tools/qa/check_vrm.py vrm                     # 需要 pip install jsonschema
python tools/qa/dynamics.py vrm                      # 需要 Node.js
```

---

## 四、目錄結構

```
Model-To-VRM/
├── main.py                 圖形介面入口
├── main.spec               PyInstaller 打包設定
├── convert/
│   ├── pmx2vrm.py          PMX → VRM 入口（convert() 與命令列）
│   ├── vrmconv/            PMX → VRM 的各個階段
│   │   ├── humanoid.py     骨骼名對應、付與骨合併、層級修正、T-pose
│   │   ├── skeleton.py     骨骼前處理、glTF 節點樹、佔位骨
│   │   ├── mesh.py         頂點權重、依材質切網格
│   │   ├── materials.py    MToon 材質（toon 換算、透明判定）
│   │   ├── expressions.py  表情
│   │   ├── springbone.py   物理 → SpringBone
│   │   └── vrm_ext.py      VRM 擴充本體
│   ├── fbx2pmx.py  uemodel2pmx.py  psk2pmx.py  xps2pmx.py   其他格式 → PMX
│   └── pmx_check.py        PMX 驗證
├── formats/                格式讀寫（pmxio、vrmio(GLB)、fbx_reader、uemodelio、pskio、xpsio …）
├── gfx/
│   ├── preview.py          介面內的即時預覽（純 Python 軟體光柵）
│   ├── web_preview.py      三維預覽（本機伺服器 + 視窗）
│   └── web/                三維預覽頁面，vendor/ 裡是 three.js、three-vrm 離線副本
└── tools/                  批次轉換和品質檢查（不隨程式打包）
```

模組之間用裸名匯入（`import pmxio`），`main.py` 執行時會把 `formats/`、`convert/`、`gfx/` 加進 `sys.path`。

---

## 五、打包

```bash
pip install pyinstaller
pyinstaller main.spec
```

**務必用 `main.spec`**：子目錄模組是執行時才加進 `sys.path` 的，PyInstaller 的靜態分析看不到，
spec 裡設定好了 `pathex`、`hiddenimports` 和三維預覽的頁面資源（`gfx/web`）。改了原始碼要重新打包。

推送形如 `v1.2.3` 的 tag 會觸發 [GitHub Actions](.github/workflows/build-app.yml)，在 Windows / macOS / Linux
（x64 與 ARM）上自動打包並上傳到草稿 Release。

---

## 六、常見問題

### 匯入 Unity / 遊戲時卡在載入中

多半是 UniVRM 讀到不合規的資料拋出了例外。先用 `tools/qa/check_vrm.py` 檢查檔案；Unity 程式的日誌在
`%USERPROFILE%\AppData\LocalLow\<公司名>\<產品名>\Player.log`，裡面有具體的例外。

### 臉偏白 / 沒有陰影

VRoid 等原生模型的臉本來就幾乎是平塗的，陰影主要來自頭髮等的**投影**（粉色）。這些由 PMX 的 toon 貼圖決定；
模型沒有 toon 貼圖時，眼睛、眉毛之類的材質不畫陰影。可以在三維預覽裡調整燈光角度和投影開關檢查。

### 頭髮 / 裙擺抖動、穿模

SpringBone 是近似換算，不是複刻 MMD 的物理。可以先用 `--spring-stiffness` 整體調硬 / 調軟，
匯入 Unity 後也可以在 UniVRM 裡逐條調整。大幅抬腿時裙擺和大腿根部有少量重疊，原生模型同樣如此。

### 模型鏤空 / 黑邊

看日誌裡「繞序自動判定」那一行。判定錯了加 `--flip-winding`；薄片頭髮、裙擺依賴雙面的加 `--force-double-sided`。

### 錯誤 `FileNotFoundError`（路徑太長）

Windows 預設限制路徑 260 字元，unitypackage 解出來的目錄很深時容易超過。本工具會自動改用 `\\?\` 長路徑形式；
舊版本可以把檔案移到靠近磁碟根目錄的地方再轉。

### FBX 轉出來背對鏡頭 / 透明不對

FBX 沒有「正面朝哪」的欄位，預設依腳尖方向、臉部網格重心判定，不對時加 `--face-180`。貼圖 alpha 預設
`--alpha auto` 逐張判定是透明還是遮罩，整塊半透用 `strip`，蕾絲、髮梢變硬邊用 `keep`。

### MMD 提示無法載入 PMX（編碼）

MMD 只讀 UTF-16LE 編碼的 PMX（中文漢化版的提示把意思譯反了）。本工具輸出的 PMX 都是 UTF-16LE；
舊檔案可以用 `python formats/pmxio.py "舊檔案.pmx"` 轉換。

### PmxEditor 提示「アンカーデータの読み込みに失敗しました」

PmxEditor 會自動載入和模型同名的 `.psk` 當作錨點資料，而 `.psk` 也是 Unreal 網格的副檔名。按確定即可，模型正常；
把 PMX 改名或關閉 `[ファイル] → [アンカーデータの自動読み込み／保存]` 就不會再跳出。

---

## 七、已知限制

- **VRM 0.x 不輸出 SpringBone**（物理只寫 VRM 1.0）。
- **物理**：PMX 裡鏈與鏈之間的橫向關節（裙擺常見）在 SpringBone 裡無法表達，會捨棄。
- **材質**：乘算球面貼圖（.sph）、共用 toon07～10 沒有對應項；描邊預設不輸出。
- **表情預設**：依 MMD 標準表情名對應，`にこり`（笑眉）這類只動眉毛的表情對應到 happy 時變化不明顯。
- **其他格式**：FBX 保留英文骨骼名（套 MMD 動作需改名）；XPS 沒有表情；PMX 每個頂點最多 4 根骨骼權重。
- **貼圖**：KTX2 / WebP 不支援，材質會退化為純色。

---

## 八、授權與版權

- 本專案以 **GPL-3.0** 發布（見 [LICENSE](LICENSE)），原作者 SaraKale 等。
- 三維預覽附帶的 three.js、three-vrm 為 MIT 授權（見 `gfx/web/vendor/`）。
- VRM meta 預設寫成最保守的授權：需要署名、禁止再散布、禁止修改。
- **轉換前請自行確認你有權使用該模型**。很多 MMD 模型禁止二次配布，轉出的 VRM 同樣不能公開散布。
