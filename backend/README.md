# AChat Backend

FastAPI + SQLAlchemy(async) 后端，为 AChat 前端（Next.js web / Capacitor mobile）与
Electron 桌面壳提供服务。LLM 接入走 adapter 层（Claude Code / Codex CLI 与
OpenAI 兼容 SDK），编排能力含 DAG 派发、worktree 隔离、人审 pending 流程与
记忆/RAG 子系统。

## 环境

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"       # Windows；POSIX 用 .venv/bin/pip
pip install "aeval-framework[api,cli]"      # 评测框架，可选
```

依赖同样维护在 `requirements.txt`（与 pyproject 保持同序）；Python >= 3.11。

## 常用命令

```bash
.venv/Scripts/python -m pytest tests -q    # 全量测试（自包含：sqlite 注入，无需 Docker）
.venv/Scripts/python -m ruff check app     # lint
.venv/Scripts/python -m app.main           # 本地启动（配置见仓库根 .env.example）
```

架构全貌见仓库根 `ARCHITECTURE.md` 与 `OVERVIEW.md`；规格见 `specs/`。
