import { describe, expect, it } from 'vitest'

import {
  computeCacheHitRate,
  computeCost,
  computeLastNetInput,
  computeMessageTotalTokens,
  computeNetInput,
  computeTotalTokens,
  computeWeightedCacheHitRate,
  inferCacheStyle,
  type CacheStyleBucket,
} from '@/shared/usage'

// ─── inferCacheStyle (11.6) ───────────────────────────────

describe('inferCacheStyle', () => {
  it('returns "anthropic" when cacheCreationTokens > 0', () => {
    expect(inferCacheStyle(500)).toBe('anthropic')
  })

  it('returns "deepseek" when cacheCreationTokens === 0', () => {
    expect(inferCacheStyle(0)).toBe('deepseek')
  })

  it('returns "deepseek" for negative values (defensive)', () => {
    expect(inferCacheStyle(-1)).toBe('deepseek')
  })
})

// ─── computeTotalTokens (11.5) ─────────────────────────────

describe('computeTotalTokens', () => {
  it('deepseek: input + output (cache already in input)', () => {
    expect(computeTotalTokens('deepseek', 564_000, 7_100, 0, 488_700)).toBe(571_100)
  })

  it('anthropic: input + output + cacheCreation + cacheRead', () => {
    expect(computeTotalTokens('anthropic', 9_000, 2_000, 5_000, 3_000)).toBe(19_000)
  })

  it('none: input + output (no cache)', () => {
    expect(computeTotalTokens('none', 1_000, 200, 0, 0)).toBe(1_200)
  })
})

// ─── computeMessageTotalTokens (11.5) ─────────────────────

describe('computeMessageTotalTokens', () => {
  it('deepseek: input + output (no cacheCreation in MessageUsage)', () => {
    expect(computeMessageTotalTokens('deepseek', 1_000, 200, 500)).toBe(1_200)
  })

  it('anthropic: input + output + cacheRead (no cacheCreation field)', () => {
    expect(computeMessageTotalTokens('anthropic', 1_000, 200, 500)).toBe(1_700)
  })

  it('none: input + output', () => {
    expect(computeMessageTotalTokens('none', 1_000, 200, 0)).toBe(1_200)
  })
})

// ─── computeNetInput ──────────────────────────────────────

describe('computeNetInput', () => {
  it('DeepSeek: subtracts cacheReadTokens from inputTokens', () => {
    expect(computeNetInput('deepseek', 564_000, 488_700, 0)).toBe(75_300)
  })

  it('Anthropic: adds cacheCreationTokens to inputTokens', () => {
    expect(computeNetInput('anthropic', 9_000, 0, 5_000)).toBe(14_000)
  })

  it('DeepSeek: clamps to 0 when cacheRead > input (defensive)', () => {
    expect(computeNetInput('deepseek', 100, 200, 0)).toBe(0)
  })

  it('DeepSeek: returns full input when no cache read', () => {
    expect(computeNetInput('deepseek', 1_000, 0, 0)).toBe(1_000)
  })

  it('none: returns inputTokens as-is', () => {
    expect(computeNetInput('none', 1_000, 500, 0)).toBe(1_000)
  })
})

// ─── computeLastNetInput ──────────────────────────────────

describe('computeLastNetInput', () => {
  it('DeepSeek: lastInput - lastCacheRead', () => {
    expect(computeLastNetInput('deepseek', 79_100, 70_000)).toBe(9_100)
  })

  it('Anthropic: returns lastInputTokens (input excludes cache)', () => {
    expect(computeLastNetInput('anthropic', 9_000, 5_000)).toBe(9_000)
  })

  it('DeepSeek: clamps to 0 when lastCacheRead > lastInput', () => {
    expect(computeLastNetInput('deepseek', 100, 200)).toBe(0)
  })

  it('none: returns lastInputTokens', () => {
    expect(computeLastNetInput('none', 1_000, 500)).toBe(1_000)
  })
})

// ─── computeCacheHitRate ─────────────────────────────────

describe('computeCacheHitRate', () => {
  it('deepseek: cacheRead / inputTokens', () => {
    expect(computeCacheHitRate('deepseek', 1000, 0, 500)).toBeCloseTo(50, 0)
  })

  it('anthropic: cacheRead / (input + cacheRead + cacheCreation)', () => {
    expect(computeCacheHitRate('anthropic', 1000, 500, 500)).toBeCloseTo(25, 0)
  })

  it('none: always 0', () => {
    expect(computeCacheHitRate('none', 1000, 0, 500)).toBe(0)
  })

  it('deepseek: 0 when no input', () => {
    expect(computeCacheHitRate('deepseek', 0, 0, 500)).toBe(0)
  })
})

// ─── computeWeightedCacheHitRate (11.7) ──────────────────

