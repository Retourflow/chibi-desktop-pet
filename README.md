# chibi-desktop-pet

Live2D 风格的 Windows 桌宠：用代码实现的 2D 网格变形 rig（不依赖 Cubism / moc3），
支持 VTube Studio 面部追踪、物理摆动和点击互动。

> 状态：v0.1 开发中。rig、GPU 渲染、桌宠窗口和 VTube Studio 面捕已能运行；点击互动和打瞌睡还没做。

## Windows 快速启动

双击 `run_pet.bat`。第一次运行会在本文件夹里建 `.venv` 并安装依赖（pip 缓存和临时文件也放在本文件夹），之后直接启动。
需要先装好 Python 3.11+；如果 `python` 不在 PATH 上，先执行 `set PYTHON=D:\你的路径\python.exe`。

桌宠操作：左键拖动；右键或托盘图标打开菜单（大小、面捕、置顶、退出）。
黑色控制台窗口会每 5 秒打印一次 `fps` 和每帧耗时，关掉控制台桌宠也会退出（v0.1 为了方便看报错）。

## 面部捕捉（VTube Studio）

1. 打开 VTube Studio，在设置里开启「Allow Plugin API access」（默认端口 8001）
2. 桌宠右键菜单勾选「面捕（VTube Studio）」
3. 第一次连接时 VTS 会弹窗问是否允许「Chibi Desktop Pet」，点允许（之后会记住）

脸丢失或关掉 VTS 时，桌宠自动回到鼠标跟随。连不上或方向不对时，双击 `check_vts.bat`，它会打印 VTS 发来的所有参数。
方向和范围可以在 `assets/vts_mapping.json` 里改。

## 文档

- [PROJECT_MEMORY.md](PROJECT_MEMORY.md) — 项目当前状态、需求、任务（新会话先读这个）
- [design.md](design.md) — 架构、参数表、设计决策
- [docs/LAYER_SPEC.md](docs/LAYER_SPEC.md) — 美术分层 PSD 规范

## 开发环境

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev,app,psd,tools]"
pytest
```

## 用法

```bash
# 分层 PSD（或整画布 PNG 文件夹）→ atlas.png + model.json
python -m chibi_pet.importer art.psd build/model
# 渲染一段预览动画
python tools/preview.py build/model build/preview.gif
# 启动桌宠
python -m chibi_pet.app build/model
```
