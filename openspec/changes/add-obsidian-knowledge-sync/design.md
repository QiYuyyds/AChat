## Context

AChat 已有完整的 Document + Version 知识库体系和 RAG 三路融合检索（Milvus 语义 + ES BM25 + Neo4j KG），但文档入库完全依赖手动上传。用户在 Obsidian 中维护大量 Markdown 笔记，每次内容更新都需手动重新上传，知识库内容容易滞后。

现有系统关键约束：
- `DocumentService` 提供 `write_document()` / `ingest_version()` / `delete_document()` 等完整 CRUD + RAG 桥接
- `RAGEngine.ingest()` 负责 split → embed → index（PG/Milvus/ES/Neo4j 四路）
- `RecursiveSplitter` 有 Markdown 围栏代码块保护，但不认识 Obsidian 特有语法
- `documents` 表是扁平结构，无路径信息
- `user_settings` 表存储 per-user 配置（API keys、companion mode 等）
- 基础设施可独立降级，RAG 不可用时不阻断核心对话流

## Goals / Non-Goals

**Goals:**
- 手动触发 + 增量同步 Obsidian vault 到 RAG 知识库（只处理变化的文件）
- 正确处理 Obsidian 特有 Markdown 语法（frontmatter、transclusion、wikilink、math block、comments、dataview）
- 文件系统式文档组织（虚拟目录树浏览，按文件夹层级展开）
- `.obsidianignore` 排除规则
- 用户设置面板配置 vault 路径
- 复用现有 DocumentService + RAGEngine + HybridStore 管道，不另起炉灶

**Non-Goals:**
- 不做文件系统实时监听（watchdog），MVP 只做手动触发
- 不做双向同步（AChat 不回写 Obsidian vault）
- 不开发 Obsidian 插件
- 不处理 `.canvas` 文件（Obsidian Canvas 是 JSON 格式，后续可扩展）
- 不做文件夹级别的权限或配置（文件夹是虚拟的，从 document.source_path 派生）
- 不做跨 vault 同步（一个用户配置一个 vault 路径）

## Decisions

### D1: 同步触发方式——手动触发 + 增量

**选择**：用户在前端点"同步 Obsidian"按钮，后端扫描 vault，对比 `content_hash` 判断变化，只处理新增/更新/删除的文件。

**理由**：
- 本地运行场景，watchdog 常驻进程增加复杂度且跨平台有坑
- 手动触发让用户有控制感（"我改完了，同步一下"）
- 增量更新只需对比文件内容 hash，只处理变化的文件，效率足够

**备选**：watchdog 文件系统监听（实时但复杂）、定时扫描（不实时但自动）、Obsidian 插件（最自然但开发维护成本高）。

### D2: 文档组织——虚拟路径 + 来源分区（方案 C）

**选择**：`documents` 表新增 `source_path` 列（如 `"Projects/AChat/架构设计.md"`），不新增 folder 表。API 提供虚拟目录树视图，从 `source_path` 派生文件夹结构。**不同来源的文档分区存放，不混在一起**：

- `source='obsidian_sync'` 的文档使用文件树视图（有 source_path，按 vault 目录结构组织）
- `source='user_upload'` 的文档使用扁平列表视图（source_path 为空，按 updated_at DESC 排序）
- `source='agent_generated'` / `source='artifact_import'` 的文档使用扁平列表视图（同上）

前端知识库页面分为两个独立区域：
```
┌─────────────────────────────────────────────────┐
│  知识库                  [同步 Obsidian] [上传]  │
│  ─────────────────────────────────────────────  │
│                                                  │
│  📁 Obsidian Vault                               │
│  ├── 📁 Projects/          (4)                   │
│  ├── 📁 Daily Notes/       (2)                   │
│  └── 📄 README.md           obsidian · ver 1     │
│                                                  │
│  ─────────────────────────────────────────────  │
│                                                  │
│  📄 我的文档                                     │
│  ├── 产品需求文档.pdf       upload · ver 3       │
│  ├── API设计规范.md         agent · ver 1        │
│  └── 用户调研报告.pdf       upload · ver 2       │
└─────────────────────────────────────────────────┘
```

**理由**：
- 最简单，只加一列，不改表结构骨架
- Obsidian 同步时路径直接来自 vault 文件系统，无需额外维护 folder 表
- 手动上传和 Agent 产物的文档没有目录结构概念，不需要伪造路径，保持扁平列表即可
- 不同来源的文档生命周期管理方式不同（Obsidian 有增量同步，上传/产物没有），分区避免混淆
- 查询简单：`WHERE source_path LIKE 'Projects/%' AND source='obsidian_sync'`
- `GET /api/documents/tree` 默认只返回 `source='obsidian_sync'` 的文档
- `GET /api/documents` 仍返回所有来源的文档（兼容现有行为），前端按 source 分区渲染

**备选**：独立 Folder 表（文件夹是一等公民但增加复杂度）、一个 vault = 一个 Document（粒度太粗）、所有来源混在一个树里（手动上传文档没有目录结构，强行套路径不自然）。

### D3: Transclusion 展开策略——展开 2 层 + 环检测

**选择**：`![[note]]` 嵌入递归展开 Markdown 笔记内容，最大深度 2 层，带环检测（visited set，遇到环标记 `[circular]`）。图片嵌入替换为 `[image: filename]` 占位符。

