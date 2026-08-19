## Why

记忆系统的 wikilink 邻接图数据已经完整存在于后端 SQLite 中（`WikilinkExpander` 管理的 `wikilinks` 表），但前端只能通过搜索结果的 `expansion` 字段间接看到单跳邻居。用户无法直观感知记忆文件之间的关联拓扑，也缺乏交互式探索记忆网络的手段。需要在记忆面板新增一个"图谱"Tab，用力导向图（force-directed graph）展示全部记忆节点和 wikilink 边，支持点击节点查看文件详情并高亮邻居。

## What Changes

- **后端新增 graph API**：`GET /api/memory/graph` 返回全量节点和边数据，支持 `bucket` / `agent_id` / `min_degree` 过滤参数
- **`WikilinkExpander` 新增 `get_all_edges()` 方法**：一次性获取全部 wikilink 边
- **`MemoryService` 新增 `get_graph_data()` 方法**：聚合节点 frontmatter + 边数据，计算 degree
- **前端新增 `MemoryTab = 'graph'`**：在记忆面板的 Tab 列表中加入"图谱"选项
- **前端新增 `MemoryGraphPanel` 组件**：基于 `d3-force` + Canvas 的力导向图渲染，节点颜色按 bucket 区分、大小按 degree 缩放、边样式按 predicate 区分
- **前端新增 graph API 客户端**：`fetchMemoryGraph()` 函数和 `GraphNode` / `GraphEdge` / `GraphData` 类型定义
- **点击节点交互**：高亮节点及其邻居、暗化无关节点、右侧弹出文件详情面板（复用现有 `MemoryDetailView` 结构）
- **引入新依赖 `d3-force` + `d3-zoom` + `d3-selection`**

## Capabilities

### New Capabilities

- `memory-graph-view`: 记忆图谱可视化能力——后端 graph 数据 API + 前端力导向图渲染 + 节点交互

### Modified Capabilities

（无——本次变更不修改已有 spec 的 requirement 级别行为）

## Impact

- **后端代码**：
  - `backend/app/memory/search/wikilink_expander.py` — 新增 `get_all_edges()` 方法
  - `backend/app/memory/memory_service.py` — 新增 `get_graph_data()` 方法
  - `backend/app/api/memory.py` — 新增 `GET /api/memory/graph` 端点
- **前端代码**：
  - `src/stores/app-store.ts` — `MemoryTab` 类型扩展 `'graph'`
  - `src/lib/api/memory.ts` — 新增 graph 类型定义和 `fetchMemoryGraph()` 函数
  - `src/components/settings/memory-management/memory-graph-panel.tsx` — 新增图谱面板组件
  - `src/components/cognition-main-panel.tsx` — Tab 列表注册图谱 Tab
  - `src/components/memory-library.tsx` — Tab 列表同步注册
- **新依赖**：`d3-force` + `d3-zoom` + `d3-selection`（前端）
- **不影响**：现有记忆文件 CRUD、搜索、pipeline、preference、session memory 等功能不受影响