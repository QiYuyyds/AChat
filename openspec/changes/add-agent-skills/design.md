## Context

后端已迁移至 Python FastAPI（`backend/app/`），前端仍为 Next.js/React（`src/`）。custom adapter（`backend/app/adapters/custom_adapter.py`）自跑 OpenAI 兼容 tool loop，AChat 对其工具调用完全可控；SDK adapter（claude-code / codex）的工具集与 context 管理由各自 SDK 接管，AChat 难以插入统一的渐进式披露。

项目已存在三套与本变更同构的成熟机制，本设计全程复用而非新造：

- **绑定选择**：`Agent.tool_names`（JSONB）+ `create-agent-dialog.tsx` 勾选区（`models.py:104`、对话框 `:686`）。
- **上传落盘**：`/documents/upload`（`api/documents.py:205`）+ 前端知识库上传组件。
- **按需拉取（渐进式披露的现成形态）**：`memory_recall` / `rag_search` 工具（`agent_runner.py:466-495` 自动注入），模型判断需要 → 调工具 → 拿回内容。

约束来自 CLAUDE.md：不引入新依赖须说明；LLM 输出不可信（§5.1）；fs/bash 必过沙箱（§5.3）；命令必过双平台黑名单（§5.2）；改实体字段同步 `specs/01`，改 schema 同步 `specs/08` 并 `pnpm db:push`。

## Goals / Non-Goals

**Goals:**
- custom agent 可装备 skill，按 description 自主决定何时加载，渐进式披露（仅元数据常驻，正文/资源按需）。
- 三条来路（上传文件 / 上传文件夹 / agent `write_skill`）归一到 `<data_dir>/skills/<slug>/`，下游零分叉。
- skill 内容存本地磁盘、不进 DB；仅 `Agent.skill_names` 绑定入库。
- 安全约束零放宽，全部复用现有黑名单与沙箱。

**Non-Goals:**
- 不为 SDK agent（claude-code / codex）接入 skill。
- 不做 skill 版本链 / 编辑器 / 市场 / 共享分发。
- 不引入 skill 专属的文件读取或执行原语（复用 `fs_read` / `bash`）。
- 不把 skill 内容纳入 RAG 知识库或 workspace 配额。

## Decisions

### D1：渐进式披露，而非全文常驻
选中 skill 只把 `name + description` 注入 system prompt；正文经 `load_skill(name)` 按需读回。
- 理由：「跟 tool 一样跟着 agent」指**选择/绑定**像 tool，**加载**要像 RAG。全文常驻会随 skill 数线性撑爆 context，丢掉 skill 的核心价值。
- 备选：把每个选中 skill 的 `SKILL.md` 全文拼进 prompt——实现更短，但 3 个 skill 即显著膨胀，否决。
- 复用：`load_skill` 与 `memory_recall` / `rag_search` 完全同构（model-invoked，读回文本）。

### D2：内容存盘、绑定入库（拆分「不进 DB」）
skill 的**内容**（`SKILL.md` + 附带文件）存 `<data_dir>/skills/<slug>/`；skill 的**绑定**（哪个 agent 选了哪些 skill）存 `Agent.skill_names`（JSONB）。
- 理由：与 tool 完全一致——tool 实现在代码（不入库），`tool_names` 入库。skill 重在附带文件，纯 DB 装不下脚本；绑定必须可持久化才能「跟 agent 走」。
- 备选：skill 元数据也入库（可在 UI 编辑）——但与「不进 DB」诉求冲突且附带文件仍要落盘，徒增双写一致性问题，否决。

### D3：三来路归一到同一落盘结构
上传单文件、上传文件夹、agent `write_skill` 共用同一套：契约校验 → slugify → 重名检查 → 写 `<data_dir>/skills/<slug>/`。
- 理由：registry / `load_skill` / 勾选 UI / `fs_read` 访问全部对来源无感，维护面最小。
- 单文件：后端按 frontmatter `name` 建目录、存为 `SKILL.md`。
- 文件夹：前端 `<input webkitdirectory>` 带相对路径上传，后端按相对路径还原目录树（根须含 `SKILL.md`）。
- agent 自建：`write_skill(name, description, body, files?)`，与 `rag_ingest(document, title)` 同构。

