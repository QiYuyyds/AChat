## ADDED Requirements

### Requirement: ObsidianSyncService SHALL 提供手动触发的增量同步

系统 MUST 实现 `ObsidianSyncService`，提供 `sync_vault(vault_path, user_id)` 方法。同步流程 MUST 依次执行：扫描 vault 目录收集所有 `.md` 文件（含相对路径和 content_hash）→ 加载 `.obsidianignore` 排除规则 → 与 `documents` 表中 `source='obsidian_sync'` 的记录按 `source_path` 对比 → 对新增/更新/删除的文件分别处理 → 返回同步报告。同步 MUST 是幂等的——连续调用两次，第二次的 added/updated/deleted 应为 0。

#### Scenario: 首次同步 vault 全部入库
- **WHEN** 用户首次对一个包含 10 个 .md 文件的 vault 触发同步
- **THEN** 所有 10 个文件被解析、预处理、入库到 RAG
- **AND** 每个文件创建一个 Document（source='obsidian_sync'，source_path 为 vault 内相对路径）
- **AND** 返回同步报告 { scanned: 10, added: 10, updated: 0, deleted: 0, skipped: 0, errors: [] }

#### Scenario: 增量同步只处理变化文件
- **WHEN** 用户修改了 vault 中 2 个文件后再次触发同步
- **THEN** 只有这 2 个 content_hash 变化的文件被重新入库（创建新 version + 清理旧 chunks + 重新 ingest）
- **AND** 其余文件被跳过（content_hash 未变）
- **AND** 返回同步报告 { scanned: 10, added: 0, updated: 2, deleted: 0, skipped: 8, errors: [] }

#### Scenario: vault 中删除的文件同步为文档删除
- **WHEN** 用户在 vault 中删除了 1 个文件后触发同步
- **THEN** 该文件对应的 Document 被软删除（status='deleted'）
- **AND** 关联的 RAG chunks 被清理
- **AND** 返回同步报告包含 deleted: 1

#### Scenario: 同步是幂等的
- **WHEN** 连续两次触发同步且 vault 内容未变
- **THEN** 第二次同步的 added=0, updated=0, deleted=0, skipped=全部

### Requirement: 增量同步 SHALL 通过 content_hash 判断文件变化

系统 MUST 为每个 vault 文件计算 `sha256(content)[:16]` 作为 `content_hash`，存入 `documents.content_hash` 列。同步时按 `source_path` 匹配 DB 记录，对比 `content_hash` 判断是否有变化。hash 相同的文件 MUST 被跳过。

#### Scenario: 文件内容未变跳过同步
- **WHEN** 同步扫描到一个文件，其 content_hash 与 DB 中记录的 content_hash 相同
- **THEN** 该文件被跳过，不创建新 version，不重新 ingest

#### Scenario: 文件内容变化触发更新
- **WHEN** 同步扫描到一个文件，其 content_hash 与 DB 中记录的 content_hash 不同
- **THEN** 为该 Document 创建新 version（content_md 为预处理后的内容）
- **AND** 清理旧 version 的 RAG chunks 后重新 ingest 新内容
- **AND** 更新 documents.content_hash 为新值

### Requirement: ObsidianPreprocessor SHALL 解析 Obsidian 特有 Markdown 语法

系统 MUST 实现 `ObsidianPreprocessor`，提供 `process(content, vault_path)` 方法，返回 `(processed_content, metadata)`。预处理管道 MUST 按以下顺序执行：

1. **Frontmatter 提取**：解析 `---` YAML 块，提取 `title`、`tags`、`aliases` 到 metadata，从内容剥离
2. **Transclusion 展开**：`![[note]]` 展开 .md 笔记内容（递归，最大深度 2，带环检测）；`![[image.png]]` 替换为 `[image: image.png]`；非 .md 非图片嵌入跳过
3. **Wikilink 归一化**：`[[Target]]` → `"Target"`，`[[Target|Alias]]` → `"Alias"`；收集 `linked_entities` 到 metadata
4. **注释剥离**：删除 `%%comment%%` 块
5. **Dataview 剥离**：删除 ` ```dataview ` 代码块
6. **Callout 简化**：`> [!type] Title\n> content` → `**Title**\ncontent`
7. **Inline tag 提取**：保留 `#tag` inline，额外收集到 metadata.tags
8. **Highlight 简化**：`==text==` → `**text**`

#### Scenario: Frontmatter 被提取到 metadata
- **WHEN** 预处理一个包含 `---\ntitle: 设计\ntags: [项目]\n---\n# 正文` 的文件
- **THEN** processed_content 为 `# 正文`
- **AND** metadata 包含 { title: "设计", tags: ["项目"] }

#### Scenario: Transclusion 递归展开
- **WHEN** 文件 A 包含 `![[B]]`，B 包含 `![[C]]`，C 是普通笔记
- **THEN** 预处理后 A 的内容包含 B 的内容，B 的内容包含 C 的内容
- **AND** 展开深度为 2 层

