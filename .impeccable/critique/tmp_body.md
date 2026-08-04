⚠️ DEGRADED: single-context (no sub-agent tool exposed in this session)

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2 | 初始加载无 loading 指示；搜索过滤无即时反馈 |
| 2 | Match System / Real World | 3 | "联系人"隐喻清晰；"项能力"措辞略模糊 |
| 3 | User Control and Freedom | 3 | 删除有确认对话框；但删除后不可撤销 |
| 4 | Consistency and Standards | 2 | 多处偏离 DESIGN.md：input h-10 vs h-8、header text-xl vs 1rem、border/40 vs /10、10px 字号 |
| 5 | Error Prevention | 3 | 删除确认 + builtin 不可删；搜索无验证 |
| 6 | Recognition Rather Than Recall | 3 | 卡片信息完整；但 edit/delete 仅 hover 可见 |
| 7 | Flexibility and Efficiency | 2 | 无键盘快捷键；无批量操作；无排序选项 |
| 8 | Aesthetic and Minimalist Design | 3 | 布局干净；但 header 与内容区视觉权重失衡 |
| 9 | Error Recovery | 1 | 删除失败仅 console.error，用户无感知 |
| 10 | Help and Documentation | 2 | 空状态有引导；但无上下文帮助 |
| **Total** | | **24/40** | **Acceptable** |

## Design Specificity Verdict

**LLM assessment**: 联系人主面板（`agent-main-panel.tsx`）是一个功能完整但设计特异性偏低的 Agent 管理界面。header-icon + title + description + CTA 的模式是通用管理面板的标准模板，卡片网格 + hover 操作也是 CRUD UI 的常见模式。面板没有体现 AChat "The Clarity Lab" 设计系统的独特气质——Apple-inspired 微冷中性灰 + System Blue 的色彩语言存在，但 header 的 `text-xl font-bold` 和 search input 的 `h-10` 打破了 DESIGN.md 的紧凑密度规则，让面板从 "精密仪器" 滑向 "通用后台"。

面板的结构性同质化体现在：header → search → section header → grid → empty state 的线性布局与任何联系人/资源管理面板可互换。缺少能体现 "多 Agent 协作工作空间" 这一产品定位的独特信号——比如 Agent 的在线状态、最近活跃会话、工具能力可视化等。

**Deterministic scan**: 检测器发现 4 个 advisory 级问题，全部是 `text-[10px]` 字号偏离 DESIGN.md 类型阶梯（行 194, 199, 214, 218）。这些是 "内置" / "Orchestrator" badge 和 adapter 名称 / 能力计数使用的 10px 字号，低于 DESIGN.md 最小的 12px label。这是一个真实的类型系统一致性问题。

## Overall Impression

面板功能闭环，信息架构合理，但设计系统执行有多处偏离。header 视觉权重过大（text-xl font-bold），与 DESIGN.md 的 "通过 weight 500 建立层级，不通过字号跳跃" 原则冲突。搜索输入框 h-10（40px）与按钮 h-8（32px）不同高，破坏了 "视觉节奏的基石"。卡片设计较好——信息分组清晰、hover 操作合理，但 border-border/40 比 DESIGN.md 的 hairline（10%）重了四倍。最大的机会：让面板从 "通用 CRUD 后台" 变成 "Agent 编排控制台"。

## What's Working

1. **卡片信息架构**：avatar → name + badges → description → adapter + capabilities 的分组层次清晰，每张卡片是一个自足的信息单元。`line-clamp-2` 和 `truncate` 的使用恰当。

2. **删除确认流程**：AlertDialog 提供了明确的后果说明（"已使用该 Agent 的会话将无法继续使用它"），builtin agent 不可删除的按钮隐藏逻辑正确。这是 error prevention 的好实践。

3. **响应式网格**：`grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4` 的递进断点合理，在不同屏幕尺寸下卡片密度自适应。

## Priority Issues

**[P1] Header 字号偏离 DESIGN.md 类型阶梯**
- **Why it matters**: header 使用 `text-xl font-bold`（20px, 700），而 DESIGN.md 明确规定 heading 为 1rem / 500 weight。这破坏了 "The Single Family Rule"——"标题层级通过 weight 500 建立，不通过字号跳跃"。header 视觉权重过大，与紧凑的卡片内容区形成断层。
- **Fix**: 将 header 标题改为 `text-base font-medium` 或 `text-lg font-medium`，与 DESIGN.md heading 规格对齐。
- **Suggested command**: `/impeccable typeset`

**[P1] 搜索输入框高度与按钮不同高**
- **Why it matters**: search input 是 `h-10`（40px），而 DESIGN.md 规定 input 和 button 同高 h-8（32px）。这破坏了 "保持按钮和输入框同高——视觉节奏的基石" 的 Do 规则。40px vs 32px 的差异在视觉上明显。
- **Fix**: 将 search input 改为 `h-8`，与 "创建 Agent" 按钮同高。调整 padding 以保持可读性。
- **Suggested command**: `/impeccable layout`

