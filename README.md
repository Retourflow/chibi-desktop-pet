# chibi-desktop-pet

Live2D 风格的 Windows 桌宠：用代码实现的 2D 网格变形 rig（不依赖 Cubism / moc3），
支持 VTube Studio 面部追踪、物理摆动和点击互动。

> 状态：v0.1 开发中。rig、GPU 渲染和桌宠窗口已能运行；面部捕捉和点击互动还没做。

## Windows 快速启动

双击 `run_pet.bat`。第一次运行会在本文件夹里建 `.venv` 并安装依赖（pip 缓存和临时文件也放在本文件夹），之后直接启动。
需要先装好 Python 3.11+；如果 `python` 不在 PATH 上，先执行 `set PYTHON=D:\你的路径\python.exe`。

桌宠操作：左键拖动；右键或托盘图标打开菜单（大小、置顶、退出）。

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
