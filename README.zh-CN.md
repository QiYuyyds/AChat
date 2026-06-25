# AgentHub

<p align="center">
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white">
  <img alt="React" src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=0B1F2A">
  <img alt="Electron" src="https://img.shields.io/badge/Electron-33-47848F?logo=electron&logoColor=white">
  <img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-local--first-044A64?logo=sqlite&logoColor=white">
  <img alt="pnpm" src="https://img.shields.io/badge/pnpm-workspace-F69220?logo=pnpm&logoColor=white">
</p>

<p align="center">
  <b>简体中文</b>
</p>

AgentHub 是一个 local-first 的多 Agent 协作工作空间，把 AI 协作做成 IM 群聊式的体验。

它不把每次 agent 运行当成一段孤立的终端记录，而是围绕「会话」来组织工作：Agent 是联系人，会话是工作空间，文件与产物是共享上下文，Orchestrator 还能把一项工作拆给多个 Agent 并行完成。

<p align="center">
    <img src="docs/images/agenthub-preview.png" alt="AgentHub 多 Agent 协作与产物预览" width="100%" />
</p>

> 当前状态：本地开发中。Web 版与 Electron 桌面版已可用；移动伴随端开发中。

## 目录

- [为什么选 AgentHub](#为什么选-agenthub)
- [功能特性](#功能特性)
  - [IM 式 Agent 工作空间](#im-式-agent-工作空间)
  - [多 Agent 支持](#多-agent-支持)
  - [Orchestrator 与任务调度](#orchestrator-与任务调度)
  - [产物与部署预览](#产物与部署预览)
- [技术栈](#技术栈)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [桌面应用](#桌面应用)
  - [指定 Electron 构建平台](#指定-electron-构建平台)
  - [SQLite ABI 说明](#sqlite-abi-说明)
- [移动伴随端](#移动伴随端)
- [常用命令](#常用命令)
- [架构](#架构)
- [安全模型](#安全模型)
- [已知限制](#已知限制)
- [参与贡献](#参与贡献)

---

## 为什么选 AgentHub

如今的编码 Agent 很强，但真实工作往往不止一个 prompt：

- 同时保持多个会话和工作空间
- 把工作分给不同的 Agent 和模型
- 查看推理过程、工具调用、文件写入、命令输出和产物
- 在改动落到工作空间前审批高风险操作
- 在桌面端继续工作，未来还能用手机监看

AgentHub 正是为这套工作流而生。它默认本地运行，使用 SQLite，并把 Agent 的执行保留在你自己的机器上。

---

## 功能特性

### IM 式 Agent 工作空间

- 会话列表、群聊、@提及、未读状态、书签、置顶、引用回复、编辑重发、撤回、重新生成。
- 消息是结构化的 parts，而不是一整块 markdown：文本、代码、思考、工具调用、工具结果、附件、产物引用、部署卡片、调度计划各自有不同的渲染。
- 工具调用在聊天流里可见，包括较长的 bash 命令及其输出。

### 多 Agent 支持

| 适配器 | 适用场景 |
| --- | --- |
| Claude Code | 使用 `@anthropic-ai/claude-agent-sdk`，带 Claude Code 工具集与会话续接。 |
| Codex | 使用 `@openai/codex-sdk`，配独立的 AgentHub `CODEX_HOME` / `CODEX_SQLITE_HOME`。 |
| Custom Agent | 兼容 OpenAI Chat Completions 的 provider，如 OpenAI、DeepSeek、火山方舟、OpenRouter、SiliconFlow 等。 |
| Mock | 本地开发用，不消耗 token。 |

你可以在 UI 里创建自定义 Agent，自带模型、provider、system prompt、base URL、API key 和工具集。

### Orchestrator 与任务调度

Orchestrator 是一个带额外工具的普通 Agent。它可以：

- 提出结构化的澄清问题
- 制定任务计划
- 等待计划被批准或修订
- 把任务派发给子 Agent
- 跟踪子任务的完成、失败、阻塞和产物
- 把最终结果聚合回会话

### Workspace 文件与审批

- 每个会话有一个 workspace。
- Sandbox 模式把文件存在 `.agenthub-data/workspaces/<conversationId>` 下。
- Local 模式把会话绑定到一个真实的本地项目目录。
- `fs_read`、`fs_write`、`bash` 都被限制在生效的 workspace 目录内。
- Review 模式可以在文件写入前要求审批。
- 高风险 bash 命令可以在执行前要求审批。

### 产物与部署预览

Agent 可以创建并引用结构化产物：

- `web_app`：沙箱 iframe 预览
- `document`：markdown 渲染
- `image`：图片预览
- `ppt`：幻灯片预览 + 真 `.pptx` 导出
- `code_file`：workspace 文件引用
- `diff`：版本对比

对于本地前端项目，Agent 可以把 `dist`、`build`、`out`、`client/dist` 等静态输出目录发布到一张本地预览卡片里。

### 桌面与移动端

- 支持 Electron 桌面打包。
- `apps/mobile` 下有一个 Capacitor 移动伴随端。
- 设想的移动端工作方式是「伴随客户端」：手机通过 LAN 或 Tailscale 连到桌面端的 AgentHub host，然后观察运行、发消息、处理审批。

---

## 技术栈

- Next.js 16 App Router + React 19
- TypeScript strict 模式
- Tailwind CSS v4 + shadcn/ui
- Zustand + Immer
- SQLite + Drizzle + `better-sqlite3`
- SSE 实时更新
- Electron 33 桌面打包
- Capacitor 移动伴随端
- pnpm workspaces

Next.js 锁定在 `16.2.6`。如果你要改动框架层的行为，先读 `node_modules/next/dist/docs/` 下的本地 Next 文档。

---

## 环境要求

- Node.js 20+
- pnpm
- 走桌面端路径需要 macOS 或 Windows
- 只有开发 iOS 伴随端时才需要 Xcode 和 CocoaPods

可选的 provider 配置：

- 用 Claude Code 适配器走 OAuth 时需要 Claude Code 登录
- Anthropic、OpenAI、DeepSeek、火山方舟，或自定义 OpenAI 兼容 provider 的 API key

---

## 快速开始

```bash
pnpm install

# 可选
cp .env.example .env.local

# 启动开发服务
pnpm dev
```

打开：

```text
http://localhost:3000
```

首次启动时，应用会自动创建 SQLite 数据库并 seed 内置 Agent。

API key 既可以配在 `.env.local`，也可以在应用的设置面板里配。Agent 级别的 key 会覆盖全局设置。

常用环境变量：

```bash
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
ARK_API_KEY=...
```

Claude Code 也可以直接复用本机已有的 Claude Code 登录，无需单独的 Anthropic API key。

---

## 桌面应用

开发模式：

```bash
pnpm electron:dev
```

默认打包命令：

```bash
pnpm electron:build
```

产物输出到：

```text
release/
```

当前 `package.json#build` 配置的目标：

- macOS：`dmg`，`arm64`
- Windows：`nsis`，`x64`

### 指定 Electron 构建平台

`pnpm electron:build` 是个便捷脚本。如果你想精确选择平台/架构，就跑同一套 prebuild 流程，再带平台 flag 调 `electron-builder`：

```bash
# macOS arm64 DMG
pnpm build && pnpm electron:prebuild && pnpm electron:tsc && pnpm exec electron-builder --mac dmg --arm64

# macOS x64 DMG
pnpm build && pnpm electron:prebuild && pnpm electron:tsc && pnpm exec electron-builder --mac dmg --x64

# Windows x64 NSIS 安装包
pnpm build && pnpm electron:prebuild && pnpm electron:tsc && pnpm exec electron-builder --win nsis --x64
```

直接调 `electron-builder` 时，短 flag 也可以：

```bash
pnpm exec electron-builder -m --arm64
pnpm exec electron-builder -w --x64
```

原生模块在这里很关键。`better-sqlite3` 是针对特定 Node/Electron ABI 和 CPU 架构编译的。为了发布可靠，最好在目标 OS/架构上构建，或用 CI 矩阵。从 macOS 交叉构建 Windows 可能需要 Wine 等额外工具链，跨 CPU 架构交叉构建原生模块也不总是可靠。

### SQLite ABI 说明

本项目会根据命令在 Node ABI 和 Electron ABI 之间切换 `better-sqlite3`：

- `pnpm dev`、`pnpm test`、`pnpm e2e`：Node ABI
- `pnpm build`、`pnpm start`、`pnpm db:*`、打包后的 Electron app：Electron ABI

包脚本会尝试自动检查并 rebuild。如果你看到原生模块版本错误，跑下面之一：

```bash
pnpm rebuild better-sqlite3
pnpm electron:rebuild
```

然后重跑刚才失败的命令。

---

## 移动伴随端

移动端 workspace：

```bash
apps/mobile
```

常用命令：

```bash
pnpm mobile:dev
pnpm mobile:build
pnpm mobile:sync
pnpm mobile:open:ios
pnpm mobile:open:android
```

移动端被设计成通过 LAN 或 Tailscale 连接桌面端的 AgentHub host。Agent 执行、文件写入、命令执行和 workspace 状态都留在桌面侧。

---

## 常用命令

```bash
pnpm dev                 # Web 开发服务
pnpm typecheck           # TypeScript 检查
pnpm lint                # ESLint
pnpm test                # Vitest
pnpm e2e                 # Playwright E2E
pnpm build               # Electron Node ABI 下的 Next 生产构建
pnpm start               # 启动生产服务
pnpm db:push             # 应用 Drizzle schema
pnpm db:seed             # 重新 seed 内置 Agent
pnpm electron:dev        # 桌面开发模式
pnpm electron:build      # 桌面打包
```

本地数据：

```text
.agenthub-data/agenthub.db
.agenthub-data/workspaces/
```

打包后的桌面数据：

```text
macOS:   ~/Library/Application Support/AgentHub/data
Windows: %APPDATA%/AgentHub/data
```

---

## 架构

AgentHub 采用五层结构：

```text
L5 UI
  React 组件、shadcn/ui、消息/产物渲染

L4 State + Transport
  Zustand store、SSE 客户端、StreamEvent 的 reducer

L3 Application Services
  AgentRunner、ConversationService、EventBus、ToolExecutor

L2 Agent Platform Adapters
  Claude Code、Codex、自定义 OpenAI 兼容适配器、Mock

L1 Persistence
  SQLite、Drizzle、workspace 文件系统
```

核心契约是 `StreamEvent`。适配器输出、工具活动、产物创建、待审批、调度状态、用量更新，都先汇入这个事件模型，再到达 UI。

关键文档：

- [CLAUDE.md](./CLAUDE.md)：给 AI 协作者的项目规则
- [OVERVIEW.md](./OVERVIEW.md)：代码地图与当前实现状态
- [openspec/project.md](./openspec/project.md)：OpenSpec 能力索引
- [specs/](./specs)：编号版详细规格

---

## 安全模型

AgentHub 假定 LLM 的输出是不可信输入。

- 文件工具把路径解析到会话生效的 workspace 之内。
- Bash 命令在 workspace cwd 内运行。
- 危险的 bash 模式会被拦截。
- 高风险命令可以要求审批。
- 生成的 web app 产物在沙箱 iframe 里渲染。
- API key 是本地设置或环境变量；没有任何托管的 key 服务。

这是一个本地单用户应用，不是多租户托管服务。

---

## 已知限制

- 还没配置 Linux 桌面打包。
- 带原生模块的跨平台 Electron 构建，应该通过目标平台机器或 CI 处理。
- Claude Code SDK 可以通过它自己的工具层写文件；sandbox 配额只对 AgentHub 托管的文件工具生效。
- 一些 SDK 层的命令/文件审批桥接，取决于底层适配器暴露了什么。
- 移动端是伴随客户端，不是独立的 Agent 运行时。

---

## 参与贡献

改代码前，先读：

1. [CLAUDE.md](./CLAUDE.md)
2. [openspec/project.md](./openspec/project.md)
3. [openspec/specs](./openspec/specs) 和 [specs](./specs) 下的相关文件

当你改动实体、流式事件、工具、适配器、持久化、平台行为或安全规则时，代码和 spec 要一起更新。

---

## License

学习 / 研究项目。在面向广泛外部使用发布 release 前，请先补上正式 license。
