# chibi-desktop-pet

Live2D 风格的 Windows 桌宠：用代码实现的 2D 网格变形 rig（不依赖 Cubism / moc3），
支持 VTube Studio 面部追踪、物理摆动和点击互动。

> 状态：v0.1 开发中，还不能运行。

## 文档

- [PROJECT_MEMORY.md](PROJECT_MEMORY.md) — 项目当前状态、需求、任务（新会话先读这个）
- [design.md](design.md) — 架构、参数表、设计决策
- [docs/LAYER_SPEC.md](docs/LAYER_SPEC.md) — 美术分层 PSD 规范

## 开发环境

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
pytest
```
