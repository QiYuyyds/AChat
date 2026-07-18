/**
 * Token 用量计算 helpers —— provider-aware。
 *
 * 核心差异（与 `usage-badge.tsx` 的 `computeCacheHitRate` 同源）：
 * - Anthropic：`input_tokens` **不含** cache_read / cache_creation → 总量需加上两者
 * - DeepSeek / OpenAI 等：`prompt_tokens` **已含** cached_tokens → 总量不能再加 cacheRead
 *
 * 信号：`cacheCreationTokens > 0` 表示 Anthropic 风格上报。
 * 当 `cacheCreationTokens` 不可用时（MessageUsage 不含此字段），用 `modelProvider === 'anthropic'` 兜底。
 */

import type { ModelProvider } from './types'

/**
 * 计算真实的 token 总量（input + output，按 provider 语义修正 cache 双重计算）。
 *
 * 用于 RunUsage（含 cacheCreationTokens 字段）。
 */
export function computeTotalTokens(
  inputTokens: number,
  outputTokens: number,
  cacheCreationTokens: number,
  cacheReadTokens: number,
): number {
  if (cacheCreationTokens > 0) {
    return inputTokens + outputTokens + cacheCreationTokens + cacheReadTokens
  }
  return inputTokens + outputTokens
}

/**
 * 计算单条消息的 token 总量。
 *
 * MessageUsage 不含 cacheCreationTokens，用 modelProvider 做兜底信号：
 * anthropic → input 排除 cache，需加回 cacheRead
 * 其他 provider（deepseek / openai / ...）→ input 已含 cacheRead，不重复加
 */
export function computeMessageTotalTokens(
  inputTokens: number,
  outputTokens: number,
  cacheReadTokens: number,
  modelProvider?: ModelProvider | null,
): number {
  if (modelProvider === 'anthropic') {
    return inputTokens + outputTokens + cacheReadTokens
  }
  return inputTokens + outputTokens
}