describe('computeWeightedCacheHitRate', () => {
  const emptyBucket: CacheStyleBucket = {
    inputTokens: 0,
    cacheReadTokens: 0,
    cacheCreationTokens: 0,
    outputTokens: 0,
  }

  it('returns 0 when all buckets empty', () => {
    expect(
      computeWeightedCacheHitRate({
        deepseek: emptyBucket,
        anthropic: emptyBucket,
        none: emptyBucket,
      }),
    ).toBe(0)
  })

  it('single deepseek bucket: same as computeCacheHitRate', () => {
    const buckets = {
      deepseek: { inputTokens: 1000, cacheReadTokens: 500, cacheCreationTokens: 0, outputTokens: 200 },
      anthropic: emptyBucket,
      none: emptyBucket,
    }
    expect(computeWeightedCacheHitRate(buckets)).toBeCloseTo(50, 0)
  })

  it('mixed deepseek + anthropic: weighted average', () => {
    // deepseek: 1000 input, 500 cacheRead → 50% hit rate, weight = 1500
    // anthropic: 1000 input, 0 cacheCreation, 500 cacheRead → 33.3% hit rate, weight = 1500
    // weighted = (0.5 * 1500 + 0.333 * 1500) / 3000 = (750 + 500) / 3000 = 41.67%
    const buckets = {
      deepseek: { inputTokens: 1000, cacheReadTokens: 500, cacheCreationTokens: 0, outputTokens: 200 },
      anthropic: { inputTokens: 1000, cacheReadTokens: 500, cacheCreationTokens: 0, outputTokens: 200 },
      none: emptyBucket,
    }
    const result = computeWeightedCacheHitRate(buckets)
    expect(result).toBeCloseTo(41.67, 0)
  })

  it('none bucket contributes 0 hit rate but adds to weight', () => {
    // deepseek: 1000 input, 500 cacheRead → 50%, weight = 1500
    // none: 1000 input, 0 cacheRead → 0%, weight = 1000
    // weighted = (0.5 * 1500 + 0 * 1000) / 2500 = 750 / 2500 = 30%
    const buckets = {
      deepseek: { inputTokens: 1000, cacheReadTokens: 500, cacheCreationTokens: 0, outputTokens: 200 },
      anthropic: emptyBucket,
      none: { inputTokens: 1000, cacheReadTokens: 0, cacheCreationTokens: 0, outputTokens: 200 },
    }
    const result = computeWeightedCacheHitRate(buckets)
    expect(result).toBeCloseTo(30, 0)
  })
})

// ─── computeCost ──────────────────────────────────────────

describe('computeCost', () => {
  const flashPricing = {
    currency: 'CNY' as const,
    inputCacheHit: 0.02,
    inputCacheMiss: 1,
    output: 2,
  }

  it('DeepSeek v4-flash: actualCost ≈ ¥0.10 with real data', () => {
    const result = computeCost('deepseek', flashPricing, 564_000, 488_700, 0, 7_100)
    expect(result.actualCost).toBeCloseTo(0.0993, 3)
    expect(result.currency).toBe('CNY')
  })

  it('DeepSeek v4-flash: noCacheCost ≈ ¥0.58', () => {
    const result = computeCost('deepseek', flashPricing, 564_000, 488_700, 0, 7_100)
    expect(result.noCacheCost).toBeCloseTo(0.5782, 3)
  })

  it('DeepSeek v4-flash: savings ≈ ¥0.48 (83%)', () => {
    const result = computeCost('deepseek', flashPricing, 564_000, 488_700, 0, 7_100)
    expect(result.savings).toBeCloseTo(0.4789, 3)
    expect(result.savingsPct).toBeCloseTo(83, 0)
  })

  it('Anthropic-style: includes cacheCreation in netNew', () => {
    const anthropicPricing = {
      currency: 'USD' as const,
      inputCacheHit: 0.3,
      inputCacheMiss: 3,
      output: 15,
    }
    const result = computeCost('anthropic', anthropicPricing, 9_000, 3_000, 5_000, 2_000)
    expect(result.actualCost).toBeCloseTo(0.0729, 4)
    expect(result.currency).toBe('USD')
  })

  it('none style: no cache, cost = input + output', () => {
    const result = computeCost('none', flashPricing, 1_000, 0, 0, 500)
    // netNew = 1000, actualCost = 0 + 1000*1 + 500*2 (per 1M) = 0.001 + 0.001 = 0.002
    expect(result.actualCost).toBeCloseTo(0.002, 5)
  })

  it('zero output → cost is cache + netNew only', () => {
    const result = computeCost('deepseek', flashPricing, 1_000, 500, 0, 0)
    expect(result.actualCost).toBeCloseTo(0.00001 + 0.0005, 5)
  })
})
