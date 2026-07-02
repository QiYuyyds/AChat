# Design notes

## 良性跳过 vs 真失败：怎么分

`compact_conversation` 现在的失败点分两类：

| 触发条件（现状 raise ValueError → 400） | 归类 | 改后行为 |
|---|---|---|
| 消息数 ≤ `KEEP_RECENT_MESSAGES`（`:137`） | 良性 | 200 + skipped + 友好消息 |
| 待压条数 < `MIN_COMPACTABLE`（`:141`） | 良性 | 同上 |
| 待压 token < `MIN_COMPACT_TOKENS`（`:152`） | 良性 | 同上 |
| 无配置模型的 agent（`:286/:304`） | 良性* | 200 + skipped + 友好消息（文案不同：说明当前会话没有可用于生成摘要的模型 agent） |
| 会话不存在（`:111`） | 真失败 | 400（保留） |
| 摘要生成失败：模型返回为空（`:170`） | 真失败 | 500/400（保留） |

\* 「无模型 agent」是个判断点：它不是崩溃，而是「这里做不了压缩」，误触时同样该给友好提示而非红错。归入良性，但文案要区分——用户需要知道原因是「没有可当摘要器的模型 agent」，而不是「对话太短」。

### 实现方式

引入一个专用信号，把「良性跳过」与真异常在类型上分开，避免 API 层靠解析错误字符串来猜：

```
class CompactionSkipped(Exception):
    def __init__(self, reason: str, message: MessageRecord): ...
```

- service 的良性分支：构造友好系统消息（见下）→ 广播 → `raise CompactionSkipped(reason, message_record)`。
- API：`except CompactionSkipped as s: return 200 {skipped:true, reason:s.reason, message:s.message}`；`except ValueError → 400`；`except Exception → 500`。

（也可以用返回值 union 代替异常；用异常是为了最小改动 `compact_conversation` 现有的 early-return/raise 控制流。）

## 只广播不落库的系统消息

**决策**：良性跳过的提示消息经 `event_bus.publish(MessageAddedEvent(...))` 广播，并随 API 响应的 `message` 字段返回，**但不 `db.add(Message(...))`**。

- 消息仍需一个 `id`（`new_message_id()`）、`role="system"`、`status="complete"`，供前端 `upsertMessage` 上屏与去重。
- 刷新页面后消失——这是**预期行为**：误触提示是即时性的，不进历史。
- 与现有系统消息（「没有协调者」`conversation_service.py:798`、压缩成功 `:210-257`）的区别就在这一点：那些落库，这条不落库。spec 需明确这条的临时语义，避免后人误以为漏写了 `db.add`。

### 风险 / 边界

- 其他已连接的客户端也会收到广播并短暂上屏这条消息，同样刷新即失——可接受。
- 前端 `upsertMessage` 必须能接纳一条 DB 里不存在的消息且不触发「拉取校对后删除」。现有乐观更新已按 id 幂等，临时消息只是永不被后续 fetch 命中——不会报错，只会在下次全量刷新时自然消失。

## 前端 skipped 分支

- `CompactConversationResult`：`summary` / `ctxBefore` / `ctxAfter` 变为可选；新增 `skipped?: boolean`、`reason?: string`。
- `usage-badge.tsx handleCompact` 与 `message-input.tsx executeCompactCommand`：
  - 成功（`!skipped`）：`upsertMessage(message)` + `setCtxOverride(...)`（badge 路径）——维持现状。
  - 跳过（`skipped`）：`upsertMessage(message)`，**跳过** `setCtxOverride`。
  - catch：只剩真失败会进来，保留 `console.error`（本次不扩展真失败的 UI，属独立议题）。
