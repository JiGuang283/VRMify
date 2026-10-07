# Model-To-VRM（純 Python）

MMD の **PMX モデルを VRM 1.0 / 0.x に変換**します。Unity（UniVRM）、VSeeFace、three-vrm など VRM 対応のソフトやゲームにそのまま読み込めます。
FBX / unitypackage、uemodel（UEFormat）、psk / pskx（Unreal ActorX）、xps（XNALara）は一度 PMX に変換してから VRM にできます。

**Python 標準ライブラリのみで実装**：Blender、Unity、mmd_tools、UniVRM は不要で、バイナリ形式を直接読み書きします。

[English](README.md) | [简体中文](README_SC.md) | [繁體中文](README_TC.md) | [日本語](README_JP.md)

> 本プロジェクトは [SaraKale/Model-To-PMX](https://github.com/SaraKale/Model-To-PMX)（GPL-3.0）の派生です。
> 2026-10 から改変：PMX → VRM を作り直し（ボーン、表情、MToon マテリアル、SpringBone 物理）、
> VRM → PMX、PMX → uemodel / psk / FBX の出力を削除しました。

---

## 1. 機能

### PMX → VRM

| 項目 | 内容 |
|---|---|
| **ボーン** | MMD 標準ボーン名（`下半身`、`左腕`、`左人指２` …）を VRM humanoid に自動対応；付与 100% の D ボーン（`足D` など）を付与親に統合；humanoid の階層を修正；足りない必須ボーンはプレースホルダーで補完 |
| **ポーズ** | A ポーズの腕を VRM が求める T ポーズに起こす（ボーンと頂点を一緒に） |
| **座標** | PMX（左手系）→ glTF（右手系）を 1 回の鏡映で変換し、面の向きを自動判定。左右反転や穴あきが起きない |
| **マテリアル** | MToon。影色と明暗の境界は PMX の toon テクスチャから換算（肌はピンクの影で、VRoid ネイティブと同様）；色はリニア値で出力；テクスチャの alpha 分布から不透明 / カットアウト / 半透明を自動選択；加算スフィア → matcap |
| **表情** | 頂点モーフ → morph target（スパース格納でファイルが小さい）；よく使う表情は VRM プリセットに自動対応（まばたき、あいうえお、喜怒哀楽）；材質モーフ → マテリアルカラーバインド（パーツの表示 / 非表示） |
| **物理** | PMX の剛体 / ジョイント → VRM 1.0 SpringBone：髪、スカート、リボン、袖をネイティブ VRM の挙動に合わせて換算。コライダーは PMX のボーン追従剛体から |
| **互換性** | 出力は VRM 公式 JSON Schema に合格し、UniVRM の読み込みを中断させるデータ（BlendShape の名前重複、色成分の欠落など）を避けます |

物理とマテリアルの換算の詳細は [Document/PMX转VRM_使用说明.md](Document/PMX转VRM_使用说明.md)（中国語）を参照。

### その他の形式 → PMX

| 入力 | 説明 |
|---|---|
| FBX / unitypackage | バイナリと ASCII の FBX 7.x；向きの自動判定、テクスチャ alpha マスクの処理、ゲームのエフェクト板を除外 |
| uemodel（UEFormat v1–v10） | FortnitePorting / FModel の出力；分割パーツを全身モデルに合わせて整列可能 |
| psk / pskx（Unreal ActorX） | ウェイト、MRPH 頂点モーフ、追加 UV；Bip001 のボーン名を MMD 標準の日本語名に変換 |
| xps（XNALara バイナリ） | 軸の自動変換、日本語標準ボーン名、IK の補完、テクスチャ alpha を保持 |

変換した PMX はさらに VRM に変換できます。

### 3D プレビュー

three-vrm（UniVRM と同じ MToon / SpringBone の規則）で変換結果を確認：待機 / 歩き / 走り / 脚上げのモーション、
調整できるライトと影、表情スライダー、スクリーンショット。オフラインで動作します。

---

## 2. 動作環境

- **Python 3.10+**（tkinter 同梱のもの。Windows 公式インストーラーには標準で含まれます）
- **任意**：`Pillow`（旧プレビューのテクスチャ読み込み高速化）、`pywebview`（3D プレビューを専用ウィンドウで表示。無ければシステムのブラウザを使用）
- ウィンドウへのドラッグ＆ドロップは Windows ネイティブ API を使用。他の OS では「参照」ボタンで選択してください

---

## 3. 使い方

### GUI

```bash
python main.py
```

1. ファイルをウィンドウにドロップ（またはドロップ欄をクリックして選択）。ドロップは読み込みとプレビューだけで、**「変換開始」を押すと変換します**。
2. 「タスク」は既定で拡張子から自動判定：`.pmx` → VRM、その他の形式 → PMX。
3. 「VRM」タブで VRM のバージョン（1.0 / 0.x）と表情の出力有無を選べます。
4. 変換後に「3D プレビュー（three-vrm）」で結果を確認。

既定では元ファイルの隣の `<ファイル名>_<日付>_<番号>` フォルダーに出力するので、繰り返し変換しても上書きしません。

### コマンドライン

空白や日本語を含むパスは二重引用符で囲んでください。

```bash
# PMX → VRM 1.0（既定で SpringBone あり）
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"

# VRM 0.x（古いソフト向け。0.x は SpringBone を出力しません）
python convert/pmx2vrm.py "model.pmx" --spec 0x --title "名前" --author "作者"

# 3D プレビュー
python gfx/web_preview.py "model.vrm"
```

`pmx2vrm.py` の主なオプション：

| オプション | 説明 |
|---|---|
| `--spec 1.0\|0x` | VRM のバージョン。既定 1.0 |
| `--scale auto\|倍率` | 既定は身長を 1.6 m に正規化 |
| `--keep-pose` | 元のポーズを保持（既定は A ポーズ → T ポーズ） |
| `--no-spring` / `--no-spring-skirt` | 物理を変換しない / スカート系の物理を変換しない |
| `--spring-stiffness k` | SpringBone の硬さの全体倍率。既定 1.0 |
| `--spring-gravity g` / `--spring-gravity-skirt g` | 髪など / スカートの重力。既定 0 / 0.1 |
| `--no-spring-center` | モデルのルートを center にしない（既定の設定では全身の移動で髪が振られない） |
| `--outline` | PMX のエッジ設定から MToon のアウトラインを出力（既定はなし） |
| `--force-double-sided` | 全マテリアルを両面に |
| `--flip-winding` | 三角形の向きを強制反転（既定は自動判定） |
| `--dense-morphs` / `--max-morphs N` | モーフを密に格納 / 先頭 N 個だけ出力 |
| `--title` / `--author` | VRM meta に書き込み |

その他の形式 → PMX：

```bash
python convert/fbx2pmx.py "model.fbx" -o "model.pmx"            # --alpha keep|auto|strip、--face-180
python convert/uemodel2pmx.py "model.uemodel" -o "model.pmx"    # --fbx で FBX も出力
python convert/psk2pmx.py "model.pskx" -o "model.pmx"           # --raw-bone-names、--no-ik
python convert/xps2pmx.py "model.xps" -o "model.pmx"            # --show-unused、--one-sided
python convert/pmx_check.py "model.pmx"                         # PMX の検証 + プレビュー画像
python formats/unitypackage_unpack.py "pack.unitypackage"       # unitypackage の展開
```

### 一括変換とチェック

`tools/` に一括変換、VRM の構造 / 公式 Schema チェック、SpringBone の動的テストがあります。使い方は [tools/README.md](tools/README.md)：

```bash
python tools/batch_convert.py model -o vrm           # キャラごとに SpringBone あり / なしの 2 つを出力
python tools/qa/check_vrm.py vrm                     # pip install jsonschema が必要
python tools/qa/dynamics.py vrm                      # Node.js が必要
```

---

## 4. ディレクトリ構成

```
Model-To-VRM/
├── main.py                 GUI のエントリーポイント
├── main.spec               PyInstaller の設定
├── convert/
│   ├── pmx2vrm.py          PMX → VRM の入口（convert() とコマンドライン）
│   ├── vrmconv/            PMX → VRM の各段階
│   │   ├── humanoid.py     ボーン名の対応、付与ボーンの統合、階層修正、T ポーズ
│   │   ├── skeleton.py     ボーン前処理、glTF ノードツリー、プレースホルダー
│   │   ├── mesh.py         頂点ウェイト、マテリアルごとのメッシュ分割
│   │   ├── materials.py    MToon マテリアル（toon 換算、透明判定）
│   │   ├── expressions.py  表情
│   │   ├── springbone.py   物理 → SpringBone
│   │   └── vrm_ext.py      VRM 拡張本体
│   ├── fbx2pmx.py  uemodel2pmx.py  psk2pmx.py  xps2pmx.py   その他の形式 → PMX
│   └── pmx_check.py        PMX の検証
├── formats/                形式の読み書き（pmxio、vrmio(GLB)、fbx_reader、uemodelio、pskio、xpsio …）
├── gfx/
│   ├── preview.py          画面内のリアルタイムプレビュー（純 Python のソフトウェアラスタライザ）
│   ├── web_preview.py      3D プレビュー（ローカルサーバー + ウィンドウ）
│   └── web/                3D プレビューのページ。vendor/ に three.js、three-vrm のオフライン版
└── tools/                  一括変換と品質チェック（パッケージには含めない）
```

モジュール同士は裸の名前で import します（`import pmxio`）。`main.py` が実行時に `formats/`、`convert/`、`gfx/` を `sys.path` に追加します。

---

## 5. パッケージ化

```bash
pip install pyinstaller
pyinstaller main.spec
```

**必ず `main.spec` を使ってください**：サブディレクトリのモジュールは実行時に `sys.path` に追加されるため PyInstaller の静的解析からは見えません。
spec に `pathex`、`hiddenimports`、3D プレビューのページ資源（`gfx/web`）を設定済みです。ソースを変更したら再ビルドが必要です。

`v1.2.3` 形式のタグを push すると [GitHub Actions](.github/workflows/build-app.yml) が Windows / macOS / Linux
（x64 と ARM）でビルドし、下書きの Release にアップロードします。

---

## 6. よくある質問

### Unity / ゲームへの読み込みが止まる

たいていは UniVRM が不正なデータで例外を出しています。まず `tools/qa/check_vrm.py` でファイルを確認してください。Unity 製アプリのログは
`%USERPROFILE%\AppData\LocalLow\<会社名>\<製品名>\Player.log` にあり、具体的な例外が記録されています。

### 顔が白っぽい / 影がない

VRoid などネイティブモデルの顔はもともとほぼベタ塗りで、影は主に髪などの**落ち影**（ピンク）です。これは PMX の toon テクスチャで決まり、
toon のないマテリアル（目、眉など）には影を付けません。3D プレビューのライト角度と影の切り替えで確認できます。

### 髪 / スカートの揺れや突き抜け

SpringBone は近似換算で、MMD の物理の再現ではありません。`--spring-stiffness` で全体の硬さを調整するか、UniVRM で揺れものごとに調整してください。
脚を大きく上げるとスカートと太ももの付け根が少し重なりますが、ネイティブモデルでも同様です。

### 穴あき / 黒い縁

ログの「面の向きの自動判定」の行を確認してください。判定が誤っていれば `--flip-winding`、両面描画が前提の薄い髪やスカートには `--force-double-sided` を付けます。

### `FileNotFoundError`（パスが長すぎる）

Windows はパスを 260 文字までに制限しており、unitypackage を展開した深いフォルダーでは超えがちです。本ツールは自動で `\\?\` 形式の長いパスに切り替えます。
古いバージョンではファイルをドライブ直下近くに移してから変換してください。

### FBX が後ろ向き / 透明が正しくない

FBX には「正面」を示す項目がないため、つま先の向きと顔メッシュの重心で判定します。誤っていれば `--face-180` を付けてください。テクスチャの alpha は既定の
`--alpha auto` でテクスチャごとに透明かマスクかを判定します。全体が半透明になるなら `strip`、レースや髪先が硬くなるなら `keep`。

### MMD で PMX が読み込めない（文字コード）

MMD は UTF-16LE の PMX しか読めません。本ツールの出力は常に UTF-16LE です。古いファイルは `python formats/pmxio.py "古いファイル.pmx"` で変換できます。

### PmxEditor で「アンカーデータの読み込みに失敗しました」

PmxEditor はモデルと同名の `.psk` をアンカーデータとして自動で読みますが、`.psk` は Unreal のメッシュの拡張子でもあります。OK を押せばモデルは正常に開きます。
PMX の名前を変えるか、`[ファイル] → [アンカーデータの自動読み込み／保存]` をオフにすれば出なくなります。

---

## 7. 既知の制限

- **VRM 0.x では SpringBone を出力しません**（物理は VRM 1.0 のみ）。
- **物理**：PMX のチェーン間の横方向ジョイント（スカートに多い）は SpringBone で表現できないため捨てます。
- **マテリアル**：乗算スフィア（.sph）、共有 toon07～10 には対応する項目がありません。アウトラインは既定で出力しません。
- **表情プリセット**：MMD 標準の表情名で対応付けます。`にこり`（笑い眉）のように眉だけを動かす表情を happy に割り当てると変化が小さくなります。
- **その他の形式**：FBX は英語のボーン名を保持します（MMD のモーションを使うには名前の変更が必要）。XPS には表情がありません。PMX は 1 頂点あたり最大 4 ボーンのウェイトです。
- **テクスチャ**：KTX2 / WebP は非対応で、単色になります。

---

## 8. ライセンスと著作権

- 本プロジェクトは **GPL-3.0** で公開しています（[LICENSE](LICENSE)）。原作者は SaraKale ほか。
- 3D プレビューに同梱の three.js、three-vrm は MIT ライセンスです（`gfx/web/vendor/` を参照）。
- VRM meta は既定で最も制限的な許諾（クレジット表記必須、再配布禁止、改変禁止）を書き込みます。
- **変換する前に、そのモデルを使う権利があるか確認してください。** 多くの MMD モデルは再配布を禁止しており、変換した VRM も公開配布できません。
