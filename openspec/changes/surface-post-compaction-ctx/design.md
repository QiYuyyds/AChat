# Design

## Context

`当前 ctx` 语义 = 「上次真实 prompt 大小」(retrospective)。压缩改变的是「下次 prompt 大小」(prospective)。用户期望压缩后立刻看到数字变小。方案 A：压缩后把后端已算好的 `ctx_after` 作为一个**乐观覆盖值**塞进前端，直到下一次真实 run 用实测值接管。

被否决的替代方案：
- **B（改成前瞻值）**：要在前端复刻后端 prompt 组装逻辑，易随后端漂移，改动大。
- **C（专用 SSE 事件）**：碰 StreamEvent 契约（CLAUDE.md §3.3），为一个纯 UI 反馈不值得。

## Data flow

```
compact_conversation()
  ├─ 已算出 ctx_before / ctx_after
  ├─ CompactResult(summary, message, ctx_before, ctx_after)   ← 结构化返回
  └─ 系统消息文字保持不变（人类可读）

/conversations/{id}/compact  →  { summary, message, ctxBefore, ctxAfter }

UsageBadge.handleCompact:
  upsertMessage(result.message)
  setCtxOverride(convId, result.ctxAfter, result.message.createdAt)

app-store:
  ctxOverrideByConv[convId] = { tokens, at }

useConversationUsageTotal(convId):
  ...派生 lastInputTokens (记录来源时间戳 latestTs)...
  const ov = ctxOverrideByConv[convId]
  if (ov && ov.at > latestTs) result.lastInputTokens = ov.tokens
```

## Key decisions

- **覆盖时间戳用系统消息 `createdAt`（后端 `now_ms()`）**，与 run.startedAt / message.createdAt 同为毫秒、同一时钟域，比较无歧义。前端 `Date.now()` 也可但存在跨机/时钟漂移风险，取消息时间更稳。
- **不显式清除覆盖值**：靠 `ov.at > latestTs` 的时间比较自然失效。真实 run 发生 → 其时间戳更大 → 覆盖被忽略。避免引入清除时机的复杂度。
- **`useConversationUsageTotal` 需要暴露 latestTs**：现有代码里 runs 分支用 `latestRunWithUsage`、messages 兜底分支用 `latestMsgCreatedAt`，两者已在函数作用域内。取两者中实际用于 `lastInputTokens` 的那个作为比较基准（无数据时为 -1，任何 override.at 都更新，符合预期）。
- **`ctxOverrideByConv` 是 ephemeral UI state**：不持久化。刷新页面后覆盖值丢失，「当前 ctx」回落到 messages 兜底派生值——可接受，因为刷新后通常也已发生新对话或用户重新点压缩。

## Edge cases

- 覆盖值写入后、下一次 run 前，用户再点一次压缩（第二次通常 400「没有足够历史」）：不写覆盖值，旧覆盖值仍有效。
- `ctxAfter` 为 0 或异常：后端保证 `ctxAfter = estimate_tokens(summary) + kept messages`，恒 > 0，前端不特判。
- 多会话并行：`ctxOverrideByConv` 按 convId 隔离，互不影响。
