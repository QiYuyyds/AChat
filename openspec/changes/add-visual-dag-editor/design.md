## Context

AChat 的 `dispatch_plan` 工具让 Orchestrator LLM 生成结构化 DAG（任务+依赖），经 `validate_dag` → `topological_waves` → `execute_dag` 波调度执行。当 `plan_approval_enabled=True` 时，DAG 先挂起到 `PendingDispatchPlansStore`，发 `dispatch.plan.pending` SSE，前端渲染 `DispatchPlanCard`，用户 approve/reject/revise。

当前前端用 `PlanTaskList`（flat 列表）渲染 DAG，依赖关系以文字 "→ t1, t2" 展示。3+ 节点的复杂 DAG 结构不直观。且用户只能 approve/reject 二选一或用自然语言 revise，无法直接编辑 DAG。

现有基础设施已 90% 就绪：`validate_dag` 校验逻辑、`execute_dag` 执行引擎、`_revalidation_validator` approve 时再验机制、`PlanReviewOutcome` 三种结果（approve/reject/revise）均已存在。

## Goals / Non-Goals

**Goals:**
- 用可视化 DAG 图替代 flat 列表，让用户直观看到任务节点和依赖关系
- DAG 图在执行中实时反映节点状态（pending→running→complete/failed/skipped）
- 审批阶段允许用户可视化编辑 DAG（增删节点、改依赖、换 Agent、编辑任务描述）
- 编辑后的 DAG 经后端 `validate_dag` 重新校验后执行，不破坏现有安全链

**Non-Goals:**
- 不做 Coze 式完整可视化工作流编辑器（节点类型 = LLM/代码/分支/API 等）
- 不引入新的 DAG 节点类型（节点仍是一个完整 Agent Loop，不是单步操作）
- 不改 `DispatchPlanItem` schema 字段
- 不改 StreamEvent 协议
- 不改 `validate_dag`/`topological_waves`/`execute_dag` 核心逻辑
- 不持久化编辑后的 DAG（编辑只在 pending 阶段存在于内存，approve 后交给 execute_dag 执行）
- 不做 DAG 模板保存/复用（未来可能扩展，本变不涉及）

## Decisions

### Decision 1: React Flow + dagre 作为 DAG 图渲染方案

**选择**: `@xyflow/react`（React Flow v12）+ `@dagrejs/dagre`

**理由**:
- React Flow 是 React 生态最主流的节点图库，原生支持 Custom Node、拖拽连线、缩放平移
- dagre 是经典的 DAG 自动布局算法，支持 `rankdir` 方向控制和 `nodesep`/`ranksep` 间距
- 两者都是 TypeScript 友好，与 Next.js 16 + React 19 + shadcn/ui 技术栈兼容
- React Flow 的 Custom Node 天然支持根据 `DispatchTaskStatus` 渲染不同样式

**备选**:
- D3.js：太底层，需要手写拖拽/连线/缩放，开发量大 3x+
- vis-network：功能够但 React 集成差，样式定制困难
- 6px/react-flow：同 React Flow 但社区更小

### Decision 2: 后端 approve 接受可选 modified_plan，而非新增 API 端点

**选择**: 扩展现有 `POST /pending-dispatch-plans/{plan_id}` 的 `action: "approve"` 请求体，新增可选 `plan: DispatchPlanItem[]` 字段

**理由**:
- `PendingDispatchPlansStore.approve()` 已有 `_revalidation_validator` → `validate_dag` 再验机制
- 只需加一个可选参数 `modified_plan`，替换 `pending_plan.plan` 后走已有校验+执行链路
- 不新增端点、不新增事件类型、不改 `PlanReviewOutcome` 结构
- 用户体验：一个 POST 请求完成"编辑 + 确认"，原子操作

**备选**:
- 新增 `PUT /pending-dispatch-plans/{plan_id}` 单独更新 plan 再 approve：两步操作，有竞态问题（更新后 plan 可能在 approve 前过期）
- 新增 `action: "edit"` 再 `action: "approve"`：复杂化状态机，无收益

### Decision 3: 前端实时校验镜像 `validateDagFrontend()`

**选择**: 在 `src/lib/dag-validate.ts` 中用 TypeScript 镜像 `validate_dag` 的校验逻辑（重复 id / 自依赖 / 缺失引用 / 环检测），编辑时实时调用

**理由**:
- `validate_dag` 逻辑很轻（~30 行 Python），镜像成本低
- 编辑时即时反馈（按钮禁用 + 错误提示）比提交后才发现错误体验好得多
- 后端 `approve` 时仍会跑 `_revalidation_validator` 做权威校验，前端校验是 UX 增强 not 安全依赖

**备选**:
- 只靠后端校验：编辑体验差，用户改了环要等提交才知道
- 调后端 API 实时校验：每次编辑发请求，延迟高且不必要

### Decision 4: 只读 DAG 图在 plan_approval_enabled=False 时也生效

**选择**: `DispatchPlanReadOnlyCard` 中的 DAG 图不依赖审批开关，只要有 `dispatch.plan` 事件就渲染

**理由**:
- `dispatch.plan` 事件在 DAG 执行时就会发射（不只在审批流程中），只读 DAG 图展示执行进度对所有用户都有价值
- 即使没开审批的用户也能看到 DAG 结构和实时进度

### Decision 5: 小 DAG 降级为列表

**选择**: 当 `plan.length <= 2` 且无 `dependsOn` 时，仍用列表渲染；否则用 DAG 图

**理由**:
- 1-2 个无依赖节点的 DAG 图视觉效果不如列表直观
- 避免简单场景下画布过度留白

## Risks / Trade-offs

- **[React Flow 包体积 ~150KB gzipped]** → 可接受：AChat 是本地运行桌面应用，非移动端首屏场景；且只在有 dispatch plan 的会话才加载该组件，可用 `next/dynamic` 懒加载
- **[dagre 布局不稳定]** → dagre 对相同拓扑结构产出确定性布局，但节点数变化时位置可能跳变。缓解：编辑模式下手动定位优先（用户拖拽位置记忆），dagre 仅做初始布局
- **[前端校验与后端校验逻辑漂移]** → 前端 `validateDagFrontend` 是镜像，后端 `validate_dag` 是权威。后端逻辑变更时需同步。缓解：在前端函数注释中标注"镜像 backend/app/services/dag_executor.py:validate_dag"
- **[编辑后 plan 的 agentId 校验]** → 用户编辑时可能指定不在会话中的 agentId。缓解：前端从 `agents` store 获取可选项，后端 `_verify_agents_in_conversation` 已有校验，approve 时会再验
- **[编辑模式下 SSE 状态更新冲突]** → 编辑模式只在 `reviewStatus='pending'` 时激活，此时 DAG 尚未执行，无 `dispatch.start/end` 事件。approve 后切换只读模式才开始接收状态更新，无冲突
