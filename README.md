# Model-To-VRM (Pure Python)

Convert MMD **PMX models to VRM 1.0 / 0.x**, ready for Unity (UniVRM), VSeeFace, three-vrm and other VRM-capable apps and games.
FBX / unitypackage, uemodel (UEFormat), psk / pskx (Unreal ActorX) and xps (XNALara) can be converted to PMX first, then to VRM.

**Python standard library only**: no Blender, Unity, mmd_tools or UniVRM — binary formats are read and written directly.

[English](README.md) | [简体中文](README_SC.md)

> This project is derived from [SaraKale/Model-To-PMX](https://github.com/SaraKale/Model-To-PMX) (GPL-3.0).
> Modified since 2026-10: PMX → VRM was reworked (skeleton, expressions, MToon materials, SpringBone physics),
> and the VRM → PMX, PMX → uemodel / psk / FBX outputs were removed.

---

## 1. Features

### PMX → VRM

| Area | What it does |
|---|---|
| **Skeleton** | Maps standard MMD bone names (`下半身`, `左腕`, `左人指２` …) to the VRM humanoid; merges 100 % append bones (`足D` etc.) into their parents; fixes the humanoid hierarchy; adds placeholder bones for missing required slots |
| **Pose** | Raises A-pose arms to the T-pose VRM requires (bones and vertices together) |
| **Coordinates** | PMX (left-handed) → glTF (right-handed) via one mirror, with automatic winding detection — no mirrored or see-through models |
| **Materials** | MToon. Shade color and shading boundary come from the PMX toon ramp (pink skin shadows, like native VRoid models); colors written in linear space; opaque / cutout / blend chosen from the texture's alpha; additive sphere maps → matcap |
| **Expressions** | Vertex morphs → morph targets (sparse, small files); common ones mapped to VRM presets (blink, aiueo); emotion presets combine brow, eye and mouth morphs like VRoid (e.g. angry = 怒り + ｷﾘｯ + 口角下げ) so the whole face changes, also for models that only have ARKit shapes (common on VRChat); material morphs → material color binds (show / hide parts); bone morphs (寄り目 etc.) baked into vertex morphs; UV morphs (emoticon sprites) → texture offsets; gaze expressions lookUp / lookDown / lookLeft / lookRight added when the model has eye bones |
| **Physics** | PMX rigid bodies / joints → VRM 1.0 SpringBone, tuned to feel like native VRM models; colliders from the PMX bone-following rigid bodies. Models with no physics at all (common for FBX) get it from bone names and skeleton structure: hair, skirts, sleeves, ribbons and tails swing, with body colliders added |
| **Textures** | Optional max size (4096 / 2048 / 1024) and JPEG for opaque textures — VRM files usually shrink by half or more (needs Pillow); textures with transparency stay PNG |
| **Metadata** | Author, version, contact and usage license (who may use it, commercial use, modification, redistribution, credit) set in the UI; conservative defaults |
| **Compatibility** | Output passes the official VRM JSON schemas and avoids data that aborts UniVRM imports (duplicate blend shape names, missing color components …) |

Conversion details: [Document/PMX转VRM_使用说明.md](Document/PMX转VRM_使用说明.md) (Chinese).

### Other formats → PMX

| Input | Notes |
|---|---|
| FBX / unitypackage | Binary and ASCII FBX 7.x; automatic facing detection, texture alpha-mask handling, skips game effect sheets. When the FBX has no texture file names (common for Unity assets), textures are found through the Unity materials (.mat); English bone names (HumanIK / Mixamo / VRoid / Blender / 3ds Max) and expression names (VRChat, VRoid, `MTH_A`-style) map to VRM automatically; overlapping alternate meshes (hand poses, an open-mouth head) show one and become toggle expressions. When converting to VRM, unpacked assets and the intermediate PMX stay in a temp folder, so the output folder only gets VRMs; in a package with several models, an outfit / accessory-only one (no mesh on the head or hands) is skipped |
| uemodel (UEFormat v1–v10) | FortnitePorting / FModel exports; split parts can be aligned to a full-body reference |
| psk / pskx (Unreal ActorX) | Weights, MRPH vertex morphs, extra UVs; Bip001 bone names → standard MMD Japanese names |
| xps (XNALara binary) | Axis conversion, standard Japanese bone names, IK bones, texture alpha kept |

The resulting PMX can then be converted to VRM.

### 3D preview

View results with three-vrm (the same MToon / SpringBone rules as UniVRM): idle / walk / run / leg-raise motions,
adjustable lighting and shadows, expression sliders, snapshots. PMX files are shown natively with three.js's MMD
components (MMD shading + MMD physics), not through VRM. Works offline.

### Tuning after conversion

The **Tune** panel (right of the preview; opens by itself when the previewed model can be tuned — convert a PMX first) adjusts one model after conversion — **no reconversion**; saving writes straight
into the output VRM:

- **Name** and **thumbnail** (a square shot of the current preview, shown as the avatar icon in games / VRM apps)
- **Preset expressions**: which PMX morph drives each VRM preset (happy, blink, mouth A/I/U/E/O …). Automatic mapping only knows
  the standard Japanese morph names, so models with other names can be completed here; ▶ tries an expression.
  Combined emotion presets show as "怒り+ｷﾘｯ+口角下げ"; switch back to a single morph if you prefer (the combination stays as a custom expression).
  The slider next to each preset is its **strength** (0–150%, previewed while dragging): morph weights are scaled and capped at 100% each,
  so going above 100% only helps combined presets
- **Gaze range**: how far the eyes turn left/right, down and up at the edge of their range (computed from the eye shape by default);
  the arrows preview it. Changes both VRM look-at (looking at the camera etc.) and the lookLeft etc. expressions added at
  conversion (those go up to 40° left/right, 30° up/down)
- **Physics**: overall stiffness / drag / gravity plus per-chain stiffness, gravity and on/off, previewed live while dragging

Tuning is stored per source file in `edits.json` (thumbnails in `edits/`) and reapplied on later conversions; "Reset all"
returns to the converted state. Re-tuning never drifts: the original values are stored in the file on the first change and
every save starts from them.

---

## 2. Requirements

- **Python 3.10+** (the converter itself is standard library only; without pywebview the file dialogs use tkinter,
  included in the official Windows installer)
- **Optional** (`pip install -r requirements.txt`): `pywebview` (native window with drag & drop; otherwise the system browser is
  used), `Pillow` (texture slimming)

---

## 3. Usage

### GUI

**Double-click `启动.bat`** ("start"; you can also drop files onto it). It uses the project's `.venv` when present, otherwise
the Python on PATH. Setting up the environment once is recommended:

```bash
uv venv .venv && uv pip install -r requirements.txt     # or python -m venv .venv, then .venv\Scripts\pip install -r requirements.txt
```

From a terminal: `python ui/app.py [files or folders ...]` (`--browser` forces the system browser).

- Left: add files / folders (PMX, VRM, archives, FBX, unitypackage, uemodel, psk, xps) and click "Start conversion" at the bottom
  ("Convert again" once everything is done).
  Settings have four tabs:
  - **VRM**: version, physics, expressions, outline, texture max size and JPEG
  - **Inputs**: options for FBX, uemodel, psk and xps
  - **Metadata**: author and usage license (the name defaults to the model file name)
  - **Output**: convert non-PMX formats to VRM (default, via PMX) or stop at PMX; save to the app folder (default: a `vrm`
    folder next to the program) / next to the source / a chosen folder, optional per-run subfolder
- Right: three-vrm preview. Previewing a file that hasn't been converted yet builds a temporary VRM in the background first;
  after conversion the real output is shown. PMX files (added PMX files, PMX outputs, "PMX only" results) are previewed
  natively: MMD toon shading, sphere maps, morphs and live MMD physics (ammo.js); models with standard bone names
  (Japanese MMD or common English names) also play the idle / walk motions. The Light, Expressions, Tune and Info panels open in a column to the right of the preview (one at a time); the preview narrows
  instead of being covered. See [Tuning after conversion](#tuning-after-conversion) for the Tune panel.
- **Archives from model sites can be added directly** (zip / 7z / rar / tar.gz …; add the first part of a split archive):
  they are extracted to a temp folder, the PMX inside is converted (other formats if there is no PMX; one nested archive level
  is unpacked too) and the output goes next to the archive. Zip uses the built-in extractor and restores legacy GBK / Shift-JIS
  (even mixed) file names, so textures resolve; 7z / rar use 7-Zip / WinRAR / Bandizip if installed, otherwise Windows' own tar
  (Windows 11 handles 7z and rar; the Windows 10 version can't read LZMA 7z and fails on file names outside the system code
  page — install 7-Zip then). Password-protected archives are not supported.
- Chinese / English UI; settings are saved to `settings.json`.
- In a plain browser dropped files have no path — use "Add files". The app exits a minute after the page is closed.

### Command line

Quote paths that contain spaces or non-ASCII characters.

```bash
# PMX → VRM 1.0 (SpringBone on by default)
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"

# VRM 0.x (for older apps; 0.x has no SpringBone output)
python convert/pmx2vrm.py "model.pmx" --spec 0x --title "Name" --author "Author"

# 3D preview
python ui/app.py "model.vrm"
```

Common `pmx2vrm.py` options:

| Option | Meaning |
|---|---|
| `--spec 1.0\|0x` | VRM version, default 1.0 |
| `--scale auto\|factor` | Default normalises height to 1.6 m |
| `--keep-pose` | Keep the original pose (default: A-pose → T-pose) |
| `--no-spring` / `--no-spring-skirt` | No physics / no skirt-type physics |
| `--spring-stiffness k` | Overall SpringBone stiffness multiplier, default 1.0 |
| `--spring-gravity g` / `--spring-gravity-skirt g` | Gravity for hair etc. / skirts, default 0 / 0.1 |
| `--no-auto-spring` | Don't add physics automatically when the model has none |
| `--no-spring-center` | Don't use the model root as center (by default whole-body movement doesn't fling the hair) |
| `--outline` | Write MToon outlines from the PMX edge settings (off by default) |
| `--force-double-sided` | Make every material double-sided |
| `--flip-winding` | Force-reverse triangle winding (detected automatically by default) |
| `--dense-morphs` / `--max-morphs N` | Dense morph storage / export only the first N morphs |
| `--no-combo-expr` | Don't combine brow / eye / mouth morphs into emotion presets (one PMX morph per preset) |
| `--no-gaze` | Don't add gaze expressions (by default lookUp / lookDown / lookLeft / lookRight are made from the eye bones) |
| `--tex-max N` / `--tex-jpeg` | Texture max size / store opaque textures as JPEG (needs Pillow) |
| `--title` / `--author` | Written to the VRM meta |

Other formats → PMX:

```bash
python convert/fbx2pmx.py "model.fbx" -o "model.pmx"            # --alpha keep|auto|strip, --face-180
python convert/uemodel2pmx.py "model.uemodel" -o "model.pmx"    # --fbx also writes an FBX
python convert/psk2pmx.py "model.pskx" -o "model.pmx"           # --raw-bone-names, --no-ik
python convert/xps2pmx.py "model.xps" -o "model.pmx"            # --show-unused, --one-sided
python convert/pmx_check.py "model.pmx"                         # PMX check + preview image
python formats/unitypackage_unpack.py "pack.unitypackage"       # unpack a unitypackage
```

### Batch conversion, checks and tests

`tools/` contains batch conversion, VRM structure / official schema checks, a SpringBone dynamics test and a regression
comparison on real models — see [tools/README.md](tools/README.md):

```bash
python tools/batch_convert.py model -o vrm           # each character with and without SpringBone
python tools/qa/check_vrm.py vrm                     # needs: pip install jsonschema
python tools/qa/dynamics.py vrm                      # needs Node.js
python -m unittest discover -s tests                 # unit tests (synthetic model, no real models needed)
python tools/regress.py record model/some-character  # record real-model outputs as a baseline
python tools/regress.py check                        # after code changes: reconvert and compare
```

---

## 4. Layout

```
Model-To-VRM/
├── 启动.bat                double-click launcher for the GUI
├── requirements.txt        optional packages (pywebview, Pillow)
├── Model-To-VRM.spec       PyInstaller spec
├── convert/
│   ├── pmx2vrm.py          PMX → VRM entry (convert() and CLI)
│   ├── vrmconv/            PMX → VRM stages
│   │   ├── humanoid.py     bone-name mapping, append-bone merge, hierarchy fixes, T-pose
│   │   ├── skeleton.py     bone preprocessing, glTF node tree, placeholder bones
│   │   ├── mesh.py         vertex weights, per-material primitives
│   │   ├── materials.py    MToon materials (toon ramps, alpha modes)
│   │   ├── expressions.py  expressions
│   │   ├── morphbake.py    bone / gaze morphs baked into vertex morphs, UV morphs → texture offsets
│   │   ├── springbone.py   physics → SpringBone
│   │   ├── textures.py     texture slimming (resize, JPEG)
│   │   └── vrm_ext.py      VRM extension bodies
│   ├── fbx2pmx.py  uemodel2pmx.py  psk2pmx.py  xps2pmx.py   other formats → PMX
│   ├── pmx_check.py        PMX check
│   ├── pipeline.py         conversion flow: detect format → other formats to PMX → VRM (used by the GUI)
│   └── vrm_edit.py         tuning after conversion: expression presets, physics, name, thumbnail
├── formats/                format I/O (pmxio, vrmio (GLB), fbx_reader, uemodelio, pskio, xpsio …), archive (7z / rar / tar), longpath (Windows long paths)
├── ui/                     GUI
│   ├── app.py              entry point (pywebview window / browser)
│   ├── server.py           backend: file list, options, conversion jobs, local HTTP API
│   ├── web/                page (HTML / CSS / JS, Chinese and English)
│   └── viewer/             three-vrm viewer component viewer-core.js; vendor/ holds offline three.js and three-vrm
├── tests/                  unit tests (synth.py builds a small test model)
├── .github/workflows/      GitHub Actions: tests on push, release build on tags
└── tools/                  batch conversion, QA, regression comparison (not bundled)
```

Modules import each other by bare name (`import pmxio`); `ui/server.py` and the command-line scripts put `formats/` and `convert/` on `sys.path` at runtime.

---

## 5. Building and releases

Releases are a **portable zip**: unzip and double-click `Model-To-VRM.exe`, nothing to install; delete the folder to uninstall.

```bash
pip install -r requirements-dev.txt     # pyinstaller + pywebview + Pillow
python tools/build_release.py           # → dist/Model-To-VRM-v<version>-win64.zip (folder only: pyinstaller Model-To-VRM.spec)
```

- The **version** lives only in `ui/version.py`; the UI header, the exe's Properties → Details and the zip name all read it.
- **Always build with the spec**: the sub-directory modules are added to `sys.path` only at runtime, which PyInstaller's static
  analysis can't see; the spec sets `pathex`, `hiddenimports` and the page assets (`ui/web`, `ui/viewer`). Install pywebview and
  Pillow in the build environment so the exe gets the native window and texture slimming.
- **Settings** (`settings.json`, `edits.json`) are written next to the exe, or to `%APPDATA%\Model-To-VRM` when that folder isn't
  writable (e.g. under `C:\Program Files`).
- The **native window** uses the system WebView2 (built into Windows 11, nearly always present on Windows 10); without it, or if the
  window fails to open, the UI opens in the browser instead.
- The exe is not code-signed, so Windows SmartScreen warns on first run: "More info → Run anyway".
- The zip includes `LICENSE` (GPL-3.0) and the READMEs.

**GitHub Actions** (`.github/workflows/`):

| Workflow | Runs on | Does |
|---|---|---|
| `test.yml` | pushes to main that touch code; manual | unit tests on Linux (incl. official VRM schema checks), about a minute |
| `release.yml` | `v*` tags; manual | tests on Windows → `build_release.py` → `smoke_exe.py` runs the built exe on a few synthetic models → for tags a **draft** release with the zip; manual runs only upload the zip as an artifact |

To release: bump `ui/version.py` → commit → `git tag v0.1.0` → `git push origin main v0.1.0` → review the draft under Releases
and publish. The build fails if the tag and `ui/version.py` disagree.

---

## 6. Troubleshooting

### Importing into Unity / a game hangs while loading

Usually UniVRM hit invalid data and threw. Check the file with `tools/qa/check_vrm.py`; Unity apps log to
`%USERPROFILE%\AppData\LocalLow\<company>\<product>\Player.log`, which contains the exception.

### Faces look too pale / have no shadows

Faces of native models such as VRoid are mostly flat; their shadows come mainly from hair **cast shadows** (pink). These follow
the PMX toon ramp; materials without one (eyes, brows …) get no shadow. Check with the light angle and shadow toggle in the 3D preview.

### Hair / skirts jitter or clip

SpringBone is an approximation, not MMD's Bullet physics. Adjust overall with `--spring-stiffness`, or per chain in UniVRM.
Skirts overlap the upper thigh a little on high leg raises — native models do too.

### Holes / black edges

See the "winding" line in the log. If it's wrong use `--flip-winding`; thin hair / skirts that rely on double-sided rendering need `--force-double-sided`.

### `FileNotFoundError` (path too long)

Windows limits paths to 260 characters, which deep unitypackage trees easily exceed. The tool switches to `\\?\` long paths automatically;
with older versions move the file closer to the drive root.

### FBX faces away / wrong transparency

FBX has no "front" field; facing is detected from the toes and the face mesh — add `--face-180` if it's wrong. Texture alpha defaults to
`--alpha auto` (per-texture mask detection); use `strip` for whole-mesh translucency, `keep` if lace / hair tips turn hard-edged.

### MMD can't load the PMX (encoding)

MMD only reads UTF-16LE PMX files. This tool always writes UTF-16LE; convert old files with `python formats/pmxio.py "old.pmx"`.

### PmxEditor shows "アンカーデータの読み込みに失敗しました"

PmxEditor auto-loads a `.psk` with the model's name as anchor data, but `.psk` is also the Unreal mesh extension. Click OK — the model loads fine;
rename the PMX or disable `[ファイル] → [アンカーデータの自動読み込み／保存]` to stop it.

---

## 7. Known limitations

- **VRM 0.x has no SpringBone output** (physics is written for VRM 1.0 only).
- **Physics**: PMX joints between chains (common in skirts) can't be expressed in SpringBone and are dropped. The converter
  turns the legs' push on each skirt chain toward the panel's facing, so legs no longer split a skirt down the middle, but the
  panel still doesn't move as one piece like the original.
- **Materials**: multiplicative sphere maps (.sph) and shared toon07–10 have no equivalent; outlines are off by default.
- **Expression presets**: mapped and combined by morph name (MMD Japanese names, VRoid / VRChat English names, ARKit, common
  Chinese names); models whose names match none of these can't be combined (assign them in Tune; the "表情预设" log line lists
  what's missing), and the combination weights are fixed, so a few models may look a bit strong or weak.
- **Other formats**: FBX keeps English bone names (rename before using MMD motions); XPS has no morphs; PMX allows at most 4 bone weights per vertex.
- **Textures**: KTX2 / WebP are not supported and fall back to a flat color.

---

## 8. License and copyright

- Released under **GPL-3.0** ([LICENSE](LICENSE)); original work by SaraKale and contributors.
- three.js and three-vrm bundled with the 3D preview are MIT-licensed (see `ui/viewer/vendor/`).
- The VRM meta defaults to the most restrictive terms: credit required, no redistribution, no modification.
- **Make sure you are allowed to use a model before converting it.** Many MMD models forbid redistribution, and that applies to the converted VRM too.
