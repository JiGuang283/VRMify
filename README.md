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
| **Expressions** | Vertex morphs → morph targets (sparse, small files); common ones mapped to VRM presets (blink, aiueo, emotions); material morphs → material color binds (show / hide parts) |
| **Physics** | PMX rigid bodies / joints → VRM 1.0 SpringBone, tuned to feel like native VRM models; colliders from the PMX bone-following rigid bodies |
| **Compatibility** | Output passes the official VRM JSON schemas and avoids data that aborts UniVRM imports (duplicate blend shape names, missing color components …) |

Conversion details: [Document/PMX转VRM_使用说明.md](Document/PMX转VRM_使用说明.md) (Chinese).

### Other formats → PMX

| Input | Notes |
|---|---|
| FBX / unitypackage | Binary and ASCII FBX 7.x; automatic facing detection, texture alpha-mask handling, skips game effect sheets |
| uemodel (UEFormat v1–v10) | FortnitePorting / FModel exports; split parts can be aligned to a full-body reference |
| psk / pskx (Unreal ActorX) | Weights, MRPH vertex morphs, extra UVs; Bip001 bone names → standard MMD Japanese names |
| xps (XNALara binary) | Axis conversion, standard Japanese bone names, IK bones, texture alpha kept |

The resulting PMX can then be converted to VRM.

### 3D preview

View results with three-vrm (the same MToon / SpringBone rules as UniVRM): idle / walk / run / leg-raise motions,
adjustable lighting and shadows, expression sliders, snapshots. Works offline.

---

## 2. Requirements

- **Python 3.10+** with tkinter (included in the official Windows installer)
- **Optional**: `Pillow` (faster texture decoding in the classic preview), `pywebview` (3D preview in its own window; otherwise the system browser is used)
- Drag & drop into the window uses native Windows APIs; on other systems use the Browse button

---

## 3. Usage

### New UI (preview)

```bash
pip install pywebview        # optional: native window + drag & drop; otherwise the system browser is used
python ui/app.py
```

- Left: add files / folders (PMX, VRM, FBX, unitypackage, uemodel, psk, xps), set the VRM version, physics, expressions …
  and click "Start conversion". Non-PMX formats go through PMX to VRM by default; the Inputs tab can stop at PMX.
- Right: three-vrm preview. Previewing a file that hasn't been converted yet builds a temporary VRM in the background first;
  after conversion the real output is shown.
- Chinese / English UI; settings are saved to `settings.json`.
- In a plain browser dropped files have no path — use "Add files". The app exits a minute after the page is closed.

### Classic GUI

```bash
python main.py
```

1. Drop files onto the window (or click the drop area). Dropping only loads and previews — **conversion starts with "Start conversion"**.
2. The task is picked from the extension by default: `.pmx` → VRM, other formats → PMX.
3. The VRM tab selects the VRM version (1.0 / 0.x) and whether to export expressions.
4. After converting, click "3D preview (three-vrm)".

Output goes to a `<name>_<date>_<n>` folder next to the source, so repeated conversions never overwrite each other.

### Command line

Quote paths that contain spaces or non-ASCII characters.

```bash
# PMX → VRM 1.0 (SpringBone on by default)
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"

# VRM 0.x (for older apps; 0.x has no SpringBone output)
python convert/pmx2vrm.py "model.pmx" --spec 0x --title "Name" --author "Author"

# 3D preview
python gfx/web_preview.py "model.vrm"
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
| `--no-spring-center` | Don't use the model root as center (by default whole-body movement doesn't fling the hair) |
| `--outline` | Write MToon outlines from the PMX edge settings (off by default) |
| `--force-double-sided` | Make every material double-sided |
| `--flip-winding` | Force-reverse triangle winding (detected automatically by default) |
| `--dense-morphs` / `--max-morphs N` | Dense morph storage / export only the first N morphs |
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

### Batch conversion and checks

`tools/` contains batch conversion, VRM structure / official schema checks and a SpringBone dynamics test — see [tools/README.md](tools/README.md):

```bash
python tools/batch_convert.py model -o vrm           # each character with and without SpringBone
python tools/qa/check_vrm.py vrm                     # needs: pip install jsonschema
python tools/qa/dynamics.py vrm                      # needs Node.js
```

---

## 4. Layout

```
Model-To-VRM/
├── main.py                 GUI entry point
├── main.spec               PyInstaller spec
├── convert/
│   ├── pmx2vrm.py          PMX → VRM entry (convert() and CLI)
│   ├── vrmconv/            PMX → VRM stages
│   │   ├── humanoid.py     bone-name mapping, append-bone merge, hierarchy fixes, T-pose
│   │   ├── skeleton.py     bone preprocessing, glTF node tree, placeholder bones
│   │   ├── mesh.py         vertex weights, per-material primitives
│   │   ├── materials.py    MToon materials (toon ramps, alpha modes)
│   │   ├── expressions.py  expressions
│   │   ├── springbone.py   physics → SpringBone
│   │   └── vrm_ext.py      VRM extension bodies
│   ├── fbx2pmx.py  uemodel2pmx.py  psk2pmx.py  xps2pmx.py   other formats → PMX
│   ├── pmx_check.py        PMX check
│   └── pipeline.py         conversion flow: detect format → other formats to PMX → VRM (used by the new UI)
├── formats/                format I/O (pmxio, vrmio (GLB), fbx_reader, uemodelio, pskio, xpsio …)
├── ui/                     new UI
│   ├── app.py              entry point (pywebview window / browser)
│   ├── server.py           backend: file list, options, conversion jobs, local HTTP API
│   └── web/                page (HTML / CSS / JS, Chinese and English)
├── gfx/
│   ├── preview.py          classic GUI live preview (pure-Python rasteriser)
│   ├── web_preview.py      standalone 3D preview (local server + window)
│   └── web/                three-vrm viewer component viewer-core.js and preview page; vendor/ holds offline three.js and three-vrm
└── tools/                  batch conversion and QA (not bundled)
```

Modules import each other by bare name (`import pmxio`); `main.py` puts `formats/`, `convert/` and `gfx/` on `sys.path` at runtime.

---

## 5. Building

```bash
pip install pyinstaller
pyinstaller main.spec
```

**Always build with `main.spec`**: the sub-directory modules are added to `sys.path` only at runtime, which PyInstaller's
static analysis can't see; the spec sets `pathex`, `hiddenimports` and the 3D preview assets (`gfx/web`). Rebuild after source changes.

Pushing a `v1.2.3`-style tag triggers [GitHub Actions](.github/workflows/build-app.yml), which builds for Windows / macOS / Linux
(x64 and ARM) and uploads to a draft release.

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
- **Physics**: PMX joints between chains (common in skirts) can't be expressed in SpringBone and are dropped.
- **Materials**: multiplicative sphere maps (.sph) and shared toon07–10 have no equivalent; outlines are off by default.
- **Expression presets**: mapped by standard MMD morph names; brow-only morphs such as `にこり` mapped to happy change little.
- **Other formats**: FBX keeps English bone names (rename before using MMD motions); XPS has no morphs; PMX allows at most 4 bone weights per vertex.
- **Textures**: KTX2 / WebP are not supported and fall back to a flat color.

---

## 8. License and copyright

- Released under **GPL-3.0** ([LICENSE](LICENSE)); original work by SaraKale and contributors.
- three.js and three-vrm bundled with the 3D preview are MIT-licensed (see `gfx/web/vendor/`).
- The VRM meta defaults to the most restrictive terms: credit required, no redistribution, no modification.
- **Make sure you are allowed to use a model before converting it.** Many MMD models forbid redistribution, and that applies to the converted VRM too.