**理由**：
- 不展开会导致上下文断裂——检索"技术栈"时，嵌入 `![[技术栈]]` 的文档不会命中
- 展开后 RRF 去重 + `_dedupe_results` 会过滤重复内容
- 2 层深度平衡了上下文完整性和内容爆炸风险
- 环检测防止无限递归

**备选**：不展开（上下文断裂）、完全展开（可能内容爆炸）、只展开 1 层（嵌套引用仍断裂）。

### D4: Wikilink 处理——归一化为纯文本 + 提取 KG 实体

**选择**：`[[Target]]` → `"Target"`，`[[Target|Alias]]` → `"Alias"`。提取的 `linked_entities` 列表作为 metadata 存储，可供 Neo4j KG 建立文档间引用关系。

**理由**：
- `[[` `]]` 对 embedding 是纯噪声
- 提取的实体名可增强 KG 图谱搜索（搜"设计文档"时通过 KG 关联到引用它的文档）
- 不破坏当前 Splitter 逻辑，预处理在 split 之前完成

### D5: Frontmatter 处理——提取为 metadata，从内容剥离

**选择**：YAML frontmatter 解析后提取 `title`、`tags`、`aliases` 等到 document metadata，从正文内容中剥离 `---` 块。

**理由**：
- YAML 对 embedding 是噪声
- `tags` 和 `aliases` 可用于 ES 额外索引或 KG 实体标签
- 使用 `pyyaml` 解析（已在项目依赖中）

### D6: `.obsidianignore` 格式——gitignore 语法

**选择**：vault 根目录下的 `.obsidianignore` 文件，使用 gitignore 语法（`pathspec` 库匹配）。默认排除 `.obsidian/` 和 `Templates/`。

**理由**：
- gitignore 语法用户最熟悉
- `pathspec` 是轻量纯 Python 库
- 默认排除规则保证 Obsidian 配置目录和模板不入库

### D7: Math block 保护——扩展 Splitter 的 `_FENCE_RE`

**选择**：修改 `RecursiveSplitter._FENCE_RE`，在现有代码围栏保护基础上增加 `$$...$$` math block 保护。

**理由**：
- 当前 splitter 会从中间切碎 `$$E=mc^2$$`，破坏 LaTeX 语义
- 扩展正则即可，不改变 splitter 的核心递归逻辑
- 对非 Obsidian 文档也有效（通用 Markdown math block 保护）

### D8: 增量同步 diff 算法

**选择**：同步时扫描 vault 所有 `.md` 文件，计算每个文件的 `sha256(content)[:16]` 作为 `content_hash`。与 `documents` 表中 `source='obsidian_sync'` 且 `source_path` 匹配的记录对比：
- 文件存在但 DB 无记录 → 新增
- 文件存在且 hash 不同 → 更新（创建新 version + 重新 ingest）
- 文件存在且 hash 相同 → 跳过
- DB 有记录但文件不存在 → 删除（soft-delete + clean chunks）

**理由**：
- content hash 是内容变化的准确判断（不受 mtime 精度影响）
- 按 source_path 匹配，确保文件移动后能正确关联
- 增量只处理变化的文件，大 vault 也能快速同步

### D10: 文档来源分区——Obsidian 同步与手动上传/产物独立存放

**选择**：知识库视图中不同 `source` 的文档分区展示，不混在一起。`source='obsidian_sync'` 的文档走文件树视图（有 source_path），`source='user_upload'` / `source='agent_generated'` / `source='artifact_import'` 的文档走扁平列表视图（无 source_path）。`GET /api/documents/tree` 只返回 `source='obsidian_sync'` 的文档。`GET /api/documents` 返回所有来源文档，前端按 source 分区渲染。

**理由**：
- 手动上传和 Agent 产物没有目录结构概念，不应和 vault 文件混在一起
- Obsidian 同步的文档有增量同步生命周期，手动上传没有，分区避免管理操作混淆
- RAG 检索不区分来源——所有文档的 chunks 都在同一个检索池中，分区只影响 UI 展示

### D9: 同步 API 返回同步报告

**选择**：`POST /api/obsidian/sync` 返回同步报告 JSON：
```json
{
  "scanned": 50,
  "added": 5,
  "updated": 2,
  "deleted": 1,
  "skipped": 42,
  "errors": [{"path": "broken.md", "error": "encoding failed"}]
}
```

**理由**：
- 用户需要知道同步了什么
- errors 数组让用户知道哪些文件有问题（如编码失败、环检测等）
- 前端可展示同步摘要 toast

## Risks / Trade-offs

- [Transclusion 展开导致内容重复] → RRF 去重 + `_dedupe_results` 过滤重复 chunk；展开深度限制 2 层控制爆炸
- [大 vault 首次同步慢] → 首次全量入库耗时取决于文件数量和 embedding API 限速；增量同步只处理变化文件，后续很快。可在前端展示进度
- [vault 路径无效或不存在] → sync API 返回 400 + 明确错误信息；不阻断已有知识库
- [Obsidian 语法持续演进] → Preprocessor 设计为管道式（每个 step 独立），新增语法处理只需加一个 step
- [`.obsidianignore` 缺失] → 默认排除 `.obsidian/` 和 `Templates/`，用户可自定义覆盖
- [math block 正则误匹配] → `$$` 必须独占行才触发保护（`^\$\$` multiline），减少误匹配
- [文件移动后 hash 不变但路径变化] → 按 source_path 匹配而非 content_hash，路径变化会被识别为"新增 + 删除"，内容会重新 ingest。可接受（embedding cache 命中率高，不会重复 embed）
