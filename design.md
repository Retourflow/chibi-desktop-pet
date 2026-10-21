# design.md — chibi-desktop-pet

> 状态（2026-10-21）：导入器、rig 核心、物理、CPU 预览渲染、GPU 渲染、透明桌宠窗口（待机 + 鼠标跟随 + 拖动 + 托盘）已实现；VTube Studio、点击互动、打瞌睡还没做。标「待验证」的地方还没有实际跑过。

## 1. 路线

不用 Cubism、不产出 moc3。自己写一套 Live2D 原理的 2D 网格 rig：

```
参数 (Parameter) ──► 关键形态插值 (keyform blend) ──► 变形器链 (deformer chain) ──► 绘制
      ▲
      ├─ VTube Studio 追踪数据（WebSocket）
      ├─ 鼠标跟随 / 待机动画 / 点击反应
      └─ 物理模拟（头发、尾巴、流苏、手臂）
```

代价：无法在 VTube Studio 里加载模型；大角度转头的质感不如手工 Cubism 绑定。用户会在 v0.1 完成后评估是否转回 Cubism。

## 2. 技术栈

| 层 | 选择 | 状态 |
|---|---|---|
| 语言 | Python 3.11+ | ✓ |
| 网格计算 | numpy（顶点变形全部向量化） | ✓ |
| 素材导入 | psd-tools 读 PSD，或读一个文件夹的整画布 PNG → atlas.png + model.json | ✓（PNG 路径已测试；PSD 路径代码已写，**还没用真实 PSD 测过**） |
| 预览渲染 | 自写 numpy CPU 光栅化（`render/soft.py`），用于测试和预览 GIF | ✓，约 1 秒/帧，只适合离线预览 |
| 实时渲染 | moderngl（OpenGL 3.3）离屏渲染到 FBO → 读回 → 透明 PySide6 窗口 | ✓ 云端（llvmpipe 软件 GL + Xvfb）跑到 61 fps；**Windows 上待验证** |
| 窗口 | PySide6，无边框 + 透明 + 置顶 + 托盘 | ✓ 同上，Windows 上的透明和点击穿透待验证 |
| 面捕 | VTube Studio 插件 API，`ws://localhost:8001` | 计划（T6） |
| 测试 | pytest | ✓ 21 个测试（GPU 测试需要 GL 环境，否则跳过） |

## 3. 代码结构

```
src/chibi_pet/
  importer/
    layers.py   PSD / PNG 文件夹 → Layer(name, 裁切后的 RGBA, 画布偏移)
    mesh.py     按 alpha 生成规则网格（每个有像素的格子 = 2 个三角形）
    atlas.py    shelf 打包成一张纹理图集
    build.py    写出 atlas.png + model.json；CLI: python -m chibi_pet.importer
  rig/
    model.py    读取 model.json
    spec.py     rig spec 里的相对坐标解析（Resolver）
    keyform.py  多线性 keyform 插值
    deformers.py  RotationDeformer / Turn3DDeformer / BendDeformer
    physics.py  摆锤链（Verlet 积分）
    runtime.py  Rig：参数 → 物理 → keyform → 变形器链 → DrawItem 列表
  render/
    soft.py     CPU 光栅化（带 clip mask、premultiplied alpha）
    gl.py       GPU 渲染（moderngl），规则与 soft.py 相同，测试里两者对比
  drive/
    idle.py     呼吸、随机眨眼（偶尔连眨两次）、尾巴和头发的「风」
    mouse.py    头和眼睛跟随鼠标（tanh 饱和 + 指数平滑，眼睛快、身体慢）
  app/
    pet_window.py  透明置顶窗口、拖动、右键/托盘菜单（大小、置顶、退出）
    __main__.py    python -m chibi_pet.app <model_dir>
run_pet.bat     Windows 一键启动：在本文件夹里建 .venv，pip 缓存和临时文件也放在本文件夹
assets/rig/chibi.rig.json   本角色的 rig spec（手写）
tools/make_placeholder.py   从设定图粗拆占位图层（不提交生成结果）
tools/preview.py            渲染预览动画 GIF
```

## 4. 数据格式

### model.json（导入器输出，机器生成）
画布尺寸、atlas 文件、每个部件的 `rect`、`vertices`（画布像素坐标）、`uvs`、`triangles`。格式说明写在 `importer/build.py` 顶部。

### rig spec（手写，`assets/rig/chibi.rig.json`）
- `parameters`：参数 ID、范围、默认值
- `deformers`：变形器树（`parent` 指向父变形器）
- `parts`：每个部件用哪个变形器、深度 `depth`、`keyforms`、`clip`（用哪个部件当遮罩）
- `draw_order`：从后到前；美术里没有的部件会被跳过
- `physics`：摆锤链的输入、输出参数

