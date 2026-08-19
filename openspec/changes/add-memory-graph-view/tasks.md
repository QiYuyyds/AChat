## 1. 后端：WikilinkExpander 全量边查询

- [x] 1.1 在 `backend/app/memory/search/wikilink_expander.py` 中新增 `get_all_edges()` 方法，返回 `wikilinks` 表全部行（`source` / `target` / `predicate`）
- [x] 1.2 为 `get_all_edges()` 编写单元测试（`backend/tests/test_wikilink_expander.py` 或新建文件），覆盖空表、单条边、多条带 predicate 边

## 2. 后端：MemoryService 图数据聚合

- [x] 2.1 在 `backend/app/memory/memory_service.py` 中新增 `get_graph_data(bucket, agent_id, min_degree)` 方法，调用 `get_all_edges()` 并聚合节点 frontmatter + degree
- [x] 2.2 实现 bucket / agent_id / min_degree 过滤逻辑（过滤节点后同步裁剪边）
- [x] 2.3 为 `get_graph_data()` 编写单元测试，覆盖无参数全量、bucket 过滤、min_degree 过滤、空图

## 3. 后端：Graph API 端点

- [x] 3.1 在 `backend/app/api/memory.py` 中新增 `GET /api/memory/graph` 端点，调用 `MemoryService.get_graph_data()`
- [x] 3.2 验证端点认证（JWT）、错误处理（MemoryService 未初始化时返回 503）
- [x] 3.3 为端点编写集成测试（`backend/tests/test_api_memory.py` 或新建文件），覆盖 200 正常返回、503 未初始化、过滤参数

## 4. 前端：类型定义和 API 客户端

- [x] 4.1 在 `src/lib/api/memory.ts` 中新增 `GraphNode` / `GraphEdge` / `GraphData` 类型定义
- [x] 4.2 新增 `fetchMemoryGraph(params)` 函数，发起 `GET /api/memory/graph` 请求
- [x] 4.3 运行 `pnpm typecheck` 验证类型正确

## 5. 前端：Store 扩展

- [x] 5.1 在 `src/stores/app-store.ts` 中将 `MemoryTab` 类型扩展为 `'long-term' | 'graph' | 'preferences' | 'session'`
- [x] 5.2 运行 `pnpm typecheck` 验证类型正确

## 6. 前端：安装新依赖

- [x] 6.1 运行 `pnpm add d3-force d3-zoom d3-selection` 安装力导向布局依赖
- [x] 6.2 运行 `pnpm add -D @types/d3-force @types/d3-zoom @types/d3-selection` 安装类型声明
- [x] 6.3 验证 `pnpm typecheck` 通过

## 7. 前端：MemoryGraphPanel 组件

- [x] 7.1 创建 `src/components/settings/memory-management/memory-graph-panel.tsx` 组件文件
- [x] 7.2 实现 Canvas 容器 + d3-force simulation 初始化（`forceLink` / `forceManyBody` / `forceCenter`）
- [x] 7.3 实现节点渲染（圆，颜色按 bucket，大小按 degree）
- [x] 7.4 实现边渲染（线，样式按 predicate：实线 / 虚线 / 点线）
- [x] 7.5 实现 d3-zoom 缩放和拖拽平移
- [x] 7.6 实现加载状态（Loader2 spin）和空数据状态
- [x] 7.7 实现节点点击交互（高亮邻居 + 暗化其他 + 弹出右侧详情面板）
- [x] 7.8 实现右侧文件详情面板（调用 `readMemoryFile(path)` 获取完整内容，复用 `MemoryDetailView` 渲染结构）
- [x] 7.9 实现点击空白取消选中（恢复正常显示 + 关闭详情面板）
- [x] 7.10 实现 bucket 筛选控件（全部 / 经验 / 个人 / 知识 / 日常）
- [x] 7.11 实现节点总数 + 边总数统计显示
- [x] 7.12 运行 `pnpm typecheck` 和 `pnpm lint` 验证

## 8. 前端：Tab 注册

- [x] 8.1 在 `src/components/cognition-main-panel.tsx` 的 `MEMORY_SUBTABS` 数组中，在 `long-term` 之后插入 `{ id: 'graph', label: '图谱', icon: ShareNetwork }`
- [x] 8.2 在 `MemoryTabContent` 中新增 `{subtab === 'graph' && <MemoryGraphPanel />}` 渲染分支
- [x] 8.3 在 `src/components/memory-library.tsx` 的 `TABS` 数组中同步新增 graph Tab
- [x] 8.4 运行 `pnpm typecheck` 和 `pnpm lint` 验证

## 9. 端到端验证

- [x] 9.1 启动后端 + 前端，验证图谱 Tab 出现在记忆面板中
- [x] 9.2 验证无记忆文件时显示空状态
- [x] 9.3 创建带 wikilink 的记忆文件，验证图谱渲染节点和边
- [x] 9.4 验证点击节点高亮邻居 + 弹出文件详情
- [x] 9.5 验证 bucket 筛选功能
- [x] 9.6 验证缩放和拖拽平移
