# Agent 长任务探索问题分析

> 场景：用户绑定本地项目文件夹，要求 agent「详细分析一下这个项目代码」。Agent 在有限的 8 轮循环后停止；用户说「继续」后，agent 开始读取文件，随后出现幻觉——访问不存在的文件路径（如 `client/src/App.tsx`）。

## 1. 问题现象

从前端日志可观察到以下时序：

```
用户："详细分析一下这个项目代码"
  │
  ├─ run1 turn 1: fs_list          (15.4k tok)
  ├─ run1 turn 2: fs_list×3 + fs_read×4   (48.0k tok)
  ├─ run1 turn 3: fs_list×4              (86.5k tok)
  ├─ run1 turn 4: bash + fs_read + fs_list (93.2k tok)
  ├─ run1 turn 5: fs_read×7              (97.0k tok)
  ├─ run1 turn 6: fs_read×6             (102.5k tok)
  ├─ run1 turn 7: fs_read×10            (107.3k tok)
  └─ run1 turn 8: → 停止（达到 MAX_TURNS）

用户："继续"（发新消息，启动新 run）
  │
  ├─ run2 turn 1: fs_list          (16.0k tok, 从 15.6k 重新计数)
  ├─ run2 turn 2: fs_list×4 + fs_read×4
  ├─ ...
  └─ run2 后续: fs_read 失败 ×6
       "Not a file: client/src/App.tsx"
       "Not a file: client/src/App.css"
       "Not a file: client/src/index.css"
```

**关键观察**：
1. 8 轮后停止，与 context window 大小无关（硬编码上限）
2. 「继续」后 token 计数从 ~15k 重新开始 → 新 run，非续接
3. 幻觉集中出现在「继续」之后 → 上下文丢失导致

## 2. 根因分析（分层）

### A. 循环控制层 —— 最直接的硬伤

#### A1. 硬编码的 8 轮上限（与 token 无关）

```python
# backend/app/services/agent_runner.py:556
REACT_LOOP_MAX_TURNS = 8

# backend/app/adapters/custom_adapter.py:62
MAX_TURNS = 8
```

这是「8 轮后停止」的**直接原因**，且**和 context window 大小完全无关**。把 context window 改成 100M 也没用——`for turn in range(start_turn, 8)` 到 8 就 break。

「详细分析项目代码」这类任务本质是**广度优先的图遍历**：先看根目录 → 看各子目录 → 读关键文件 → 读依赖关系。一个中等规模项目（50-100 个源文件）至少需要 20-40 轮 tool 调用。8 轮只够 agent 做一次浅层扫描。

#### A2.「继续」是退化方案，不是续接

从前端日志看，「继续」后 token 从 15.6k 重新开始——这说明是**发送了一条新用户消息，启动了新 run**，而非从 checkpoint 恢复。

系统里存在 checkpoint 机制（`backend/app/services/checkpoint_service.py` + `POST /api/runs/{id}/resume`），但：
- 前端未接入 resume 调用（`src/` 目录下搜不到 `resume` 相关代码）
- 「继续」走普通发消息路径 → 新 run → 从 turn 0 重新计数 → 又是 8 轮上限

```
理想情况:                    实际情况:
run1: turn 0-7 (8轮)        run1: turn 0-7 (8轮) → 停
       ↓ checkpoint 恢复            ↓ 发新消息 "继续"
run1: turn 8-15 (8轮)       run2: turn 0-7 (8轮) → 停
       ↓ checkpoint 恢复            ↓ 但历史已被裁剪 → 幻觉
run1: turn 16-23 (8轮)      run2: 历史丢失 fs_list 结果
```

#### A3. 没有任务完成度感知

loop 的停止条件只有两个：`turn >= 8` 或 `token > 95%`。不看 agent 是否还在 productive 地探索。agent 可能在第 3 轮就卡住（反复读同一个文件），也可能在第 7 轮还有大量有价值的文件没读——一刀切都是 8 轮。

### B. 上下文管理层 —— 幻觉的真正根源

**核心结论**：幻觉不是因为模型能力差，而是因为上下文被裁剪后丢失了精确的文件结构信息。

#### B1. 三层独立压缩，互相不协调

