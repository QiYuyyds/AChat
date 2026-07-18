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

/**
 * 累计「新内容(净)」——provider-aware:
 * - DeepSeek (cacheCreation == 0): inputTokens - cacheReadTokens（input 已含 cache hit）
 * - Anthropic (cacheCreation > 0): inputTokens + cacheCreationTokens（input 不含 cache creation）
 * 结果恒为「真正按 1× 计费的量」。
 */
export function computeNetInput(
  inputTokens: number,
  cacheReadTokens: number,
  cacheCreationTokens: number,
): number {
  if (cacheCreationTokens > 0) {
    return inputTokens + cacheCreationTokens
  }
  return Math.max(0, inputTokens - cacheReadTokens)
}

/**
 * 单次 ctx 拆解的「新内容」——与 computeNetInput 同源但用 last-turn 快照值。
 * Anthropic 无 lastCacheCreationTokens，用累计 cacheCreation>0 做 provider 信号，netNew ≈ lastInputTokens。
 */
export function computeLastNetInput(
  lastInputTokens: number,
  lastCacheReadTokens: number,
  cacheCreationTokens: number,
): number {
  if (cacheCreationTokens > 0) {
    return lastInputTokens
  }
  return Math.max(0, lastInputTokens - lastCacheReadTokens)
}

/** 估算费用结果。所有金额按原币种，不换算。 */
export interface CostEstimate {
  actualCost: number
  noCacheCost: number
  savings: number
  savingsPct: number
  currency: 'CNY' | 'USD'
}

/**
 * 计算费用估算——provider-aware，复用 computeNetInput 的 netNew 语义。
 * - actualCost = cacheRead × hitPrice + netNew × missPrice + output × outPrice
 * - noCacheCost = (cacheRead + netNew) × missPrice + output × outPrice
 * - 所有单价 per 1M tokens，结果为单位货币。
 */
export function computeCost(
  pricing: { currency: 'CNY' | 'USD'; inputCacheHit: number; inputCacheMiss: number; output: number },
  inputTokens: number,
  cacheReadTokens: number,
  cacheCreationTokens: number,
  outputTokens: number,
): CostEstimate {
  const netNew = computeNetInput(inputTokens, cacheReadTokens, cacheCreationTokens)
  const divisor = 1_000_000
  const actualCost =
    (cacheReadTokens * pricing.inputCacheHit +
      netNew * pricing.inputCacheMiss +
      outputTokens * pricing.output) /
    divisor
  const noCacheCost =
    ((cacheReadTokens + netNew) * pricing.inputCacheMiss +
      outputTokens * pricing.output) /
    divisor
  const savings = noCacheCost - actualCost
  const savingsPct = noCacheCost > 0 ? (savings / noCacheCost) * 100 : 0
  return { actualCost, noCacheCost, savings, savingsPct, currency: pricing.currency }
}
