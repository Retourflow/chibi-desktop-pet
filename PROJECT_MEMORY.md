# PROJECT_MEMORY.md — chibi-desktop-pet

> 上下文恢复文件。新会话先读这里，再读 `design.md` 和 `docs/LAYER_SPEC.md`。
> 最后更新：2026-10-21

## 1. 项目是什么

把用户的 Q 版精灵龙角少女（三视图设定稿）做成 **Live2D 风格的 Windows 桌宠**，支持面部捕捉。
不走 Cubism / moc3，而是用代码自己实现网格变形 rig（route 1）。v0.1 做完后用户评估效果，再决定是否改用 Cubism 手工绑定。

角色要点：黑发带青色渐变和红色挑染、红色鹿角配白花、尖耳、金属钥匙发夹 + 红流苏、青红龙尾、绿白连衣裙、腰带、黑靴。

## 2. 当前状态

- **阶段**：T1（PR #1）、T2/T3（PR #2）、T4（PR #3）、T6 VTube Studio（PR #4）、T7 桌宠行为（PR #5）完成，PR 依次叠加；v0.1 功能齐了，等用户在 Windows 上试运行（T8 评估）
- **版本**：v0.1
- **代码**：导入器（PSD/PNG → atlas + model.json）、rig 核心（keyform、Turn3D 假 3D 转头、Bend 链式弯曲、摆锤物理）、CPU 预览渲染器、占位图生成工具、预览动画工具、GPU 渲染器、桌宠窗口、待机和鼠标驱动。21 个 pytest 通过（只在 Linux 云端跑过）
- **桌宠窗口已能运行（云端验证）**：透明置顶、待机呼吸/眨眼/摇摆、头和眼睛跟随鼠标、拖动、右键/托盘菜单（大小、面捕、置顶、退出）、GPU 渲染
- **面捕（T6）**：VTube Studio 插件 API 客户端（后台线程、token 保存、断线重连）、参数映射 `assets/vts_mapping.json`、面捕和鼠标跟随按权重平滑切换。用模拟的 VTS 服务器测过，**真实 VTS 还没测**（参数名、方向、镜像待用户用 `check_vts.bat` 验证）
- **桌宠行为（T7）**：点击头部 = 摸头（闭眼笑 + 晃头），点击身体 = 戳（惊讶 + 身体后仰回弹）；闲置 N 分钟打瞌睡（默认 3，菜单可改），点击唤醒；口型接口 `LipSync`（给以后 TTS 用）；设置记忆 `.pet_settings.json`
- **Windows 上还没跑过**：透明效果、点击穿透、帧率都待用户验证
- **美术**：用户将按 `docs/LAYER_SPEC.md` 重画分层 PSD，尚未交付。开发用占位图（从设定图粗拆，质量很粗糙）

## 3. 已确认需求（2026-10-21 访谈）

| # | 问题 | 结论 |
|---|---|---|
| 1 | 美术来源 | 用户（或找人）按分层清单重画 |
| 2 | 系统 | 只做 Windows |
| 3 | 转头幅度 | 标准 ±30° |
| 4 | 嘴 | 开合 + 嘴角（笑/撇）+ AIUEO 口型，为 TTS 预留 |
| 5 | 眼睛/表情 | v0.1 只做基础：眨眼、眼珠跟随、眉毛。笑眼和特殊表情延后 |
| 6 | 身体/手臂 | 呼吸 + 身体摇摆；手臂独立一层，惯性摆动；不做挥手动作 |
| 7 | 物理 | 头发（前/侧/后）、尾巴、发夹流苏。衣服物理延后 |
| 8 | 面捕 | **VTube Studio 作为追踪数据源**（插件 WebSocket API），模型由我们自己的程序渲染。已向用户说明 VTS 不能加载代码 rig 模型 |
| 9 | 桌宠行为 | 透明置顶、拖动、待机、鼠标跟随、托盘 + 点击互动（摸头、戳身体）+ 闲置打瞌睡。AI 对话/TTS 延后，接口预留 |
| 10 | 美术规格 | 用户交给 Claude 决定：分层 PSD，2048×2048，英文图层名 |
| 11 | 代码位置 | GitHub 仓库 + 同步一份到用户 Windows 电脑 |

## 4. v0.1 任务拆分（Project → Version → Task → DoD → Stop）

| Task | DoD | Stop condition |
|---|---|---|
| T1 仓库骨架 + 文档 | 仓库建好，docs 入库，pytest 能跑 | — |
| T2 PSD 导入器 ✓ | 读 PSD/PNG → atlas + model.json | PSD 路径还没用真实 PSD 验证 |
| T3 Rig 核心 ✓ | 参数 → keyform 插值 → 变形；单元测试覆盖插值和变形器层级 | |
| T4 渲染 + 透明窗口 ✓（云端） | Windows 上透明置顶显示，60fps | 若离屏读回方案帧率不够，再评估直接透明 GL；Windows 待验证 |
| T5 物理 | 头发/尾巴/流苏摆动自然，不发散 | 基础物理已在 T3 实现并测试，T5 只剩调参 |
| T6 VTS 对接 ✓ | 连接、认证、参数映射、断线回落 | 云端用模拟 VTS 验证；真实 VTS 待用户验证 |
| T7 桌宠行为 ✓ | 待机、鼠标跟随、点击反应、打瞌睡、托盘 | 云端用模拟点击验证 |
| T8 预览 + 评估 | 用户看效果，决定是否转 Cubism | v0.1 结束 |