```
┌─────────────────────────────────────────────────────────┐
│              压缩发生的三层                               │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Layer 1: _mid_run_compact (run 内, token > 90%)       │
│  ├── recent_keep = 6 (只保留最近 6 条完整)              │
│  ├── > 2000 tok 的旧 tool 结果 → "[已裁剪]"            │
│  └── > 20 条消息 → fold 到 15 条                       │
│  位置: backend/app/services/agent_runner.py:811         │
│                                                         │
│  Layer 2: _maybe_auto_compact_hook (run 后)             │
│  ├── watermark >= 30 条 → LLM 压缩                     │
│  └── token > 87% → LLM 压缩                            │
│      (LLM 摘要丢精度：无法保留精确文件路径)              │
│  位置: backend/app/services/agent_runner.py:310         │
│                                                         │
│  Layer 3: build_history_for (跨 run, 新 run 启动时)     │
│  ├── prune_old_tool_results (recent_turns=3)           │
│  │   → 超过 3 轮的 tool 结果如果 > 2000 tok 就裁剪     │
│  └── fold_old_messages (threshold=30, keep_recent=20)  │
│      → 超过 30 条就折叠                                │
│  位置: backend/app/services/conversation_context.py:74  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**关键问题**：`fs_list` 返回一个目录的完整文件列表，一个有 30-50 个文件的项目根目录，结果很容易超过 2000 token。一旦被裁剪：

```
裁剪前 (fs_list 结果):
{"entries": [
  {"name": "backend", "isDirectory": true},
  {"name": "frontend", "isDirectory": true},
  {"name": "package.json", "isDirectory": false, "size": 2048},
  {"name": "tsconfig.json", ...},
  ... (50 个 entry)
]}

裁剪后:
"[tool_result 已裁剪（mid-run compact）]"
```

agent 只知道「之前 list 过根目录」，但**不知道具体有哪些文件和目录**。下一步只能靠猜。

#### B2.「继续」后幻觉的精确机制

```
run1 (8轮):
  turn 1: fs_list("") → 看到 backend/, frontend/, package.json
  turn 2-7: 读了一些文件
  turn 8: 到 MAX_TURNS 停止

用户: "继续"

run2 (新 run):
  build_history_for() 构建 history:
    ├── 最近的 3 轮 tool 结果保留完整
    ├── 第 4-8 轮的 tool 结果如果 > 2000 tok → "[tool_result 已裁剪]"
    │   ↑ turn 1 的 fs_list("") 结果大概率在这里被裁剪
    └── agent 上下文里没有了 "backend/, frontend/" 这个事实

  turn 1: agent 想「继续读核心文件」
    → 但不知道实际文件结构
    → 基于训练数据里最常见的 React 项目结构猜:
       client/src/App.tsx  ← 幻觉！
       client/src/App.css  ← 幻觉！
```

这就是为什么「继续」后开始访问不存在的文件——**不是模型变笨了，是上下文里精确信息被裁剪掉了**。

#### B3. LLM 压缩对工具结果是有损的

`compact_conversation`（`backend/app/services/context_compaction_service.py:226`）调 LLM 生成摘要，prompt 要求保留「用户核心目标、关键决策、产物、待跟进事项」。但**文件路径、目录结构这种精确信息不在摘要重点里**，LLM 会把它们抽象成「探索了项目结构」这种模糊描述。

### C. 工具设计层 —— 加速 token 爆炸的结构性问题

#### C1. `fs_list` / `fs_glob` 不在 read-only cache 里

```python
# backend/app/services/agent_runner.py:559
READONLY_CACHEABLE_TOOLS = frozenset({"fs_read", "read_artifact", "read_attachment"})
```

`fs_list` 和 `fs_glob` 不在缓存列表。同一个 run 内，如果 agent 多次 `fs_list("")` 查看根目录（比如压缩后忘了之前 list 过），每次都重新执行并返回完整结果。而 `fs_read` 同一个文件只读一次（缓存命中返回 `[cached] ...`）。

这个设计逻辑有偏差：**目录列表比文件内容更该缓存**，因为目录结构在单次 run 内不会变。

#### C2. 工具结果直接塞进 messages，没有「存储 vs 传给 LLM」的分离

```
当前架构:
  fs_list → 完整 JSON result → messages.append({"role":"tool", "content": json.dumps(result)})
                                  → 直接成为 LLM 上下文的一部分
                                  → 压缩时只能粗暴裁剪

理想架构:
  fs_list → 完整 result 存到 "探索状态" (side storage)
         → 传给 LLM 的是摘要或引用 ("见探索状态 #dir_root")
         → 压缩时只需保留引用，不影响 LLM 上下文
