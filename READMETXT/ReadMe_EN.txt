Model-To-VRM

Converts MMD PMX models to VRM 1.0 / 0.x for Unity (UniVRM), VSeeFace and other VRM-capable apps and games.
FBX / unitypackage, uemodel, psk / pskx and xps can be converted to PMX first, then to VRM.

Conversions:
PMX → VRM (1.0 / 0.x): skeleton, expressions, MToon materials, SpringBone physics (1.0 only)
FBX / unitypackage → PMX
uemodel → PMX
psk / pskx → PMX
XPS → PMX

How to use:
1. Run ModelConvert.exe
2. Drop files onto the window, or click the drop area to choose files. Dropping only loads and previews;
   click "Start conversion" to convert.
3. The Task menu picks the direction from the extension by default: .pmx becomes VRM, other formats become PMX.
4. The VRM tab selects the VRM version (1.0 / 0.x).
5. After converting, click "3D preview (three-vrm)": motions, physics, lighting and expressions look as they will in apps.

---------------------------

Notes:

- Importing into Unity / a game hangs while loading: usually invalid data. The app's log is at
  %USERPROFILE%\AppData\LocalLow\<company>\<product>\Player.log and contains the error.
- Faces look pale / have no shadows: faces of native VRM models such as VRoid are mostly flat; shadows come mainly
  from hair cast shadows (pink). Check with the light angle and shadow toggle in the 3D preview.
- Hair / skirts jitter or clip: physics is an approximation; skirts overlap the upper thigh a little on high leg raises,
  as native models do. Fine-tune each chain in UniVRM's SpringBone after importing into Unity.
- Holes: check the "winding" line in the log; if it's wrong use --flip-winding. Thin hair / skirts that need
  double-sided rendering: --force-double-sided, or tick "Force double-sided materials" in the GUI.
- FBX faces away from the camera: keep "auto-detect facing" on (default) in the FBX tab; if still wrong, tick "Force an extra 180° turn".
- FBX transparency is wrong: whole-mesh translucency or ghosting → set texture alpha to "Strip all";
  lace / hair tips turned hard-edged → "Decide per texture" or "Keep as-is". Source textures are never modified.
- FileNotFoundError: Windows limits paths to 260 characters. This is handled automatically; with older versions
  move the files closer to the drive root (e.g. D:\model\).
- MMD can't load the PMX: MMD only reads UTF-16LE PMX files. This tool always writes UTF-16LE.
- PmxEditor shows "アンカーデータの読み込みに失敗しました": PmxEditor treats a .psk with the model's name as anchor data.
  Click OK; the model loads normally.
- Drag & drop works on Windows only; on other systems use the Browse button.
- config.json stores language / UI scale / options; delete it to restore defaults.

Known limitations:

- VRM 0.x has no SpringBone output.
- PMX joints between physics chains (common in skirts) can't be expressed in SpringBone and are dropped.
- Multiplicative sphere maps (.sph) have no MToon equivalent and are ignored.
- Textures: .dds (DXT1 / DXT3 / DXT5) is decoded; KTX2 / WebP are not supported and fall back to a flat color.
- FBX keeps English bone names (rename before using MMD .vmd motions); XPS has no morphs.

Copyright:
The VRM license fields default to the most restrictive terms: credit required, no redistribution, no modification.
Make sure you may use a model before converting it. Many MMD models forbid redistribution, and that applies to the VRM too.

---------------------------

This project is derived from SaraKale's Model-To-PMX (GPL-3.0) and since 2026-10 focuses on VRM conversion.
Original project: https://github.com/SaraKale/Model-To-PMX
Original icon design and testing: 沙拉酱, 小桃子

---------------------------

History:

2026-10 Model-To-VRM: reworked PMX → VRM (bone mapping and T-pose, expressions, MToon shading from toon ramps,
  PMX physics → SpringBone, passes the official VRM schemas); added the three-vrm 3D preview;
  removed the VRM → PMX and PMX → uemodel / psk / FBX outputs.

Original Model-To-PMX history:
2026-10-05 v1.1.6: Fixed the path length limit; fixed unitypackage metadata files overwriting each other on unpack.
2026-10-02 v1.1.5: Added version checking; fixed some crashes when loading VRM→PMX results into MMD.
2026-10-01 v1.1.4: Added XPS → PMX.
2026-09-27 v1.1.3: Fixed uemodel → PMX scale; added auto-alignment to a reference model in the UE tab.
2026-09-25 v1.1.2: Added PMX → FBX and PMX → psk / pskx; output goes to a same-name folder.
2026-09-24 v1.1.1: Added psk / pskx → PMX; removed toon.
2026-09-23 v1.1.0: Fixed some FBX → PMX issues.
2026-09-21 v1.0.9: Fixed uemodel (ZSTD) → PMX.
2026-09-19 v1.0.8: Added .uemodel conversion; fixes.
2026-09-18 v1.0.7: Fixed some VRM → PMX issues.
2026-09-18 v1.0.5–v1.0.6: Fixed PMX → VRM issues.
2026-09-18 v1.0.4: Fixed MMD's UTF-16 loading error and the convert button logic; added the file list.
2026-09-17 v1.0.3: Fixed toppled unitypackage skeletons.
2026-09-15 v1.0.2: Fixed the model preview.
2026-09-13 v1.0.1: Fixed the model preview.
2026-09-13 v1.0.0: First release.
