# design.md — chibi-desktop-pet

> 状态：**v0.1 设计草案**（2026-10-21）。需求已确认，代码尚未开始。标「待验证」的地方还没有实际跑过。

## 1. 路线

不用 Cubism、不产出 moc3。自己写一套 Live2D 原理的 2D 网格 rig：

```
参数 (Parameter) ──► 关键形态插值 (keyform blend) ──► 顶点变形 (mesh deform) ──► GPU 绘制
      ▲
      ├─ VTube Studio 追踪数据（WebSocket）
      ├─ 鼠标跟随 / 待机动画 / 点击反应
      └─ 物理模拟（头发、尾巴、流苏）
```

代价：无法在 VTube Studio 里加载模型；大角度转头的质感不如手工 Cubism 绑定。用户会在 v0.1 完成后评估是否转回 Cubism。

## 2. 技术栈（计划）

| 层 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 与 AI Desktop Companion 同栈 |
| 窗口 | PySide6，无边框 + 透明 + 置顶 | 同上；托盘菜单现成 |
| 渲染 | moderngl 离屏渲染到 FBO → 读回 QImage → 透明 QWidget 绘制 | 透明 OpenGL 窗口在 Windows 上坑多；离屏读回更稳，600×600 @60fps 读回成本可接受（待验证） |
| 网格计算 | numpy | 顶点变形是向量化运算 |
| 素材导入 | psd-tools 读 PSD → 裁切每层 → 打包纹理图集 (atlas) + `model.json` | 用户交付分层 PSD |
| 面捕 | VTube Studio 插件 API，`ws://localhost:8001` | 用户指定 |
| 测试 | pytest（rig 数学、PSD 导入、参数映射）+ 离屏渲染截图对比 | |

## 3. 模块划分（计划）

```
chibi_pet/
  importer/      PSD → atlas.png + model.json（图层、网格、默认 keyform）
  rig/
    model.py     数据结构：Part, Mesh, Deformer, Parameter, Keyform
    deform.py    keyform 插值 + 变形器层级（warp / rotation）
    physics.py   摆锤链（pendulum chain），输入头/身体运动，输出摆动参数
  render/
    gl_renderer.py   moderngl：上传 atlas，按 draw order 画三角网格
  drive/
    vts_client.py    VTube Studio WebSocket：认证 + 轮询参数
    mouse_follow.py  无面捕时的视线 / 转头跟随
    idle.py          呼吸、自动眨眼、打瞌睡状态机
    lipsync.py       预留：音量 / 音素 → MouthOpenY + 五元音参数
  app/
    pet_window.py    透明置顶窗口、拖动、点击命中检测、托盘
tests/
```

## 4. 参数表（v0.1）

沿用 Live2D 标准 ID，方便以后转 Cubism 时复用。

| 参数 | 范围 | 来源 |
|---|---|---|
| ParamAngleX / Y / Z | -30..30 | VTS `FaceAngleX/Y/Z`，或鼠标跟随 |
| ParamEyeLOpen / ParamEyeROpen | 0..1 | VTS `EyeOpenLeft/Right`，或自动眨眼 |
| ParamEyeBallX / Y | -1..1 | VTS `EyeLeftX/Y`…，或鼠标 |
| ParamBrowLY / ParamBrowRY | -1..1 | VTS `Brows` 系列 |
| ParamMouthOpenY | 0..1 | VTS `MouthOpen` |
| ParamMouthForm | -1..1 | VTS `MouthSmile` |
| ParamMouthA / I / U / E / O | 0..1 | 预留给 TTS lip sync（自定义参数） |
| ParamBodyAngleX / Y / Z | -10..10 | 由头部角度衰减带动 |
| ParamBreath | 0..1 | 待机正弦 |
| ParamHairFront / ParamHairSide / ParamHairBack | -1..1 | 物理 |
| ParamTail1..3 | -1..1 | 物理（三段链） |
| ParamTasselL / ParamTasselR | -1..1 | 物理 |
| ParamArmLSway / ParamArmRSway | -1..1 | 物理（身体惯性） |

VTS 输入参数名以实际 `InputParameterListRequest` 返回为准（待验证）。

## 5. VTube Studio 对接

- 已核实（读过官方 API 文档 DenchiSoft/VTubeStudio README）：WebSocket 默认 `ws://localhost:8001`；需先做插件认证（用户在 VTS 里点允许）；`InputParameterListRequest` 返回所有追踪参数及当前值；`ParameterValueRequest` 读单个参数。
- 文档提示 `InputParameterListRequest` 不建议 60fps+ 高频调用。计划：约 30fps 轮询，桌宠端做插值平滑到 60fps。
- VTS 需要在运行状态，模型可以是任意一个（我们只读追踪数据）。是否必须加载模型才输出追踪值：待验证。
- 断线时自动回落到鼠标跟随 + 待机动画。

## 6. 桌宠行为（v0.1）

- 透明、无边框、置顶，可拖动；托盘：缩放、置顶开关、面捕开关、退出
- 待机：呼吸、自动眨眼（随机间隔 2–6s）、眼睛和头跟随鼠标
- 点击：头部 → 摸头（眯眼 + 晃头）；身体 → 轻弹
- 闲置 N 分钟 → 打瞌睡（闭眼、头下垂、慢呼吸），点击唤醒
- 命中检测：按渲染结果的 alpha + 部件区域判断点到哪里，透明区域点击穿透（待验证 Windows 上的实现方式）

## 7. 设计决策记录

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-10-21 | 走代码 rig，不用 Cubism | 用户想先看 Claude 能端到端做到什么程度 |
| 2026-10-21 | 美术由用户按 LAYER_SPEC 重画 | 原图正面仅约 550px 且未分层，拆图补画质量上限低 |
| 2026-10-21 | 只支持 Windows | 用户需求 |
| 2026-10-21 | 转头 ±30° | 面捕效果和补画工作量的平衡点 |
| 2026-10-21 | 嘴：开合 + 嘴角 + AIUEO | 以后接 TTS |
| 2026-10-21 | 眼：只做基础 | 第一版控制范围，笑眼和特殊表情后加 |
| 2026-10-21 | 手臂独立摆动，不做动作 | 待机更生动，补画量可控 |
| 2026-10-21 | 物理只做头发、尾巴、流苏 | 裙子拆片在代码 rig 里易出缝 |
| 2026-10-21 | 面捕用 VTube Studio 当数据源 | 用户指定；VTS 本身不能加载我们的模型 |
| 2026-10-21 | PSD 2048×2048 | 桌宠显示 400–600px 高，足够缩放 |
| 2026-10-21 | 代码在 GitHub，同时同步到本地 | 用户选择；作品集需要 Git 记录 |
| 2026-10-21 | 渲染用 moderngl 离屏 + QWidget（计划） | 避开 Windows 透明 GL 窗口问题；待原型验证 |
