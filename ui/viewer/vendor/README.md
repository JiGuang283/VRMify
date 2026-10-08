# 第三方前端库（离线预览用）

| 库 | 版本 | 许可证 | 来源 |
|---|---|---|---|
| three.js（`three.module.min.js`，`addons/` 下的 GLTFLoader、BufferGeometryUtils、OrbitControls） | 0.169.0 | MIT（`three/LICENSE`） | npm `three` |
| three.js 的 MMD 组件（`addons/loaders/MMDLoader.js`、`TGALoader.js`，`addons/libs/mmdparser.module.js`，`addons/shaders/MMDToonShader.js`，`addons/animation/MMDAnimationHelper.js`、`CCDIKSolver.js`、`MMDPhysics.js`） | 0.169.0 | MIT（`three/LICENSE`） | npm `three` 的 `examples/jsm` |
| ammo.js（Bullet 物理的 WebAssembly 版，`addons/libs/ammo.wasm.js` + `ammo.wasm.wasm`，PMX 预览的 MMD 物理用） | three 0.169.0 附带的版本 | zlib（`ammo/LICENSE`） | npm `three` 的 `examples/jsm/libs` |
| @pixiv/three-vrm（`three-vrm.module.min.js`） | 3.5.5 | MIT（`three-vrm/LICENSE`） | npm `@pixiv/three-vrm` |

升级时和 `tools/qa/harness/package.json` 保持同一版本，预览和动态测试才是同一套物理。
three.js 在 0.169 之后的版本里把 MMD 组件标成了弃用，升级 three 时要另找 PMX 的加载方式。
