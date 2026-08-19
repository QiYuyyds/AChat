## ADDED Requirements

### Requirement: Graph Data API

系统 SHALL 提供 `GET /api/memory/graph` 端点，返回记忆文件wikilink 邻接图的全量节点和边数据。

- 端点路径：`GET /api/memory/graph`
- 认证：需要 JWT（同其他 memory 端点）
- 查询参数（全部 optional）：
  - `bucket`：按 bucket 过滤节点（`procedure` / `personal` / `wiki` / `daily`）
  - `agent_id`：按 agent 过滤节点
  - `min_degree`：过滤 degree 低于此值的节点（默认 `0`，即不过滤）
- 返回结构：
  ```json
  {
    "nodes": [{ "path", "name", "bucket", "importance", "tags", "description", "degree" }],
    "edges": [{ "source", "target", "predicate" }]
  }
  ```
- 节点 `path` SHALL 为 workspace-relative posix 路径
- 边的 `source` 和 `target` SHALL 与节点 `path` 一致
- `degree` = 该节点的入边 + 出边数量
- 当 `bucket` 过滤时，只有该 bucket 的节点返回，但边的两端必须都在返回的节点集合中
- 当 `min_degree > 0` 时，degree 低于此值的节点不返回，关联的边也不返回

#### Scenario: 全量图数据获取

- **WHEN** 用户请求 `GET /api/memory/graph`（无参数）
- **THEN** 返回全部记忆文件的节点数据和全部 wikilink 边数据
- **AND** 每个节点包含 `path` / `name` / `bucket` / `importance` / `tags` / `description` / `degree` 字段
- **AND** 每条边包含 `source` / `target` / `predicate` 字段

#### Scenario: 按 bucket 过滤

- **WHEN** 用户请求 `GET /api/memory/graph?bucket=wiki`
- **THEN** 只返回 bucket 为 `wiki` 的节点
- **AND** 边的两端节点必须都在返回的节点集合中

#### Scenario: 按 min_degree 过滤

- **WHEN** 用户请求 `GET /api/memory/graph?min_degree=2`
- **THEN** degree < 2 的节点不返回
- **AND** 关联到被过滤节点的边也不返回

#### Scenario: 空图

- **WHEN** 记忆系统中没有任何 wikilink 边
- **THEN** 返回 `{ "nodes": [], "edges": [] }`
- **AND** HTTP 状态码为 200

### Requirement: WikilinkExpander 全量边查询

`WikilinkExpander` SHALL 提供 `get_all_edges()` 方法，返回 `wikilinks` 表中全部边。

- 方法签名：`get_all_edges() -> list[dict[str, str | None]]`
- 返回格式：`[{"source": str, "target": str, "predicate": str | None}, ...]`
- SHALL 在 SQLite 连接不可用时返回空列表

#### Scenario: 获取全部边

- **WHEN** 调用 `get_all_edges()`
- **THEN** 返回 `wikilinks` 表中所有行
- **AND** 每行包含 `source_path`（映射为 `source`）、`target_path`（映射为 `target`）、`predicate`

### Requirement: MemoryService 图数据聚合

`MemoryService` SHALL 提供 `get_graph_data()` 方法，聚合节点 frontmatter 和边数据。

- 方法签名：`async def get_graph_data(bucket: str | None = None, agent_id: str | None = None, min_degree: int = 0) -> dict`
- SHALL 调用 `WikilinkExpander.get_all_edges()` 获取全部边
- SHALL 从边的 source 和 target 去重得到节点路径集合
- SHALL 对每个节点路径读取 frontmatter（`name` / `bucket` / `importance` / `tags` / `description`）
- SHALL 计算每个节点的 degree（入边 + 出边数量）
- SHALL 按 `bucket` / `agent_id` / `min_degree` 参数过滤
- SHALL 返回 `{"nodes": [...], "edges": [...]}` 结构

#### Scenario: 聚合图数据

- **WHEN** 调用 `get_graph_data()`
- **THEN** 返回所有记忆文件的节点和全部 wikilink 边
- **AND** 每个节点包含 frontmatter 元数据和 degree

#### Scenario: 过滤后聚合

- **WHEN** 调用 `get_graph_data(bucket="wiki", min_degree=2)`
- **THEN** 只返回 wiki bucket 中 degree >= 2 的节点
- **AND** 边的两端必须都在返回的节点集合中

### Requirement: 记忆图谱 Tab

前端记忆面板 SHALL 新增一个 "图谱" 子 Tab，ID 为 `'graph'`。

- `MemoryTab` 类型 SHALL 扩展为 `'long-term' | 'graph' | 'preferences' | 'session'`
- Tab 列表中 "图谱" Tab SHALL 位于 "长期记忆" 之后
- Tab 图标 SHALL 使用 `ShareNetwork`（来自 lucide-react）
- 点击 "图谱" Tab SHALL 渲染 `MemoryGraphPanel` 组件

