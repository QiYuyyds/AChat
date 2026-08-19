## Context

记忆系统的 file-native 架构已有完整的 wikilink 邻接图：

- **数据层**：`WikilinkExpander`（`backend/app/memory/search/wikilink_expander.py`）在 SQLite `wikilinks.db` 中管理 `source_path → target_path` 边，带可选 `predicate` 列。已有 `get_outlinks(path)` / `get_inlinks(path)` / `expand(seeds, hops)` 等单跳查询方法，但缺少全量边查询。
- **索引层**：`AutoIndex.full_reindex()` 在启动时扫描 `daily/` + `digest/` 全部 `.md` 文件，提取 wikilink 并写入邻接图。
- **API 层**：`backend/app/api/memory.py` 已有文件 CRUD、搜索、偏好、会话摘要等端点。搜索结果已携带 `expansion: { outlinks, inlinks }` 字段。
- **前端层**：记忆面板（`src/components/cognition-main-panel.tsx` 的 `MemoryTabContent`）已有三个子 Tab：长期记忆 / 用户偏好 / 会话摘要。`MemoryTab` 类型定义为 `'long-term' | 'preferences' | 'session'`。
- **现有依赖**：`package.json` 中已有 `@xyflow/react`（React Flow，偏 DAG），但无力导向布局能力。没有 d3 相关包。

## Goals / Non-Goals

**Goals:**

- 后端提供一次性返回全量图数据的 API（节点 + 边）
- 前端用 d3-force 力导向布局在 Canvas 上渲染记忆图谱
- 节点视觉编码：颜色按 bucket、大小按 degree
- 边视觉编码：样式按 predicate 区分（实线 / 虚线）
- 点击节点：高亮邻居 + 暗化无关节点 + 右侧弹出文件详情面板
- 支持缩放、拖拽平移
- 数据规模策略：支持 bucket / agent_id / min_degree 过滤

**Non-Goals:**

- 不做图算法分析（聚类、最短路、PageRank 等）
- 不做实时 wikilink 编辑（拖拽创建新边）——只读视图
- 不做 3D 图谱
- 不修改现有记忆文件 CRUD / 搜索 / pipeline 流程
- 不做图谱的 SSE 实时更新（手动刷新即可）

## Decisions

### D1: 渲染方案 — d3-force + Canvas

**选择**：`d3-force`（力导向布局）+ 原生 Canvas 2D 渲染 + `d3-zoom`（缩放平移）

**理由**：
- d3-force 是力导向布局的事实标准，社区成熟，算法质量高
- Canvas 渲染性能优于 SVG（节点 > 100 时差距明显）
- `d3-zoom` 提供成熟的缩放/平移事件处理
- 不用 `react-force-graph` 封装，因为它的 React 集成层会引入额外复杂度，且我们已有 Canvas 渲染经验

**备选方案**：
- `react-force-graph-2d`：React 封装好但依赖较重（~100KB），且定制灵活性不如直接用 d3
- `Cytoscape.js`：图算法内置但 API 学习曲线高，风格定制困难
- 纯 SVG：节点少时可行，但 > 100 节点会卡

### D2: 后端 API 设计 — 全量返回 + 过滤参数

**选择**：`GET /api/memory/graph` 一次性返回全量 nodes + edges

**参数**：
- `bucket` (optional): 过滤特定 bucket 的节点
- `agent_id` (optional): 过滤特定 agent 的节点
- `min_degree` (optional, default=0): 过滤孤立节点（degree < N 的不返回）

**返回结构**：
```json
{
  "nodes": [
    {
      "path": "digest/wiki/python.md",
      "name": "Python 基础",
      "bucket": "wiki",
      "importance": 0.8,
      "tags": ["python", "语言"],
      "description": "Python 语言核心概念",
      "degree": 5
    }
  ],
  "edges": [
    {
      "source": "digest/wiki/python.md",
      "target": "digest/wiki/asyncio.md",
      "predicate": null
    }
  ]
}
```

**理由**：
- 记忆文件规模通常在几十到几百，一次性返回完全可行
- degree 过滤让用户能隐藏孤立节点，减少视觉噪音
- 不做分页——图数据天然需要全量才能正确布局

**备选方案**：
- 按需加载（先返回 top-N 高 degree 节点，点击展开邻居）：复杂度高，且 wikilink 数据量不大，不需要

### D3: 节点交互 — 点击高亮 + 右侧详情面板

**选择**：
- 单击节点：高亮该节点及其 1 跳邻居，暗化其他节点和边，同时在右侧弹出文件详情面板
- 双击节点：以该节点为中心重新布局（可选增强，v1 可不做）
- 右侧面板复用现有 `MemoryDetailView` 的结构（name / description / tags / importance / body）
- 面板数据通过已有 `GET /api/memory/files/{path}` 端点获取（不重复造轮子）

**理由**：
- 复用已有文件详情 API 和渲染逻辑，减少代码重复
- 高亮 + 暗化是图可视化的标准交互模式

### D4: 视觉编码

**节点颜色**（复用现有 `BUCKET_CONFIG`）：
| bucket | 颜色 |
|--------|------|
| procedure | blue |
| personal | violet |
| wiki | emerald |
| daily | amber |

**节点大小**：`radius = 6 + Math.min(degree, 10) * 1.5`（degree 越高越大，上限封顶）

**边样式**：
| predicate | 样式 |
|-----------|------|
| NULL | 实线，灰色 `rgba(150,150,150,0.3)` |
| `derived_from` | 虚线 `setLineDash([4,4])`，灰色 |
| 其他 | 点线 `setLineDash([2,3])`，带 predicate 颜色 |

### D5: MemoryTab 扩展位置

**选择**：在 `MEMORY_SUBTABS` 数组中 `'long-term'` 之后插入 `'graph'`

```typescript
const MEMORY_SUBTABS = [
  { id: 'long-term', label: '长期记忆', icon: Brain },
  { id: 'graph', label: '图谱', icon: ShareNetwork },  // ← 新增
  { id: 'preferences', label: '用户偏好', icon: UserCog },
  { id: 'session', label: '会话摘要', icon: Sparkles },
]
```

**理由**：图谱是长期记忆的关联可视化，逻辑上紧随其后。

## Risks / Trade-offs

- **[风险] 节点数量过多时性能下降** → 缓解：Canvas 渲染 + `min_degree` 过滤 + bucket 筛选。> 500 节点时可考虑增加 `max_nodes` 参数限制返回数量。
- **[风险] d3-force 布局不稳定（抖动不收敛）** → 缓解：设置 `alphaDecay` / `velocityDecay` 参数，并在 `alpha < 0.01` 时 `simulation.stop()`。
- **[风险] 新依赖增加 bundle 体积** → 缓解：d3-force / d3-zoom / d3-selection 按需引入，tree-shaking 后约 30-40KB。
- **[权衡] 全量返回 vs 按需加载** → 选择全量返回，简化实现。如果未来记忆文件超过 1000 个，可改为按需加载。
- **[风险] wikilink 目标路径不一致**（绝对路径 vs 相对路径）→ 缓解：`AutoIndex._normalize_link_target` 已将路径归一化为 workspace-relative posix，API 返回时保持一致。
