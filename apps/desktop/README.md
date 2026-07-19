# AChat Desktop (Tauri 2)

Windows 桌面交付（**v1 产品方向**）：

**Tauri 壳 + 本机完整后端引擎 + 内嵌静态前端 + 直连基础设施（默认官方，可自配）**

- 窗口在引擎 `/healthz` 就绪后打开 **`http://127.0.0.1:<port>/`**（本机 UI，不再导航远程业务站）
- 前端业务 API **一律**打本机引擎
- 引擎直连配置的 PostgreSQL / 可选 Milvus·ES·Neo4j（`infra.default.json` + 用户覆盖）
- 不捆绑 Claude / Codex CLI

OpenSpec change: `desktop-client-tauri-local-engine`（design **D21–D30**）。

## Prerequisites

- Rust (stable) + MSVC Build Tools (Windows)
- Node 20+ / pnpm
- Python 3.11+（dev 引擎）或预构建 `resources/engine`

## Config

| File | Use |
|---|---|
| `configs/infra.default.dev.json` | 本地开发默认 infra |
| `configs/infra.default.prod.json` | 生产占位（CI 注入 secrets，勿提交真实口令） |
| `src-tauri/infra.default.json` | 打进安装包的默认配置 |
| `%APPDATA%/AChat/config/infra.user.json` | 用户设置覆盖（运行时） |

```bash
cp configs/infra.default.dev.json src-tauri/infra.default.json
pnpm check:infra
```

生产打包应在 CI 用 secrets **注入** `databaseUrl` 等，避免把生产凭据写进 git。

## Static UI（产品安装包必须用完整 UI）

安装包运行时打开的是**本机引擎托管的静态前端**（不是 `pnpm dev` 的 :3000）。  
若 `resources/ui` 只有占位页，用户会看到「本地引擎已就绪」，而不是完整 AChat。

### 完整产品 UI（推荐）

在**仓库根目录**：

```bash
# 1) Next 静态导出 → 仓库根 out/
pnpm desktop:build-ui

# 2) 拷进 Tauri resources/ui（拒绝静默使用占位页）
cd apps/desktop
pnpm build:ui
```

或一条龙（根目录，需已装好引擎 sidecar 与 infra 配置）：

```bash
pnpm desktop:package
```

### 仅引擎冒烟（占位页，不要给最终用户）

```bash
cd apps/desktop
pnpm build:ui:placeholder
```

引擎 CLI：`--ui-dir`；壳 release 导航本机引擎 origin；dev 仍可打开 `http://localhost:3000`。

## Brand icons

源图 `src/app/favicon.ico` → `python apps/desktop/scripts/generate-icons.py` → **必须 rebuild 壳**。

详见 design **D20**。

## Dev

```bash
# 终端 1：确保本地/远程 PG 与 backend 依赖可用（desktop 会直连 DATABASE_URL）
# 终端 2：
cd apps/desktop
pnpm install
pnpm build:ui   # 至少一次
pnpm dev
```

壳通过 `python -m app.desktop.cli` 拉起引擎（或 `ACHAT_ENGINE_BIN`）。

引擎参数示例：

```bash
python -m app.desktop.cli serve \
  --bind 127.0.0.1 --port 0 \
  --data-dir "$APPDATA/AChat" \
  --engine-token devtoken \
  --infra-config apps/desktop/src-tauri/infra.default.json \
  --ui-dir apps/desktop/ui
```

## Build / release

1. `pnpm build:ui`（及可选 Next 构建）
2. 引擎 sidecar：见 `backend/scripts/desktop/PACKAGING.md`
3. 注入 `src-tauri/infra.default.json`
4. `cd apps/desktop && pnpm build` → NSIS 安装包
5. 产物含：壳 + `resources/engine` + `resources/ui` + `infra.default.json`

## Smoke (internal)

安装 → 启动 → **本机登录** → 发消息 → 绑目录 →（可选）设置页改自定义 infra 并重启 → 退出。

## Related

- OpenSpec: `openspec/changes/desktop-client-tauri-local-engine`
- 引擎模块: `backend/app/desktop/`