#### Scenario: Tab 切换

- **WHEN** 用户点击记忆面板中的 "图谱" Tab
- **THEN** 主内容区渲染 `MemoryGraphPanel` 组件
- **AND** 不影响其他 Tab 的功能

### Requirement: 力导向图渲染

`MemoryGraphPanel` 组件 SHALL 使用 `d3-force` 力导向布局 + Canvas 2D 渲染记忆图谱。

- 组件挂载后 SHALL 调用 `fetchMemoryGraph()` 获取图数据
- SHALL 创建 d3-force simulation，配置 `forceLink` / `forceManyBody` / `forceCenter`
- SHALL 在 Canvas 上绘制节点（圆）和边（线）
- 节点颜色 SHALL 按 bucket 区分：procedure=blue, personal=violet, wiki=emerald, daily=amber
- 节点半径 SHALL 按 degree 缩放：`radius = 6 + Math.min(degree, 10) * 1.5`
- 边样式 SHALL 按 predicate 区分：NULL=实线, `derived_from`=虚线, 其他=点线
- SHALL 支持 `d3-zoom` 缩放和拖拽平移
- SHALL 在 simulation `alpha < 0.01` 时停止动画循环

#### Scenario: 渲染图数据

- **WHEN** `MemoryGraphPanel` 挂载并成功获取图数据
- **THEN** Canvas 上渲染力导向布局的节点和边
- **AND** 节点颜色按 bucket 区分
- **AND** 节点大小按 degree 区分

#### Scenario: 空数据状态

- **WHEN** 图数据为空（无记忆文件或无 wikilink）
- **THEN** 显示空状态提示（"暂无记忆图谱，Agent 在对话中积累的 wikilink 关联会在这里可视化展示"）

#### Scenario: 加载状态

- **WHEN** 正在请求图数据
- **THEN** 显示加载动画（Loader2 spin）

### Requirement: 节点点击交互

用户 SHALL 能点击图中的节点触发高亮和文件详情。

- 单击节点 SHALL：
  - 高亮该节点及其 1 跳邻居节点和边
  - 暗化无关节点和边（降低 opacity）
  - 在右侧弹出文件详情面板
- 文件详情面板 SHALL 通过 `GET /api/memory/files/{path}` 获取完整文件内容
- 详情面板 SHALL 复用现有 `MemoryDetailView` 的渲染结构
- 点击空白区域 SHALL 取消高亮并关闭详情面板

#### Scenario: 点击节点

- **WHEN** 用户单击图中某个节点
- **THEN** 该节点及其 1 跳邻居高亮显示
- **AND** 其他节点和边暗化
- **AND** 右侧弹出文件详情面板，展示该节点的完整记忆文件内容

#### Scenario: 取消选中

- **WHEN** 用户点击图中空白区域
- **THEN** 所有节点和边恢复正常显示
- **AND** 文件详情面板关闭

### Requirement: 图谱数据筛选

`MemoryGraphPanel` SHALL 提供 bucket 筛选控件。

- 面板顶部 SHALL 显示 bucket 筛选按钮（全部 / 经验 / 个人 / 知识 / 日常）
- 点击筛选按钮 SHALL 重新请求图数据（带 `bucket` 参数）
- SHALL 显示节点总数和边总数统计

#### Scenario: 按 bucket 筛选

- **WHEN** 用户点击 "知识" 筛选按钮
- **THEN** 重新请求 `GET /api/memory/graph?bucket=wiki`
- **AND** 图谱重新渲染只包含 wiki bucket 的节点和边

#### Scenario: 显示统计

- **WHEN** 图数据加载完成
- **THEN** 面板顶部显示 "N 个节点 · M 条边" 统计信息

### Requirement: Graph API 客户端

前端 `src/lib/api/memory.ts` SHALL 提供 graph 数据的 API 客户端函数和类型定义。

- 类型定义：
  - `GraphNode`：`{ path, name, bucket, importance, tags, description, degree }`
  - `GraphEdge`：`{ source, target, predicate }`
  - `GraphData`：`{ nodes: GraphNode[], edges: GraphEdge[] }`
- 函数：`fetchMemoryGraph(params: { bucket?, agentId?, minDegree? }): Promise<GraphData>`
- SHALL 使用 `authFetch` 发起请求（同其他 memory API 函数）

#### Scenario: 调用 fetchMemoryGraph

- **WHEN** 调用 `fetchMemoryGraph({ bucket: 'wiki' })`
- **THEN** 发起 `GET /api/memory/graph?bucket=wiki` 请求
- **AND** 返回解析后的 `GraphData` 对象