**关键设计：spec 里的位置和长度都相对部件写**，例如 `{"part": "face", "at": [0.5, 1.0]}` = 脸的底边中点，`{"part": "face", "w": 0.6}` = 脸宽的 0.6 倍。这样同一份 spec 可以同时用于占位图和最终重画的 PSD，换美术不用改坐标。

## 5. 核心算法

**约定**：画布像素坐标，y 轴向下。角度正方向 = 屏幕上的逆时针。

### 5.1 Keyform 插值（`keyform.py`）
每个部件可以有若干 keyform 组。一组绑定 1～2 个参数，在参数的关键值（keys）上各有一个形态（顶点偏移 + 不透明度）。参数取中间值时，对相邻关键值做线性插值；两个参数时是双线性（4 个角加权）。多个组的偏移相加、不透明度相乘。
spec 里的形态用「配方」写（绕 pivot 缩放/旋转 + 平移 + opacity），加载时展开成逐顶点偏移，所以运行时只是数组加权求和。

### 5.2 Turn3D：用深度做假 3D 转头（`Turn3DDeformer`）
每个顶点有深度 z：脸放在一个球面上（`z = sqrt(r² - dx² - dy²) * z_scale`），前发在更外面的球面，后发是负的常数，耳朵接近 0。
转头时把 (x, y, z) 绕头部中心做 yaw（ParamAngleX）和 pitch（ParamAngleY）旋转，再丢掉 z（正交投影）：

```
x' = x·cos(yaw) + z·sin(yaw)
z' = -x·sin(yaw) + z·cos(yaw)
y' = y·cos(pitch) - z'·sin(pitch)
```

z 越大移动越多，所以五官比脸的轮廓移得多、前发比脸移得多、后发反向移动，形成视差。参数 ±30 映射到真实角度 ±15°（`yaw.scale = 0.5`），再大就会明显撕裂。

### 5.3 Bend：链式弯曲（`BendDeformer`）
用于头发、尾巴、流苏、手臂。从 root 到 tip 的直线分成 n 段，每段一个弯曲角（来自物理参数）。累积角度沿链条线性增长、根部为 0，所以根部固定、尖端摆动。顶点用（沿链距离 s，侧向偏移 t）表示，再贴回弯曲后的中心线上。

### 5.4 物理（`physics.py`）
每组是一个 Verlet 摆锤链：根部随输入参数左右移动（`kind: x`，如转头、身体摇摆），或整个参考系随头部旋转（`kind: angle`，让头发在歪头时仍然下垂）。输出每段相对前一段的角度（度）× scale，写进 bend 参数。阻尼按帧率归一化，内部 1/120 秒子步长，保证不同帧率下手感一致。

### 5.5 渲染（`render/soft.py` 和 `render/gl.py` 规则相同）
- atlas 先转成 premultiplied alpha，再做双线性采样，透明边缘不发黑
- 每个部件先画进自己的缓冲区，再整体混合，避免共享边被画两次出现细缝
- clip：例如瞳孔只在眼白范围内显示；遮罩的覆盖度会乘上遮罩自身的不透明度，所以闭眼时瞳孔一起消失
- GPU 版：每个部件一个 VAO（位置缓冲每帧更新，UV 和索引不变）；clip 部件绘制前先把遮罩部件画进单通道 mask 纹理，片元着色器按 `gl_FragCoord` 采样；atlas 生成 mipmap，缩小显示时更平滑
- 同一部件内相邻三角形共享的边，GPU 的光栅化规则保证每个像素只画一次，所以不需要 CPU 版那样的部件缓冲区

### 5.6 窗口（`app/pet_window.py`）
每帧：待机 + 鼠标跟随 → 参数 → 物理 → evaluate → GPU 离屏渲染 → 读回 → `QImage` → `paintEvent` 画进透明窗口。按 `devicePixelRatio` 渲染物理像素，高 DPI 屏幕不糊。拖动只在不透明像素上生效；Windows 上 Qt 的半透明顶层窗口是 layered window，完全透明的像素理论上会让点击穿透到下面的窗口（待验证）。

## 6. 参数表（v0.1）

| 参数 | 范围 | 来源 |
|---|---|---|
| ParamAngleX / Y / Z | -30..30 | VTS `FaceAngleX/Y/Z`，或鼠标跟随 |
| ParamEyeLOpen / ParamEyeROpen | 0..1 | VTS `EyeOpenLeft/Right`，或自动眨眼 |
| ParamEyeBallX / Y | -1..1 | VTS 眼球参数，或鼠标 |
| ParamBrowLY / ParamBrowRY | -1..1 | VTS `Brows` 系列 |
| ParamMouthOpenY | 0..1 | VTS `MouthOpen` |
| ParamMouthForm | -1..1 | VTS `MouthSmile` |
| ParamMouthA / I / U / E / O | 0..1 | 预留给 TTS lip sync，**还没有 keyform** |
| ParamBodyAngleX / Y / Z | -10..10 | 由头部角度带动 |
| ParamBreath | 0..1 | 待机正弦 |
| ParamSway | -1..1 | 待机「风」，驱动尾巴和后发 |
| ParamHairFront / HairSide1-2 / HairBack1-2 / Tail1-3 / Tassel1-2 / ArmLSway / ArmRSway | ±45（度） | 物理输出 |