**[P1] 10px 字号偏离类型阶梯**
- **Why it matters**: 4 处 `text-[10px]` 使用（badge "内置"/"Orchestrator"、adapter 名称、能力计数）低于 DESIGN.md 最小的 12px label。检测器正确捕获。10px 在小屏上可读性差。
- **Fix**: 将所有 `text-[10px]` 改为 `text-xs`（12px），或更新 DESIGN.md 类型阶梯增加 10px 档位（不推荐——会稀释系统）。
- **Suggested command**: `/impeccable typeset`

**[P2] 卡片边框透明度偏重**
- **Why it matters**: 卡片使用 `border-border/40`（40% 透明度），而 DESIGN.md 规定边框为 10% 透明度（hairline）。40% 比 hairline 重了四倍，与 "1px Hairline 是这个系统的全部边框语言" 的 Don't 规则冲突。
- **Fix**: 将 `border-border/40` 改为 `border-border`（使用默认 10% 透明度）。如果需要更强的视觉分隔，用 tonal layering（bg-card vs bg-background）而非加重边框。
- **Suggested command**: `/impeccable polish`

**[P2] 按钮自定义效果偏离设计系统**
- **Why it matters**: "创建 Agent" 按钮有 `shadow-sm transition-all duration-200 hover:shadow-md active:scale-95`。DESIGN.md 规定按钮 active 状态为 `translate-y-px`，且 "Flat-By-Default" 原则意味着不应默认加阴影。`active:scale-95` 是非标准的。
- **Fix**: 移除 `shadow-sm hover:shadow-md active:scale-95`，使用 shadcn button 默认的 `active:translate-y-px` 行为。
- **Suggested command**: `/impeccable polish`

**[P2] 删除失败无用户可见反馈**
- **Why it matters**: `confirmDelete` 的 catch 块仅 `console.error`，用户看不到删除失败。Nielsen H9 评分 1 分。用户会以为删除成功了。
- **Fix**: 添加 toast 或 inline error message，明确告知用户删除失败及原因。
- **Suggested command**: `/impeccable harden`

## Persona Red Flags

**Alex (Power User)**:
- 无键盘快捷键：创建 Agent、搜索、编辑、删除全部需要鼠标点击。Alex 期望 `Ctrl+N` 创建、`/` 聚焦搜索、`E` 编辑、`Delete` 删除。
- 无批量操作：不能多选 Agent 批量删除或批量配置工具。
- 无排序选项：列表只能按默认顺序展示，不能按名称、最近使用、adapter 类型排序。
- hover-only 操作：Alex 用键盘导航时无法发现 edit/delete 按钮（`opacity-0 group-hover:opacity-100`）。

**Sam (Accessibility-Dependent User)**:
- hover-only 操作按钮 (`opacity-0 group-hover:opacity-100`) 对键盘用户不可见。虽然 button 有 `aria-label`，但视觉上完全隐藏。
- search input 的 focus 样式被覆盖为 `focus-visible:ring-2 focus-visible:ring-primary/20`，比设计系统默认的 `ring-3 ring-ring/50` 弱。
- `text-[10px]` badge 文字在 200% 浏览器缩放下可能不可读。
- 删除失败无 ARIA live region 通知。

**Agent Orchestrator（项目特定 Persona — "同时管理 3+ Agent 的开发者"）**:
- 面板不显示 Agent 的最近使用状态或活跃会话数——无法快速判断哪个 Agent 正在哪个会话中使用。
- 卡片没有显示 Agent 的 model/provider 信息——要确认用的是什么模型需要点进编辑对话框。
- "项能力" 这个措辞太模糊——看到 "9 项能力" 不知道具体是什么。
- 没有 "最近使用" 或 "收藏" 排序——在 10+ Agent 时找目标 Agent 需要搜索或滚动。

## Minor Observations

- `bg-background/85 backdrop-blur-2xl` 的半透明背景效果在 DESIGN.md 中没有定义，这是一个未文档化的视觉决策。
- `ring-1 ring-border/30` 在 avatar 外圈的使用偏离了设计系统的 border 语言（应该用 `border-border` 默认值）。
- EmptyState 的 `Users` 图标在 `size-16` 容器里用 `size-8` 图标——图标与容器的比例合理。
- `text-balance` 和 `text-pretty` 的使用是好的排版实践。
- `tabular-nums` 在计数上的使用正确。
- `AlertDialogAction` 的 destructive 样式用 `bg-destructive hover:bg-destructive/90`——但 DESIGN.md 的 destructive button 是 `bg-destructive/10 text-destructive`（降饱和处理）。这里用了纯红底白字，与设计系统不一致。

## Questions to Consider

- 如果每张 Agent 卡片显示 "最近活跃会话" 和 "当前模型"，面板会从 CRUD 后台变成 Agent 编排控制台吗？
- hover-only 的 edit/delete 操作是否应该改为 always-visible 的 icon button 行？IM 联系人列表通常不隐藏操作。
- "联系人" 这个词是否准确？在 AChat 的语境中，Agent 更像 "协作者" 而非 "联系人"。但 IM 隐喻说 Agent 是联系人——这个张力值得思考。
- 面板缺少 Agent 的"在线/离线"状态指示。在 IM 范式中，联系人有在线状态——Agent 是否应该有类似的 "可用/不可用" 状态？
