# VRMify — PMX / MMD to VRM converter

Converts MMD **PMX models to VRM 1.0 / 0.x** avatars, ready for Unity (UniVRM), VSeeFace, VTuber apps, three-vrm and
other VRM games. FBX, unitypackage, uemodel, psk, xps and the archives model sites hand out convert directly too.

Comes with a desktop UI for batch conversion, a 3D preview and post-conversion tuning. The converter uses only the
Python standard library — no Blender, Unity or plugins.

English | [简体中文](README_SC.md)

## Download

Get `VRMify-v<version>-win64.zip` from [Releases](https://github.com/JiGuang283/VRMify/releases/latest),
unzip it and run `VRMify.exe`. Nothing to install; delete the folder to uninstall.

- The exe isn't code-signed, so Windows may show "Windows protected your PC" the first time: click
  "More info → Run anyway".
- The window uses the system WebView2 (built into Windows 11, present on almost every Windows 10); without it the UI
  opens in your browser.

## Features

**Conversion**

- **Skeleton**: MMD standard bone names (and common English ones) map to the VRM humanoid; MMD-specific structures
  (grant / 付与 bones, hierarchy) are fixed up, and A-poses are raised to the T-pose VRM requires.
- **Materials**: MToon toon shading. Shadow color and the light/shadow boundary come from the PMX toon textures;
  opaque / cutout / transparent is decided from the textures.
- **Expressions**: common morphs map to VRM presets (blink, aa/ih/ou/ee/oh); emotion presets combine brow, eye and
  mouth morphs like VRoid so the whole face changes. Besides MMD's Japanese names, VRoid, VRChat, ARKit and common
  Chinese names are recognized. Material, bone and UV morphs are converted too; gaze expressions are added when the
  model has eye bones, plus hideHighlight (eye highlights off) and tongue under fixed names.
- **Physics**: PMX rigid bodies and joints become VRM 1.0 SpringBone, with hair, skirts and ribbons tuned to feel like
  native VRM models; models without physics get chains from their bone names.
- **Other formats**: FBX / unitypackage, uemodel (UEFormat), psk / pskx (Unreal ActorX) and xps (XNALara) go through
  PMX to VRM. Add a zip / 7z / rar as is: the models inside are found and converted, and props or outfit-only models
  are skipped.
- **Compatibility**: output passes the official VRM JSON Schema and avoids data that makes UniVRM imports abort.

**UI**

- **Batch**: add many files or folders; several models convert at once.
- **Preview**: three-vrm (same MToon / SpringBone rules as UniVRM) with idle, walk and run motions, adjustable
  lighting, expression sliders and screenshots, plus a skeleton overlay (coloured by humanoid / weighted / physics /
  unused, with bone name and parent on hover). The wheel zooms toward the pointer and a double-click makes that
  point the orbit centre; R (or Home, or a double-click on the background) resets the view. PMX files preview natively with MMD shading and physics.
- **Automatic thumbnail**: in the UI, the face is captured after converting and written into the VRM (can be
  turned off; a thumbnail set by hand is kept).
- **Tuning after conversion**: change the name, thumbnail, which morph each preset uses and its strength, gaze range,
  and physics stiffness / gravity / per-chain switches without converting again (hovering a chain shows it and the
  colliders it hits in the preview; a chain can be kept shown or framed by the camera); saving writes straight into the VRM. Tuning is remembered per source file and reapplied the next time it converts.
- English / Chinese interface.

## Usage

1. Drag model files, folders or archives into the window (or click "Add files").
2. Adjust settings if needed and click "Start conversion". Output goes to a `vrm` folder next to the program by default, or
   next to the source, or a folder you choose.
3. Click a file's preview button to look at the result, and tune it in the "Tune" panel if needed.

Settings tabs:

| Tab | Contents |
|---|---|
| Convert | Convert to VRM 1.0 / VRM 0.x / PMX only; characters only; physics, expressions; file size (smaller textures, often halving the file); where to save. Physics and expression details, fixes for display problems and how many to convert at once sit under "Advanced" |
| Other formats | Options for FBX, uemodel, psk and xps: facing, texture alpha… |
| Metadata | Author, model version and license, set once for every model; defaults to the most restrictive terms (credit required, no redistribution, no modification) |

**Archives**: zip uses the built-in extractor and restores GBK / Shift-JIS file names from old archives; 7z / rar use
7-Zip, WinRAR or Bandizip if installed, otherwise the `tar` that ships with Windows (Windows 10's can't open some 7z
files — install 7-Zip then). Encrypted archives aren't supported.

## Running from source

Python 3.10 or newer.

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt    # optional: pywebview (own window, drag & drop), Pillow (faster textures, slimming)
```

Double-click `启动.bat` (files can be dropped onto it) or run `python ui/app.py [files ...]` (`--browser` opens the UI in
the system browser). Without the optional packages conversion still works; the UI opens in the browser, texture
handling is slower and texture slimming is unavailable.

### Command line

```bash
python convert/pmx2vrm.py "model.pmx" -o "model.vrm"            # VRM 1.0
python convert/pmx2vrm.py "model.pmx" --spec 0x                 # VRM 0.x
python convert/fbx2pmx.py "model.fbx" -o "model.pmx"            # other formats → PMX, then VRM
```

Options and conversion rules: [PMX → VRM](docs/pmx-to-vrm.md) and [Other formats → PMX](docs/other-formats.md)
(in Chinese).

## FAQ

**Unity / a game hangs while importing**: usually UniVRM hit invalid data. Check the file with
`python tools/qa/check_vrm.py file.vrm`; a Unity app's log at
`%USERPROFILE%\AppData\LocalLow\<company>\<product>\Player.log` names the exception.

**The face looks flat / has no shadows**: faces on native VRM models such as VRoid are mostly flat too; shading comes
from cast shadows (hair etc.). Shadow color comes from the PMX toon textures — check with the preview's lighting angle
and shadow switch.

**Hair or skirts jitter / clip**: SpringBone is an approximation, not MMD's physics. Adjust stiffness and gravity
overall or per chain in the Tune panel.

**Holes or black edges**: look at the winding line in the conversion log; for thin hair or skirt sheets that rely on
double-sided rendering, turn on "All materials double-sided" under Advanced on the Convert tab.

**An FBX faces away or its transparency is wrong**: turn on "Extra 180° turn" or try another "Texture alpha" mode on
the Other formats tab; see [Other formats → PMX](docs/other-formats.md).

## Known limitations

- VRM 0.x gets no SpringBone (physics is VRM 1.0 only).
- Physics is approximate: the cross joints that tie neighbouring skirt chains together in PMX can't be expressed, so
  a skirt moves less as one piece than in the original.
- Multiply sphere maps and shared toon07–10 have no equivalent; outlines are off by default.
- Expression presets are matched by name; models whose names aren't recognized need presets assigned in Tune.
- PMX from FBX keeps English bone names; XPS has no morphs.

## Development

```
convert/            converters: pmx2vrm.py (PMX → VRM), vrmconv/ (its stages), fbx2pmx / uemodel2pmx / psk2pmx / xps2pmx,
                    pipeline.py (the UI's conversion flow), parallel.py (worker processes), vrm_edit.py (tuning)
formats/            format I/O: pmxio, vrmio (GLB), fbx_reader, uemodelio, pskio, xpsio, archive (extraction)…
ui/                 desktop UI: app.py (entry), server.py (backend), web/ (pages), viewer/ (three-vrm preview)
tests/              unit tests (on small models built by synth.py)
tools/              batch conversion, VRM checks, physics tests, real-model regression, release build (tools/README.md)
docs/               conversion notes
```

Modules import each other by bare name (`import pmxio`); entry scripts put `formats/` and `convert/` on `sys.path`.

```bash
pip install -r requirements-dev.txt          # jsonschema, pyinstaller, …
python -m unittest discover -s tests         # unit tests
python tools/regress.py record <model dir>   # record real-model results (kept out of the repo)
python tools/regress.py check                # re-convert and compare after code changes
python tools/build_release.py                # build → dist/VRMify-v<version>-win64.zip
```

Releasing: bump `ui/version.py` → commit → push a `v<version>` tag. GitHub Actions tests, builds, smoke-tests the exe
and creates a draft release.

## License

- [GPL-3.0](LICENSE). Based on [SaraKale/Model-To-PMX](https://github.com/SaraKale/Model-To-PMX).
- three.js and three-vrm used by the preview are MIT-licensed (see `ui/viewer/vendor/`).
- **Make sure you're allowed to use a model before converting it.** Many MMD models forbid redistribution, and the
  converted VRM is bound by the same terms.
