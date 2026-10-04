Model Converter

You can convert and output various model format files to .pmx files.

Supported conversion directions:
FBX / unitypackage → PMX
VRM (0.x / 1.0) → PMX
PMX → VRM (0.x / 1.0)
uemodel → PMX
PMX → uemodel
psk / pskx → PMX 
PMX →FBX
PMX → psk / pskx
XPS → PMX

If you need the macOS or Linux versions, please download them here:
https://github.com/SaraKale/Model-To-PMX/releases

Usage:
1. Double-click to run ModelConvert.exe
2. Drag and drop your file here, or click the drag-and-drop area to select a file.
3. The "Task" dropdown box allows manual specification of the conversion direction, with automatic determination by file extension as the default.
4. The conversion results and logs are located at the bottom left, while the model preview occupies the entire right column.

---------------------------

Notes:

- Black lines along the model edges: this tool turns off MMD outlines by default when
  writing PMX. If you still see black edges, select all materials in MMD / PMXEditor,
  make sure the outlines are off and tick "Double-Sided Drawing".
- Still hollow: first check the "winding auto-detect" line in the log. If it guessed wrong,
  use --flip-winding; for thin geometry such as hair or skirt hems that needs two-sided
  rendering, use --force-double-sided, or tick "Force Double-Sided Material" in the UI.
- Model facing away from the camera: FBX has no "which way is front" field, so a wrong
  guess is normal. Tick "Auto-detect facing" on the FBX options page (on by default) and
  the result plus its reason are written to the log. If it is still backwards, tick
  "Force extra 180° turn" (command line --face-180). It only negates X and Z at the same
  time, so the vertex / face / bone counts are unaffected. XPS and PSK use a fixed axis
  mapping and are not affected.
- Transparent parts not transparent: an overall semi-transparent look with ghosting
  usually means the game texture uses alpha as a glow mask. Use --alpha strip ("Remove
  all" in the UI); for everyday use the default auto is recommended, as it judges each
  texture individually. If lace / gauze / hair tips turn into hard edges, the real
  transparency was cut away, so switch back to auto or keep. Whichever mode you choose,
  the original texture is never modified: the alpha-stripped result is written to a
  "name_noalpha.png" copy.
- A grey-white slab covering the body: game FBX files often contain an EffectMesh with no
  material and no texture whose UVs cover the whole atlas. It looks very much like "the
  texture is flipped", but the UVs are perfectly fine. This tool skips every mesh with no
  material by default and prints "skip <mesh name>" in the log.
- FileNotFoundError: Windows limits the total path length to 260 characters by default, so
  a deeply nested path raises this error even though the path in the message looks
  completely normal. This tool now switches to the extended-length form automatically, so
  no registry edit, no admin rights and no reboot are needed. On an older build, please
  update; as a stopgap, put the file near a drive root (e.g. D:\model\) and convert there.
  Note that this is not a non-ASCII path problem: Chinese, Japanese, spaces and brackets
  are all fine, and only the total length matters.
- MMD reports "MMD can't read UTF8 encorded PMX. Please exchange it to UTF16.": MMD only
  accepts UTF-16LE, so seeing this message means the file is actually UTF-8. Every PMX
  written by this tool is UTF-16LE.
- PmxEditor reports "アンカーデータの読み込みに失敗しました": that is a PmxEditor error and
  has nothing to do with the PMX file itself. It parsed the same-named .psk next to the
  model as its own anchor data. Just click OK and the model loads normally. To stop it
  entirely, move the PMX to another folder or rename it, or turn off
  [ファイル] → [アンカーデータの自動読み込み／保存] in PmxEditor.
- Drag and drop works on Windows only; on other systems use the "Browse" button.
- The settings file config.json stores the language / UI zoom / task direction / options
  and is recreated with default values if deleted.

Known limitations:

- FBX: both binary FBX 7.x and ASCII FBX can be read.
- Textures: .dds (DXT1 / DXT3 / DXT5) decodes natively; KTX2 / WebP are not supported and
  degrade to solid colours.
- Physics: PMX rigid bodies / joints ↔ VRM SpringBone are not converted into each other.
- Material effects: .sph / .spa / toon textures are not kept on the VRM side; PMX written
  in every direction uses no Toon at all.
- Bone names: FBX conversion keeps English bone names (Hips, Spine, …), which will not
  match ready-made MMD motions (.vmd), so rename them in bulk to the standard Japanese
  names in PMXEditor. You can use the PE bone-organising plugin I made:
https://www.bilibili.com/video/BV1MfYX6JEhE/
https://www.youtube.com/watch?v=zZj-I4YgIDY
https://www.nicovideo.jp/watch/sm46811122
  PSK takes a different route: the 3dsMax Biped naming (Bip001 …) is converted to the
  standard MMD Japanese names by default, with the English original written into the
  bone's English name field. XPS works the same way — renamed by rule, and names that
  cannot be recognised are kept as they are.
- Expressions: if the source model has no BlendShape / morph, the PMX has 0 expressions
  and they must be created by hand.
- PSK / PSKX: PMX's IK / append (inherit) / rigid bodies / joints / UV morphs / material
  morphs have no counterpart in PSK and are always skipped; a PMX vertex holds at most 4
  bone weights, so a source file with 5–6 loses some on a round trip. The .psk extension
  is also used by PmxEditor for its anchor data (see above).
- XPS / XNALara: only binary .xps is read; older plain-text variants are not supported and
  raise a clear error. The format stores no BlendShape, so the exported PMX has 0
  expressions.

---------------------------

Icon Design and Testing:
沙拉酱、小桃子

Feedback:
If you still encounter issues, please contact me below to provide feedback and a sample file.
https://github.com/SaraKale
https://space.bilibili.com/4197121
https://www.aplaybox.com/u/269621856
https://x.com/SaraKale9

---------------------------

Update history:

2026-10-05 v1.1.6:Fix path length limit; fix metadata files (asset.meta / pathname / preview.png) overwriting each other when unpacking a unitypackage.
2026-10-02 v1.1.5: Added version checking and fixed some crashes after dragging into MMD after VRM→PMX conversion.
2026-10-01 v1.1.4: Added the XPS → PMX option.
2026-09-27 v1.1.3: Fixed the scaling issue after converting uemodel to PMX, and added an option in UE to automatically align to the reference model.
2026-09-25 v1.1.2:Added PMX → FBX and PMX → psk/pskx options; the output files will be saved in a folder with the same name.
2026-09-24 v1.1.1: Added the psk/pskx → PMX option and removed toon.
2026-09-23 v1.1.0: Fixed some issues with FBX-to-PMX conversion.
2026-09-21 v1.0.9: Fixed the uemodel (ZSTD) to PMX conversion issue.
2026-09-19 v1.0.8: Added functionality for converting .uemodel files back and forth, and fixed several issues.
2026-09-18 v1.0.7: Fixed some issues with VRM-to-PMX conversion.
2026-09-18 v1.0.5–v1.0.6: Fixed issues with PMX-to-VRM conversion.
2026-09-18 v1.0.4: Corrected an error when loading MMD files in UTF-16 encoding, fixed the logic of the conversion button, and added a file list box.
2026-09-17 v1.0.3: Fixed the bone inversion issue in the unitypackage.
2026-09-15 v1.0.2: Fixed the model preview interface.
2026-09-13 v1.0.1: Fixed the model preview interface.
2026-09-13 v1.0.0: Released.

by:SaraKale