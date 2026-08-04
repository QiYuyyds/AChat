/**
 * Token 用量计算 helpers —— cacheStyle-aware。
 *
 * 核心差异：
 * - 'deepseek'：input_tokens 已含 cache_read → total = input + output，netInput = input - cacheRead
 * - 'anthropic'：input_tokens 不含 cache → total = input + output + cacheCreation + cacheRead，netInput = input + cacheCreation
 * - 'none'：不支持缓存 → total = input + output，netInput = input
 *
 * 所有函数第一个参数是 cacheStyle，消除从数据反推 provider 的反模式。
 */

import type { CacheStyle } from './types'

/**
 * 旧数据兼容：从 cacheCreationTokens 反推 cacheStyle。
 * > 0 → 'anthropic'，否则 'deepseek'。与 pre-change 行为完全一致。
 */
export function inferCacheStyle(cacheCreationTokens: number): CacheStyle {
  return cacheCreationTokens > 0 ? 'anthropic' : 'deepseek'
}

/**
 * 计算真实的 token 总量（input + output，按 cacheStyle 语义修正 cache 双重计算）。
 */
export function computeTotalTokens(
  cacheStyle: CacheStyle,
  inputTokens: number,
  outputTokens: number,
  cacheCreationTokens: number,
  cacheReadTokens: number,
): number {
  switch (cacheStyle) {
    case 'anthropic':
      return inputTokens + outputTokens + cacheCreationTokens + cacheReadTokens
    case 'deepseek':
      return inputTokens + outputTokens
    case 'none':
      return inputTokens + outputTokens
  }
}

/**
 * 计算单条消息的 token 总量。
 * MessageUsage 不含 cacheCreationTokens，用 cacheStyle 判断。
 */
export function computeMessageTotalTokens(
  cacheStyle: CacheStyle,
  inputTokens: number,
  outputTokens: number,
  cacheReadTokens: number,
): number {
  switch (cacheStyle) {
    case 'anthropic':
      return inputTokens + outputTokens + cacheReadTokens
    case 'deepseek':
      return inputTokens + outputTokens
    case 'none':
      return inputTokens + outputTokens
  }
}

/**
 * 累计「新内容(净)」——cacheStyle-aware:
 * - deepseek: inputTokens - cacheReadTokens（input 已含 cache hit）
 * - anthropic: inputTokens + cacheCreationTokens（input 不含 cache creation）
 * - none: inputTokens（不支持缓存）
 */
export function computeNetInput(
  cacheStyle: CacheStyle,
  inputTokens: number,
  cacheReadTokens: number,
  cacheCreationTokens: number,
): number {
  switch (cacheStyle) {
    case 'anthropic':
      return inputTokens + cacheCreationTokens
    case 'deepseek':
      return Math.max(0, inputTokens - cacheReadTokens)
    case 'none':
      return inputTokens
  }
}

/**
 * 单次 ctx 拆解的「新内容」——与 computeNetInput 同源但用 last-turn 快照值。
 */
export function computeLastNetInput(
  cacheStyle: CacheStyle,
  lastInputTokens: number,
  lastCacheReadTokens: number,
): number {
  switch (cacheStyle) {
    case 'anthropic':
      return lastInputTokens
    case 'deepseek':
      return Math.max(0, lastInputTokens - lastCacheReadTokens)
    case 'none':
      return lastInputTokens
  }
}

/**
 * 计算 cache 命中率——cacheStyle-aware。
 * - deepseek: cacheRead / inputTokens（input 已含 cache）
 * - anthropic: cacheRead / (input + cacheRead + cacheCreation)
 * - none: 0（不支持缓存）
 */
export function computeCacheHitRate(
  cacheStyle: CacheStyle,
  inputTokens: number,
  cacheCreationTokens: number,
  cacheReadTokens: number,
): number {
  switch (cacheStyle) {
    case 'anthropic': {
      const denom = inputTokens + cacheReadTokens + cacheCreationTokens
      return denom > 0 ? (cacheReadTokens / denom) * 100 : 0
    }
    case 'deepseek':
      return inputTokens > 0 ? (cacheReadTokens / inputTokens) * 100 : 0
    case 'none':
      return 0
  }
}

/** 分桶数据 */
export interface CacheStyleBucket {
  inputTokens: number
  cacheReadTokens: number
  cacheCreationTokens: number
  outputTokens: number
}

/**
 * 计算加权 cache 命中率——逐 style 用各自正确分母公式算命中率后加权平均。
 */
export function computeWeightedCacheHitRate(
  byCacheStyle: Record<CacheStyle, CacheStyleBucket>,
): number {
  let weightedSum = 0
  let totalInput = 0
  for (const style of ['deepseek', 'anthropic', 'none'] as CacheStyle[]) {
    const bucket = byCacheStyle[style]
    if (!bucket) continue
    const rate = computeCacheHitRate(
      style,
      bucket.inputTokens,
      bucket.cacheCreationTokens,
      bucket.cacheReadTokens,
    ) / 100
    const weight = bucket.inputTokens + bucket.cacheReadTokens + bucket.cacheCreationTokens
    weightedSum += rate * weight
    totalInput += weight
  }
  return totalInput > 0 ? (weightedSum / totalInput) * 100 : 0
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
 * 计算费用估算——cacheStyle-aware，复用 computeNetInput 的 netNew 语义。
 */
export function computeCost(
  cacheStyle: CacheStyle,
  pricing: { currency: 'CNY' | 'USD'; inputCacheHit: number; inputCacheMiss: number; output: number },
  inputTokens: number,
  cacheReadTokens: number,
  cacheCreationTokens: number,
  outputTokens: number,
): CostEstimate {
  const netNew = computeNetInput(cacheStyle, inputTokens, cacheReadTokens, cacheCreationTokens)
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