VTS 输入参数名以实际 `InputParameterListRequest` 返回为准（待验证）。

## 7. VTube Studio 对接（T6，计划）

- 已核实（读过官方 API 文档 DenchiSoft/VTubeStudio README）：WebSocket 默认 `ws://localhost:8001`；需先做插件认证（用户在 VTS 里点允许）；`InputParameterListRequest` 返回所有追踪参数及当前值；`ParameterValueRequest` 读单个参数。
- 文档提示 `InputParameterListRequest` 不建议 60fps+ 高频调用。计划：约 30fps 轮询，桌宠端插值平滑到 60fps。
- 是否必须在 VTS 里加载模型才输出追踪值：待验证。
- 断线时自动回落到鼠标跟随 + 待机动画。

## 8. 桌宠行为（T7，计划）

- 透明、无边框、置顶，可拖动；托盘：缩放、置顶开关、面捕开关、退出
- 待机：呼吸、自动眨眼（随机间隔 2–6s）、眼睛和头跟随鼠标
- 点击：头部 → 摸头（眯眼 + 晃头）；身体 → 轻弹
- 闲置 N 分钟 → 打瞌睡，点击唤醒
- 命中检测：按渲染结果的 alpha 判断，透明区域点击穿透（待验证）

## 9. 已知限制

- 占位图是从 550px 设定图粗拆的：切线粗糙、被挡住的区域是糊的填色，转头时头发边缘会有撕裂和接缝。重画的 PSD 按 LAYER_SPEC 补画后大部分会消失
- 网格是自动规则网格，不像手工网格那样沿五官走线；大幅变形时会比手工 rig 生硬
- 嘴的五个元音还没有 keyform；最终美术到了以后，参照 `ref_mouth_*` 图层做
- 最终 PSD 用 `eyelash_upper_*` / `eyelash_lower_*` 两层，spec 目前只给占位图的合并层 `eyelash_*` 写了 keyform，需要补
- CPU 预览约 1 秒/帧，只用于测试和出 GIF
- 每帧 Python 端开销：evaluate 约 2.7 ms，物理约 0.8 ms（云端测得）

## 10. 设计决策记录

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-10-21 | 走代码 rig，不用 Cubism | 用户想先看 Claude 能端到端做到什么程度 |
| 2026-10-21 | 美术由用户按 LAYER_SPEC 重画 | 原图正面仅约 550px 且未分层，拆图补画质量上限低 |
| 2026-10-21 | 只支持 Windows | 用户需求 |
| 2026-10-21 | 转头 ±30°（参数），真实约 ±15° | 面捕效果和补画工作量的平衡点；再大假 3D 会撕裂 |
| 2026-10-21 | 嘴：开合 + 嘴角 + AIUEO | 以后接 TTS |
| 2026-10-21 | 眼：只做基础 | 第一版控制范围，笑眼和特殊表情后加 |
| 2026-10-21 | 手臂独立摆动，不做动作 | 待机更生动，补画量可控 |
| 2026-10-21 | 物理只做头发、尾巴、流苏（+手臂摆动） | 裙子拆片在代码 rig 里易出缝 |
| 2026-10-21 | 面捕用 VTube Studio 当数据源 | 用户指定；VTS 本身不能加载我们的模型 |
| 2026-10-21 | PSD 2048×2048 | 桌宠显示 400–600px 高，足够缩放 |
| 2026-10-21 | 代码在 GitHub，同时同步到 `D:\live2d pet` | 用户选择；作品集需要 Git 记录 |
| 2026-10-21 | 转头用「深度 + 3D 旋转 + 正交投影」自动生成，而不是手调每个角度的 keyform | 代码 rig 没有建模师手调，自动方法是唯一能规模化的做法；原理清晰，也和用户的图形学方向相关 |
| 2026-10-21 | rig spec 用相对部件的坐标 | 占位图和最终美术可共用同一份 spec |
| 2026-10-21 | 先写 CPU 渲染器 | 云端环境没有 GPU / 显示器，需要能无窗口地测试和出预览 |
| 2026-10-21 | 占位图和预览不提交到仓库 | 仓库是公开的，那些是用户角色美术的衍生物 |
| 2026-10-21 | 渲染用 moderngl 离屏 + 透明 QWidget 画图 | 避开 Windows 透明 OpenGL 窗口的兼容问题；云端已验证，Windows 待验证 |
| 2026-10-21 | Windows 启动脚本把 venv、pip 缓存、临时文件都放在项目文件夹 | 用户规定只能占用 D 盘 |