#### Scenario: Transclusion 环检测
- **WHEN** 文件 A 包含 `![[B]]`，B 包含 `![[A]]`
- **THEN** 展开到第 2 层时检测到环
- **AND** 环位置标记为 `[circular: A]`，不继续递归

#### Scenario: 图片嵌入替换为占位符
- **WHEN** 文件包含 `![[screenshot.png]]`
- **THEN** 替换为 `[image: screenshot.png]`

#### Scenario: Wikilink 归一化
- **WHEN** 文件包含 `参考 [[设计文档|设计]] 和 [[API规范]]`
- **THEN** processed_content 为 `参考 设计 和 API规范`
- **AND** metadata.linked_entities 包含 ["设计文档", "API规范"]

#### Scenario: 注释和 dataview 被剥离
- **WHEN** 文件包含 `%%这是注释%%` 和 ` ```dataview\nLIST\n``` `
- **THEN** 两者均从 processed_content 中删除

#### Scenario: Callout 被简化
- **WHEN** 文件包含 `> [!warning] 注意\n> 有 breaking change`
- **THEN** processed_content 包含 `**注意**\n有 breaking change`

### Requirement: .obsidianignore SHALL 支持 gitignore 语法排除文件

系统 MUST 支持 vault 根目录下的 `.obsidianignore` 文件，使用 gitignore 语法（通过 `pathspec` 库匹配）。系统 MUST 默认排除 `.obsidian/` 目录和 `Templates/` 目录，即使用户未提供 `.obsidianignore` 文件。用户提供的 `.obsidianignore` 与默认排除规则合并（取并集）。

#### Scenario: 默认排除 .obsidian 和 Templates
- **WHEN** vault 中有 `.obsidian/` 和 `Templates/` 目录，且没有 `.obsidianignore` 文件
- **THEN** 这两个目录下的文件不被扫描和同步

#### Scenario: 用户自定义排除规则
- **WHEN** vault 根目录有 `.obsidianignore` 文件，内容为 `*.draft.md\nArchive/`
- **THEN** 所有 `.draft.md` 文件和 `Archive/` 目录下的文件被排除
- **AND** 默认的 `.obsidian/` 和 `Templates/` 排除规则仍然生效

#### Scenario: .obsidianignore 自身不入库
- **WHEN** vault 根目录有 `.obsidianignore` 文件
- **THEN** 该文件本身不被同步到知识库

### Requirement: 同步 API SHALL 返回同步报告

`POST /api/obsidian/sync` MUST 返回同步报告 JSON，包含 `scanned`（扫描总数）、`added`（新增数）、`updated`（更新数）、`deleted`（删除数）、`skipped`（跳过数）、`errors`（错误列表，每项含 `path` 和 `error` 字段）。

#### Scenario: 同步成功返回报告
- **WHEN** 同步完成无错误
- **THEN** 返回 { scanned: N, added: A, updated: U, deleted: D, skipped: S, errors: [] }

#### Scenario: 同步部分文件出错
- **WHEN** vault 中有 1 个文件编码失败
- **THEN** 同步继续处理其他文件
- **AND** 返回的 errors 数组包含 { path: "broken.md", error: "encoding failed" }

### Requirement: 同步状态 API SHALL 返回当前 vault 配置和上次同步信息

`GET /api/obsidian/status` MUST 返回当前用户的 vault 路径配置、vault 是否存在、`.md` 文件总数、上次同步时间和上次同步报告摘要。

#### Scenario: 查询同步状态
- **WHEN** 用户调用 GET /api/obsidian/status
- **THEN** 返回 { vault_path: "...", vault_exists: true, total_md_files: 50, last_sync_at: 1719388800.0, last_sync_summary: { added: 5, updated: 2, ... } }

#### Scenario: vault 路径未配置
- **WHEN** 用户未配置 vault 路径就查询状态
- **THEN** 返回 { vault_path: null, vault_exists: false, total_md_files: 0, last_sync_at: null }

### Requirement: ObsidianSyncService SHALL 在 main.py 生命周期中初始化

`backend/app/main.py` 的 lifespan 中 MUST 初始化 `ObsidianSyncService`（注入 document_service 和 settings），并注册 obsidian 路由到 `/api` 前缀。

#### Scenario: 后端启动时 ObsidianSyncService 就绪
- **WHEN** FastAPI 应用启动（lifespan 执行）
- **THEN** ObsidianSyncService 被实例化并持有 document_service 引用
- **AND** /api/obsidian/* 路由可访问

### Requirement: RecursiveSplitter SHALL 保护 math block 不被切碎

`RecursiveSplitter._FENCE_RE` MUST 扩展为同时匹配 `$$...$$` 块（独占行的 `$$` 起止），将其作为原子片段保护，不被从中间切分。

#### Scenario: math block 被保护
- **WHEN** splitter 处理包含 `$$\nE=mc^2\n$$` 的文本
- **THEN** 该 math block 作为整体出现在一个 chunk 中，不被从中间切断

#### Scenario: 非独占行的 $$ 不受影响
- **WHEN** 文本中有 inline `$x$`（单美元符号）
- **THEN** 不触发 math block 保护，按正常文本切分