## 5. 待办 / 等待用户

- [x] 用户在 GitHub 建好仓库并接到项目（2026-10-21）
- [x] 本地同步文件夹：`D:\live2d pet`（2026-10-21）。同步方式：Claude 把仓库文件直接复制过去（不是 git clone），每个阶段结束后同步一次
- [ ] 用户开始按 LAYER_SPEC 重画
- [ ] 用户在 Windows 上双击 `run_pet.bat` 试运行，反馈透明、点击穿透、帧率（控制台每 5 秒打印一次 fps）
- [ ] 用户打开 VTube Studio 试面捕；方向不对或连不上时运行 `check_vts.bat` 把输出发回来

## 6. 常用命令

```bash
pip install -e ".[dev,psd,tools]"
pytest
# 占位图（设定图路径换成本地的）→ 模型 → 预览动画
python tools/make_placeholder.py <设定图.png> build/placeholder_layers
python -m chibi_pet.importer build/placeholder_layers build/placeholder_model
python tools/preview.py build/placeholder_model build/preview.gif --crop 40,0,580,614
# 最终 PSD
python -m chibi_pet.importer art.psd build/model
```

Windows：双击 `D:\live2d pet\run_pet.bat`（第一次会在本文件夹建 `.venv` 并安装依赖，之后直接启动）。占位模型在 `build\placeholder_model`（不进仓库，由 Claude 同步过去）。

**用户规定：在用户电脑上只能占用 D 盘。** 以后在 Windows 上建 venv 时放在 `D:\live2d pet\.venv`，并把 pip 缓存设到 D 盘（`set PIP_CACHE_DIR=D:\live2d pet\.pip-cache`）。不要再在用户电脑上用 device_bash（会启动 Claude 桌面端的 Linux 虚拟机，占 C 盘约 20 GB 虚拟磁盘）。

## 7. 已知风险

- 代码 rig 大角度转头会比手工 Cubism 扁平；占位图上转头时头发外轮廓还有少量撕裂和双线（脸部重影已在 2026-10-21 修掉，见 design.md §9）
- 嘴的五个元音还没有 keyform；最终 PSD 的 `eyelash_upper/lower` 还没写 keyform（见 design.md §9）
- 透明 OpenGL 窗口 / 点击穿透在 Windows 上的实现方式待原型验证
- VTS 是否必须加载模型才输出追踪值、默认参数名和方向：待验证（`check_vts.bat`）
- 重画工作量大，美术交付时间决定整体进度；开发期间先用占位素材

## 8. 相关文件与位置

- GitHub：https://github.com/Retourflow/chibi-desktop-pet （**公开仓库**：不要提交用户的美术或其衍生物）。PR #1 = T1 骨架，PR #2 = T2/T3，PR #3 = T4，PR #4 = T6，PR #5 = T7
- 预览动画和占位图放在项目共享文件夹 `/mnt/project-files/chibi-desktop-pet/previews/`，不进仓库
- 用户本地副本：`D:\live2d pet`（Windows）。用户电脑上还有 `D:\Live2D Cubism 5.3`、`D:\live2d ai`、`D:\桌宠demo`、`D:\庄方宜桌宠` 等文件夹，内容未查看

- `docs/LAYER_SPEC.md` — 给重画用的分层清单
- `design.md` — 架构、参数表、决策记录
- 参考图：项目上传的三视图设定稿

## 9. 变更记录

- 2026-10-21：项目创建；选定代码 rig 路线；完成 11 项需求访谈；写出 LAYER_SPEC / design / PROJECT_MEMORY 初稿；仓库建立，T1 骨架提交
- 2026-10-21：用户发现 C 盘被占用（Claude 桌面端虚拟机），定下「只占用 D 盘」规则
- 2026-10-21：T2/T3 完成：导入器、rig 核心、物理、CPU 渲染、占位图和 6 秒预览动画
- 2026-10-21：T4 完成（云端验证）：GPU 渲染、透明桌宠窗口、待机动画、鼠标跟随、托盘、Windows 启动脚本、控制台 fps 显示；已同步到 D 盘（含占位模型）
- 2026-10-21：用户反馈预览「有大量重叠部分」（转头时的重影 / 双线）。改进占位图工具：按颜色分开头发和皮肤，重新生成占位模型和新旧对比视频，同步到 D 盘
- 2026-10-21：T6 完成（云端用模拟 VTS 验证）：VTube Studio 客户端、参数映射、面捕/鼠标混合、托盘开关、check_vts.bat；run_pet.bat 改为依赖变化时自动重装
- 2026-10-21：T7 完成（云端验证）：点击反应、打瞌睡、口型接口、设置记忆；所有行为统一成（参数，权重）分层混合