### D4：命名 slugify + 重名拒绝
`<slug> = slugify(frontmatter.name)`（kebab-case，清除 Windows 非法字符与空白）；目标目录已存在则拒绝并提示。
- 理由：Windows 是一等支持平台，目录名必须文件系统安全；单用户本地场景下「拒绝并提示，要更新先删」最简单可预期。
- 备选：覆盖（更新语义）或加后缀——前者有误删风险、后者产生 `name` 与目录不一致的混乱，本期否决，留作 Open Question。

### D5：仅 custom adapter，注入点在 agent_runner + custom_adapter
`build_adapter_input`（`agent_runner.py:1103`）追加「可用技能」元数据块到 system prompt；`load_skill` 经 custom adapter 的 tool loop 暴露。SDK agent 的 `skill_names` 强制为空（与 `tool_names` 强制 `[]` 同处理）。
- 理由：custom adapter 工具循环可控；SDK 路径无统一插入点，强行接入收益低、复杂度高。

### D6：`write_skill` opt-in；脚本不自动执行
`write_skill` 仅在 `tool_names` 显式包含时注入（仿 `web_search`）。skill 脚本仅经 agent 显式 `bash` 执行，照旧过黑名单 + 沙箱。
- 理由：agent 可写、且 skill 可带可执行脚本，构成「LLM 生成代码落盘 + 后续执行」的信任边界扩张（CLAUDE.md §5.1）。opt-in + 不自动执行 + 复用现有沙箱将风险收敛到既有 `bash` 等同水平。

## Risks / Trade-offs

- **[渐进式披露被绕过/实现成全文常驻]** → spec 明确「仅注入元数据」为规范要求；注入块只取 registry 的 name+description，不读正文。
- **[agent 自建脚本被滥用]** → `write_skill` opt-in 默认关闭；脚本不自动执行；执行路径复用 `getBannedPatterns` + `assertPathWithinWorkspace`，不新增旁路。
- **[slugify 跨语言/Windows 边界]** → 规则集中在 `skill_service`，非法字符与空白统一清除；中文等非 ASCII `name` 的 slug 策略在 Open Questions 收口（可保留音译/退化为占位 + 提示）。
- **[`skill_names` 指向已删 skill]** → registry 为唯一真相源，运行时对缺失 slug 静默跳过，不中断 run。
- **[skill 目录无配额]** → skill 属应用级数据、单用户本地场景，暂不设配额；若后续需要再补，纳入 Open Questions。
- **[DB 迁移]** → 新增可空/默认 `[]` 的 `skill_names` 列，向后兼容；旧 agent 行默认空数组，无需数据回填。

## Migration Plan

1. 加迁移脚本：`agents` 表新增 `skill_names` JSONB（默认 `[]`），运行 `pnpm db:push`。
2. 后端先行：`skill_service` + `skills` API + `load_skill` / `write_skill` 工具 + registry；agent_runner 注入与 custom_adapter 暴露。
3. 前端跟进：左侧「Skills」入口与上传组件、`create-agent-dialog` 技能勾选区、agent 类型加 `skillNames`。
4. 回滚：移除注入与工具注册、丢弃 `<data_dir>/skills/`、保留空列（列可空，无破坏性）。

## Open Questions

- 非 ASCII（如中文）`name` 的 slug 策略：音译 / 退化占位 + 提示用户改名？（默认：清非法字符后若为空则拒绝并提示。）
- 是否对 `<data_dir>/skills/` 设总量/数量配额？（默认：本期不设。）
- 重名是否提供「覆盖/更新」入口？（默认：本期仅「拒绝 + 先删再传」。）