```

当前没有「探索状态」这个概念，工具结果和 LLM 上下文是同一个东西。这是 token 爆炸的结构性原因。

#### C3. `fs_read` 截断但无智能摘要

```python
# backend/app/services/fs_service.py:19
MAX_READ_CHARS = 50_000
```

读一个 2000 行的文件，直接截断到 50k 字符。agent 要么读全文（~12k token），要么不读。没有「只读函数签名」「只读 import 部分」「只读类定义」的选项。`offset`/`limit` 参数已加入（见 `fs_read.py`），但 agent 需要主动选择分段，不是自动的。

#### C4. 没有「项目结构概览」工具

agent 想了解项目结构，只能：
1. `fs_list("")` → 看根目录（一层）
2. `fs_list("src")` → 看 src 目录（又一层）
3. `fs_list("src/components")` → 再一层...
4. 每层都消耗 token

`fs_glob` 可以一次性获取文件清单，但返回的是**扁平列表**，没有树形结构。`code_explore`（`backend/app/tools/code_explore.py`）能回答结构性问题，但依赖代码图谱已建好（`metadata.status == "ready"`），如果没建好就 fallback 到 fs 工具。

### D. Agent 策略层 —— 模型行为没有被正确引导

#### D1. agent 试图「一次性读完所有文件」

从前端日志的措辞看：
- "Now let me read all the core source files to fully understand the codebase"
- "Now let me read all the actual source code files"

这说明 agent 的策略是**贪心读取**，而不是**有策略的渐进探索**。单轮内调用 7-10 个 `fs_read`，token 瞬间从 15k 飙到 110k。

已改进的 tool guidance（`_build_agent_hub_tool_guidance`，`backend/app/services/agent_runner.py`）部分缓解了这个问题（引导用 `fs_glob`/`fs_grep` 优先定位），但：
- system prompt 层面没有「分步探索」策略引导
- 上下文被压缩后，tool guidance 可能在 fold 时丢失（如果它不是最近 20 条）

#### D2. 没有「已探索/待探索」状态追踪

```
agent 心智模型 (理想):           agent 实际行为:
  已探索: [根目录, src/, ...]      每轮独立决策
  待探索: [backend/, tests/, ...]  不知道之前探索过什么
  → 有策略地推进                   → 可能重复探索同一目录
                                  → 或跳过关键目录
```

agent 没有一个「探索清单」来追踪进度。每次压缩后，agent 对「自己已经探索了什么」的认知也会丢失。

### E. 架构层 —— 缺失的「长任务」支持

#### E1. 两条 ReAct loop 路径的压缩逻辑不一致

```
_run_react_loop (use_react_loop=True, 默认):
  ├── 有 _mid_run_compact (90% token 触发)
  ├── 有 token budget control (95% 停止)
  └── 有 hook 支持 (pre_turn, post_turn, on_stop)
  位置: backend/app/services/agent_runner.py:845

custom_adapter.stream (use_react_loop=False):
  ├── 没有任何压缩 ← 8 轮内 token 无限累积
  ├── 没有 token budget control
  └── 没有 hook 支持
  位置: backend/app/adapters/custom_adapter.py:719
```

如果有人把 `use_react_loop` 关掉，问题会更严重——8 轮内 token 直接爆，API 报错。

#### E2. token 估算不精确

```python
# backend/app/utils/model_registry.py:93
def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)  # 4 chars ≈ 1 token, 10-20% 误差

# _run_react_loop 里:
total_tokens = estimate_tokens(json.dumps(messages, ensure_ascii=False))
```

`json.dumps` 包含 `{"role": "tool", "tool_call_id": "xxx", "content": "..."}` 这种大量 JSON 元数据。用 `len/4` 估算会**高估** token 数（因为 JSON 的 `{`、`"`、`:` 等结构字符在实际 tokenizer 里可能不是 1:4）。

高估的后果：过早触发 `_mid_run_compact`，过早裁剪 tool 结果。

#### E3. `model_limit` 获取失败时的静默退化

```python
# agent_runner.py:914-918
if model_id:
    try:
        model_limit = get_model_limits(model_provider, model_id).context_window
    except Exception:
        model_limit = 0

# 后续:
if model_limit > 0:  # ← 如果 model_limit=0, 整个 token budget control 被跳过
    ...
```

如果 agent 没配置 `model_id`（空字符串），`if model_id:` 为 False，`model_limit` 保持 0，**token budget control 完全跳过**。

## 3. 问题优先级矩阵

| 根因 | 影响程度 | 是否已触及 | 代码位置 |
|---|---|---|---|
| A1. 硬编码 8 轮上限 | ★★★★★ | ✗ 未触及（最关键） | `agent_runner.py:556`, `custom_adapter.py:62` |
| B1. 三层压缩裁剪 tool 结果 | ★★★★★ | △ 改了压缩策略但未根治 | `agent_runner.py:811`, `conversation_context.py:74` |
| B2.「继续」后历史丢失 | ★★★★★ | ✗ 未触及 | `conversation_context.py:152` |
| C1. `fs_list` 不缓存 | ★★★★☆ | ✗ 未触及 | `agent_runner.py:559` |
| C2. 无探索状态分离 | ★★★★☆ | ✗ 未触及 | 架构层面缺失 |
| D1. 无分步探索引导 | ★★★★☆ | △ 改了 tool guidance | `agent_runner.py` `_build_agent_hub_tool_guidance` |
| A2. checkpoint 未接入前端 | ★★★☆☆ | ✗ 未触及 | `checkpoint_service.py`, 前端缺失 |
| C3. `fs_read` 无智能摘要 | ★★★☆☆ | △ 加了 offset/limit | `fs_service.py:59` |
| B3. LLM 压缩丢精度 | ★★★☆☆ | ✗ 未触及 | `context_compaction_service.py:226` |
| E2. token 估算不精确 | ★★☆☆☆ | ✗ 未触及 | `model_registry.py:93` |
| D2. 无探索状态追踪 | ★★☆☆☆ | ✗ 未触及 | 架构层面缺失 |
| E1. 两条路径不一致 | ★★☆☆☆ | ✗ 未触及 | `agent_runner.py:845` vs `custom_adapter.py:719` |

## 4. 改进方向建议

### 方向 1：动态 turn 上限（替代硬编码 8）

当前 8 轮是「镜像 CustomAdapter.MAX_TURNS」的保守值。可探索：
- 基于 `model_limit` 和当前 token 使用率动态计算剩余可负担的 turn 数
- 或设一个更高上限（如 30），让 token budget 来做实际约束

### 方向 2：「探索状态」作为一等公民

把工具结果从 messages 里分离出来，存到一个 per-run 的 exploration state：
- `fs_list` / `fs_glob` 结果存到探索状态
- 传给 LLM 的是引用或摘要
- 压缩时只压缩 messages，不碰探索状态
- agent 可以通过 `recall_exploration` 工具查探索状态

### 方向 3：`fs_list` / `fs_glob` 纳入 read-only cache

把 `fs_list` 和 `fs_glob` 加到 `READONLY_CACHEABLE_TOOLS`。同一参数的调用在单 run 内只执行一次，后续返回 `[cached]`。这能显著减少重复探索的 token 浪费。

### 方向 4：checkpoint 接入前端「继续」

让「继续」走 `POST /api/runs/{id}/resume` 而不是发新消息。这样：
- 从上次停止的 turn 继续计数
- messages 列表完整恢复（不经过 `build_history_for` 的裁剪）
- 不触发 auto_compact

### 方向 5：压缩时保留「结构性 tool 结果」

`_mid_run_compact` 和 `prune_old_tool_results` 裁剪时，对 `fs_list` / `fs_glob` 这类「结构性」工具结果做特殊处理：
- 不直接裁剪成 `[已裁剪]`
- 而是保留一个精简版（如只保留文件名列表，去掉 size 等元数据）
- 或者替换成引用「见探索状态 #dir_root」

## 5. 遗漏的能力：Plan 与 Subagent 为何未被调用

用户反馈：系统有 plan 功能和 subagent 功能，但在「分析项目代码」场景中完全没有看到 agent 调用它们。如果 agent 能用这些能力把任务拆分成子任务并行执行，本可以避免单 agent 贪心读取所有文件的问题。以下是根因分析。

### 5.1 三种调度能力的触发条件

```
┌──────────────────────────────────────────────────────────────────┐
│                    调度能力可用性矩阵                              │
├───────────┬──────────────┬──────────────┬──────────────┬─────────┤
│ 能力       │ solo (单聊)  │ solo (群聊)  │ coordinated  │ subagent│
│           │              │              │ (orchestrator)│         │
├───────────┼──────────────┼──────────────┼──────────────┼─────────┤
│ create_   │ ✅ 注入      │ ✅ 注入      │ ✅ 注入      │ ❌ 不注入│
│ plan      │ (depth<3)    │              │              │         │
├───────────┼──────────────┼──────────────┼──────────────┼─────────┤
│ task_     │ ✅ 注入      │ ✅ 注入      │ ✅ 注入      │ ✅ 注入  │
│ dispatch  │ (clone-only) │ (clone-only) │ (+group)     │(depth<3)│
├───────────┼──────────────┼──────────────┼──────────────┼─────────┤
│ dispatch_ │ ❌ 不注入    │ ❌ 不注入    │ ✅ 注入      │ ❌ 不注入│
│ plan(DAG) │              │              │              │         │
└───────────┴──────────────┴──────────────┴──────────────┴─────────┘
```

关键代码路径：

```python
# backend/app/services/agent_runner.py:1492-1514
# execute_run 里决定 mode:
if args.override_prompt:                        # subagent
    mode = "subagent"
else:
    dispatch_mode = get_dispatch_mode(conv)      # 读取 conversation.dispatch_mode
    if dispatch_mode == "coordinated" and is_orchestrator:
        mode = "coordinated"                      # 需要 双重条件
    else:
        mode = "solo"                             # 默认 fallback
```

### 5.2 Plan 工具：注入了但 agent 没用

#### 5.2.1 工具确实被注入

```python
# backend/app/services/agent_loop.py:371-376 (_run_solo_loop)
plan_enabled = dispatch_enabled  # depth < MAX_DISPATCH_DEPTH(3) 时为 True
if plan_enabled:
    for plan_tool in ("create_plan", "plan_step", "add_plan_steps"):
        if plan_tool not in tool_names:
            tool_names.append(plan_tool)
```

solo 模式下（单聊默认就是 solo），`create_plan` / `plan_step` / `add_plan_steps` 三个工具**确实会被注入**到 agent 的工具列表里。agent 有能力调用它们。

#### 5.2.2 但 system prompt 的引导偏向「修改文件」场景

```python
# backend/app/services/agent_loop.py:229-262 (_PLAN_SUFFIX)
### 要使用 create_plan 的场景
- 需要修改 3 个或以上文件          ← 只读任务不满足
- 需要先研究分析、再实现、再验证    ← "分析项目"只有研究，没有实现/验证
- 用户明确要求分步执行
- 任务涉及多个不同阶段（如：调研→设计→开发→测试）

### 不要使用 create_plan 的场景
- 改一个配置值、修一行代码
- 回答信息性问题                  ← "分析项目"可能被 agent 归类到这里
- 读一个文件、执行一条命令
- 1-2 步就能完成的简单操作
```

「详细分析一下这个项目代码」是**只读探索任务**，不修改文件。system prompt 的引导里：
- 「要使用」的场景全部围绕「修改文件」「实现」「开发」
- 「不要使用」的场景里有「回答信息性问题」

agent 很可能把「分析项目代码」归类为「信息性问题」，因此跳过了 `create_plan`。

#### 5.2.3 plan 的核心价值是透明度，不是执行优化

~~之前分析说「plan 工具不解决核心问题」——这个判断是错的。~~

`create_plan` 的核心价值不在于改变执行方式，而在于**用户透明度和 agent 自我组织**：

```
没有 plan 时（当前情况）:
  用户: "详细分析一下这个项目代码"
  agent: [黑盒运行 8 轮] → 突然停止
  用户: ???（不知道 agent 做了什么、为什么停、还剩什么没做）

有 plan 时（理想情况）:
  用户: "详细分析一下这个项目代码"
  agent: create_plan([
    {id:'s1', title:'探索项目根目录结构'},
    {id:'s2', title:'分析后端架构'},
    {id:'s3', title:'分析前端架构'},
    {id:'s4', title:'分析构建配置与依赖'},
    {id:'s5', title:'汇总分析结果'},
  ])
  → 用户看到完整计划，知道 agent 要做什么
  agent: plan_step(s1) → fs_list + fs_read → plan_step(s2) → ...
  → 用户实时看到进度，知道 agent 在哪一步
```

行业对比（调研 Codex / Cursor / Claude Code）：

| 工具 | 探索任务是否用 plan | 机制 |
|---|---|---|
| **Claude Code** | ✅ 是 | TodoWrite（现已升级为 Tasks），即使探索任务也创建 todo 列表 |
| **Claude Code Ultraplan** | ✅ 是 | 「动手写代码之前，先在网页上给你看一份完整的实施方案」 |
| **Cursor Plan Mode** | ✅ 是 | 明确用于「先研究代码库、提澄清问题、生成实现计划」 |
| **planning-with-files 插件** | ✅ 是 | 「处理复杂任务时，自动创建并维护 Markdown 文件来追踪进度」 |
| **本项目** | ❌ 否 | system prompt 引导偏向「修改文件」，探索任务不触发 plan |

Claude Code 源码分析文章明确指出：
> 「复杂任务为什么会失控...这不是工具不够用，而是任务本身需要规划」
> 简单任务：read_file → answer
> 复杂任务：需要**探索 → 理解 → 规划 → 执行 → 验证 → 回溯**

「分析项目代码」正是典型的复杂任务，需要规划。但当前 system prompt 的引导让 agent 认为这是「信息性问题」而跳过了 plan。

**结论**：plan 工具虽然不直接解决 token/轮次问题，但它解决了**更前置的问题**——用户不知道 agent 在干什么。这是 UX 层的核心缺陷，不是可选项。

### 5.3 task_dispatch：注入了但 agent 没选择用

#### 5.3.1 工具确实被注入

```python
# backend/app/services/agent_loop.py:366-369 (_run_solo_loop)
dispatch_enabled = args.dispatch_depth < MAX_DISPATCH_DEPTH  # depth < 3
if dispatch_enabled and "task_dispatch" not in tool_names:
    tool_names.append("task_dispatch")
```

solo 模式下 `task_dispatch` 也会被注入。agent 可以用它 clone-self 来并行处理子任务。

#### 5.3.2 agent 不用 subagent 可能是理性选择

```python
# backend/app/services/agent_loop.py:204-225 (_SOLO_DISPATCH_SUFFIX)
### 何时使用
- 任务可以拆分为独立的子任务并行处理
- 某个子任务需要大量独立的研究或文件操作    ← "分析项目"满足
- 你想在处理主任务的同时让子 Agent 处理辅助工作

### 注意事项
- 子 Agent 看不到当前对话上下文，任务描述必须自包含
- 子 Agent 共享你的工作空间，注意文件写入冲突
- 递归深度有限（最多 3 层），深层子 Agent 无法继续派发
```

引导里确实提到了「某个子任务需要大量独立的研究或文件操作」，这和「分析项目代码」匹配。但 **agent 选择不用 subagent 并不一定是 bug**——这可能是理性判断：

```
主 agent 自己读 vs 派发 subagent 的权衡:

  自己读:
  ├── 优点: 上下文连续，读过的文件记得住
  ├── 优点: 没有 spawn 开销，更快
  ├── 优点: 省 token（不需要重复传任务描述）
  └── 缺点: 受 8 轮上限，读不完

  派发 subagent:
  ├── 优点: 并行探索不同目录
  ├── 优点: 子 agent 上下文隔离，不污染父 agent
  ├── 缺点: 子 agent 看不到父 agent 已探索的内容 → 可能重复探索
  ├── 缺点: spawn 开销 + 任务描述 token 开销
  ├── 缺点: 子 agent 结果回传后面临压缩
  └── 缺点: 子 agent 也受 8 轮上限
```

对于「分析项目代码」这种任务，如果项目不大（< 30 个文件），主 agent 自己读确实可能更快更省。agent 做出这个判断是合理的。

**真正的问题不是「agent 应该用 subagent」**，而是：
1. agent 连 plan 都没创建，用户完全不知道它在干什么
2. 如果项目很大（> 100 个文件），主 agent 8 轮读不完，这时 subagent 才有价值，但 agent 可能没意识到任务规模
3. subagent 本身也有 8 轮限制，即使派发了也未必能完成子任务

#### 5.3.3 clone-self 的结果回传也会面临压缩

```python
# backend/app/tools/task_dispatch.py:146-166
result = await spawn_subagent_loop(...)
return ok({
    "status": result.status,
    "summary": result.text,   # ← 子 agent 的完整输出塞进 tool_result
})
```

子 agent 的输出通过 `spawn_subagent_loop` 返回 `result.text`，然后作为 `task_dispatch` 的 tool result 塞进父 agent 的 messages。如果子 agent 返回了详细的项目分析（几千 token），这个结果同样会面临 `_mid_run_compact` 和 `prune_old_tool_results` 的裁剪。

#### 5.3.4 subagent 也受 8 轮上限约束

```python
# backend/app/services/agent_runner.py:556
REACT_LOOP_MAX_TURNS = 8  # 所有 _run_react_loop 共用
```

子 agent 走的也是 `_run_react_loop`，同样受 8 轮上限。如果「分析 backend/ 目录」需要 10 轮 `fs_list` + `fs_read`，子 agent 也会中途停止。

### 5.4 dispatch_plan（DAG 派发）：单聊下完全不可用

```python
# backend/app/services/agent_loop.py:475-484 (_run_coordinated_loop)
# 只有 coordinated 模式才注入 dispatch_plan
if "dispatch_plan" not in tool_names:
    tool_names.append("dispatch_plan")

# backend/app/services/agent_runner.py:1503-1504
dispatch_mode = get_dispatch_mode(conv)      # 单聊默认 'solo'
if dispatch_mode == "coordinated" and is_orchestrator:  # 双重条件
    mode = "coordinated"
```

`dispatch_plan`（声明式 DAG 派发）**只在 coordinated 模式下注入**，而进入 coordinated 需要：
1. `conversation.dispatch_mode == "orchestrated"` —— 单聊默认是 `"solo"`
2. `agent.is_orchestrator == True` —— 默认是 `False`

```python
# backend/app/services/conversation_service.py:388
dispatch_mode=dispatch_mode or ("orchestrated" if mode == "group" else "solo")
#                                                    ↑ 只有群聊才默认 orchestrated
```

用户场景是**单聊**（一个 agent 分析一个项目），`dispatch_mode = "solo"`，永远不会进入 coordinated 模式，`dispatch_plan` 工具根本不会出现在 agent 的工具列表里。

### 5.5 综合归因（修正后）

```
┌──────────────────────────────────────────────────────────────────┐
│              Plan / Subagent 未被调用的根因（修正后）              │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  create_plan (进度卡片 + 用户透明度)                              │
│  ├── 根因: system prompt 引导偏向「修改文件」场景                 │
│  ├── 根因: 「分析项目」被 agent 归类为「信息性问题」而跳过 plan   │
│  ├── 对比: Claude Code / Cursor / Codex 探索任务都会先建 plan    │
│  └── 结果: 用户完全不知道 agent 在干什么（黑盒运行 → 突然停止）  │
│  影响程度: ★★★★★ (UX 核心缺陷，不是可选项)                      │
│                                                                  │
│  dispatch_plan (DAG)                                             │
│  ├── 根因: 单聊 dispatch_mode='solo'，不进入 coordinated         │
│  ├── 根因: agent.is_orchestrator 默认 False                      │
│  └── 结果: 工具根本不注入，agent 看不到它                         │
│  影响程度: ★★★★☆ (DAG 并行派发是最优解，但完全不可用)            │
│                                                                  │
│  task_dispatch (clone-self)                                      │
│  ├── 判断: agent 不用 subagent 可能是理性选择                    │
│  │   （自己读更快更省 token，适合小项目）                        │
│  ├── 问题: 大项目时 agent 可能没意识到需要 subagent              │
│  ├── 问题: 子 agent 也受 8 轮上限，派发了也未必完成              │
│  └── 结果: 工具注入了，agent 做了权衡后选择不用（可能合理）      │
│  影响程度: ★★☆☆☆ (不是核心问题，是 agent 的判断)                │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**核心结论修正**：
- 之前把 create_plan 的价值低估了（说「不解决核心问题」）——实际上 plan 解决的是**用户透明度**这个更前置的问题
- 之前把 task_dispatch 的价值高估了——agent 不用 subagent 可能是理性判断，不是 bug
- **最关键的问题是 create_plan 没被用**，因为它直接导致用户体验灾难：黑盒运行 → 突然停止 → 用户不知道发生了什么

### 5.6 缺失的「探索型任务」引导

当前 system prompt 的调度引导全部围绕**生产型任务**（写代码、修 bug、搭系统），没有覆盖**探索型任务**（分析项目、理解代码库、文档生成）。

```
生产型任务 (当前引导覆盖):
  "搭建用户认证系统" → create_plan + task_dispatch
  "修复这个 bug" → 直接做或 create_plan

探索型任务 (当前引导缺失):
  "详细分析这个项目" → ???
  "理解这个代码库的架构" → ???
  "生成项目文档" → ???
```

行业工具如何处理探索型任务：

```
Cursor:
  1. 让 Agent 讲解代码库结构（第一步就是探索）
  2. Plan Mode: 先研究代码库 → 提澄清问题 → 生成实现计划
  3. @Codebase: 语义搜索整个代码库

Claude Code:
  1. TodoWrite: 即使是探索任务也创建 todo 列表
  2. Ultraplan: 动手前先给用户看完整方案
  3. planning-with-files 插件: 自动维护 task_plan.md + findings.md + progress.md

Codex:
  1. 先分析项目结构再动手
  2. 复杂任务自动拆解为步骤

Understand-Anything 插件 (Claude Code/Cursor/Codex 通用):
  1. 多 Agent 流水线扫描整个代码库
  2. 为每个文件/函数/类构建知识图谱
  3. 生成交互式仪表盘供探索
```

对比可见，行业工具在探索型任务上有一个共同模式：**先建计划/图谱，再深入细节**。而本项目的 agent 直接跳入了细节（逐个 fs_read），跳过了「先建全局认知」这一步。

`create_plan` 在探索型任务中的价值：
1. **用户透明度**：用户看到 agent 要探索哪些模块，知道进度
2. **agent 自我组织**：agent 先想清楚要探索什么，而不是随机读文件
3. **渐进式深入**：先探索根目录 → 再各子目录 → 再关键文件，有层次
4. **中断恢复**：8 轮停止后，用户说「继续」，agent 能从 plan 看到上次到哪了

### 5.7 改进方向（针对 Plan / Subagent）

#### 方向 A：修正 system prompt，让探索型任务也触发 create_plan（最高优先级）

这是最应该先做的改动。当前 `_PLAN_SUFFIX`（`backend/app/services/agent_loop.py:229-262`）的引导偏向「修改文件」场景，需要补充探索型任务的示例：

```
### 要使用 create_plan 的场景（补充探索型）
- 需要修改 3 个或以上文件
- 需要先研究分析、再实现、再验证
- 用户明确要求分步执行
- 任务涉及多个不同阶段（如：调研→设计→开发→测试）
+ 需要系统性探索/分析一个项目或代码库（如：分析项目架构、理解代码库结构）
+ 需要生成多模块的文档或报告

### 判断示例（补充）
+ 用户：「详细分析一下这个项目代码」→ ✅ create_plan
+   （探索根目录 → 分析后端 → 分析前端 → 分析配置 → 汇总）
+ 用户：「理解这个代码库的架构」→ ✅ create_plan
```

这不需要改代码逻辑，只需要改 system prompt 文本，是最低成本的改进，但能解决**用户透明度**这个 UX 核心问题。

#### 方向 B：单聊下也允许 dispatch_plan

当前 `dispatch_plan` 只在 coordinated 模式可用。但 clone-self 的 `task_dispatch` 已经在 solo 模式下可用了，`dispatch_plan` 只是 `task_dispatch` 的声明式 DAG 版本。可以考虑在 solo 模式下也注入 `dispatch_plan`，让 agent 能一次性声明「分析 backend/」「分析 frontend/」「分析配置文件」三个并行任务。

#### 方向 C：子 agent 结果的「探索状态」回传

当前子 agent 结果通过 `result.text` 作为 tool result 塞进父 agent 的 messages，面临压缩问题。如果引入「探索状态」分离（见第 4 节方向 2），子 agent 的探索结果可以存到探索状态里，父 agent 只收到一个引用，不会被压缩裁剪。

#### 方向 D：提升 subagent 的轮次上限

如果 subagent 用于探索型任务，8 轮可能不够。可以考虑给 subagent 设置独立的、更高的轮次上限（如 15-20），因为 subagent 的 context 是隔离的，不会污染父 agent 的上下文。

#### 方向 E：不要强制 agent 用 subagent

`task_dispatch` 应该保持为**可选工具**，不是强制路径。agent 根据任务规模自己判断是否需要并行——小项目自己读更快，大项目才派发 subagent。引导里应该明确：「不确定时先用 plan + 自己读，发现读不完再考虑 task_dispatch」。

## 6. 待验证项

以下问题需要进一步确认才能精确定位：

1. **实际使用的模型和 context window 配置**：确认 agent 的 `model_provider` / `model_id`，以及是否命中 `KNOWN_MODELS`
2. **「继续」时前端调用的 API**：确认是发新消息还是 resume
3. **`code_explore` 的代码图谱是否生效**：如果未生效，agent 无法使用结构性分析工具
4. **auto_compact 是否在 run1 期间触发**：查看后端日志确认压缩时机
5. **tool_call_cache 的命中率**：确认 `fs_read` 缓存是否按预期工作
6. **agent 的 `is_orchestrator` 和会话 `dispatch_mode` 实际值**：确认是否为 solo 模式
7. **后端日志里是否有 task_dispatch / create_plan 的工具调用记录**：确认 agent 是否看到了这些工具但选择不用

---

> 本文档为 explore 模式下的分析产物，记录问题根因与改进方向，未包含实现代码。后续如需推进，建议针对各方向分别创建 OpenSpec change proposal。
