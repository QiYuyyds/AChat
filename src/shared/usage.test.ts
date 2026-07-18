import { describe, expect, it } from 'vitest'

import { computeCost, computeLastNetInput, computeNetInput } from '@/shared/usage'

describe('computeNetInput', () => {
  it('DeepSeek: subtracts cacheReadTokens from inputTokens (input includes cache hit)', () => {
    // 564k input, 488.7k cache read → 75.3k net new
    expect(computeNetInput(564_000, 488_700, 0)).toBe(75_300)
  })

  it('Anthropic: adds cacheCreationTokens to inputTokens (input excludes cache creation)', () => {
    // 9k input, 5k cache creation → 14k net new
    expect(computeNetInput(9_000, 0, 5_000)).toBe(14_000)
  })

  it('DeepSeek: clamps to 0 when cacheRead > input (defensive)', () => {
    expect(computeNetInput(100, 200, 0)).toBe(0)
  })

  it('DeepSeek: returns full input when no cache read', () => {
    expect(computeNetInput(1_000, 0, 0)).toBe(1_000)
  })
})

describe('computeLastNetInput', () => {
  it('DeepSeek: lastInput - lastCacheRead (single-turn snapshot)', () => {
    // 79.1k last input, 70k last cache read → 9.1k net new
    expect(computeLastNetInput(79_100, 70_000, 0)).toBe(9_100)
  })

  it('Anthropic: returns lastInputTokens as approximation (no lastCacheCreation)', () => {
    // Anthropic: lastInput excludes cache, so all input is "new"
    expect(computeLastNetInput(9_000, 5_000, 5_000)).toBe(9_000)
  })

  it('DeepSeek: clamps to 0 when lastCacheRead > lastInput', () => {
    expect(computeLastNetInput(100, 200, 0)).toBe(0)
  })
})

describe('computeCost', () => {
  // DeepSeek V4 Flash pricing (per 1M tokens, CNY):
  //   inputCacheHit: 0.02, inputCacheMiss: 1, output: 2
  const flashPricing = {
    currency: 'CNY' as const,
    inputCacheHit: 0.02,
    inputCacheMiss: 1,
    output: 2,
  }

  it('DeepSeek v4-flash: actualCost ≈ ¥0.10 with real data', () => {
    // input=564k, cacheRead=488.7k, output=7.1k
    // netNew = 564k - 488.7k = 75.3k
    // actualCost = 488.7k×0.02 + 75.3k×1 + 7.1k×2 (per 1M)
    //            = 0.009774 + 0.0753 + 0.0142 ≈ 0.0993 → ¥0.10
    const result = computeCost(flashPricing, 564_000, 488_700, 0, 7_100)
    expect(result.actualCost).toBeCloseTo(0.0993, 3)
    expect(result.currency).toBe('CNY')
  })

  it('DeepSeek v4-flash: noCacheCost ≈ ¥0.58 (all input at miss price)', () => {
    // noCacheCost = (488.7k + 75.3k)×1 + 7.1k×2 (per 1M)
    //             = 0.564 + 0.0142 = 0.5782 → ¥0.58
    const result = computeCost(flashPricing, 564_000, 488_700, 0, 7_100)
    expect(result.noCacheCost).toBeCloseTo(0.5782, 3)
  })

  it('DeepSeek v4-flash: savings ≈ ¥0.48 (83%)', () => {
    const result = computeCost(flashPricing, 564_000, 488_700, 0, 7_100)
    expect(result.savings).toBeCloseTo(0.4789, 3)
    expect(result.savingsPct).toBeCloseTo(83, 0)
  })

  it('DeepSeek v4-pro: uses pro pricing (higher rates)', () => {
    const proPricing = {
      currency: 'CNY' as const,
      inputCacheHit: 0.025,
      inputCacheMiss: 3,
      output: 6,
    }
    // Same token usage → higher cost
    const result = computeCost(proPricing, 564_000, 488_700, 0, 7_100)
    // actualCost = 488.7k×0.025 + 75.3k×3 + 7.1k×6 (per 1M)
    //            = 0.0122 + 0.2259 + 0.0426 = 0.2807
    expect(result.actualCost).toBeCloseTo(0.2807, 3)
  })

  it('Anthropic-style: includes cacheCreation in netNew', () => {
    const anthropicPricing = {
      currency: 'USD' as const,
      inputCacheHit: 0.3,
      inputCacheMiss: 3,
      output: 15,
    }
    // input=9k, cacheCreation=5k, cacheRead=3k, output=2k
    // netNew = 9k + 5k = 14k (Anthropic: input + cacheCreation)
    // actualCost = 3k×0.3 + 14k×3 + 2k×15 (per 1M)
    //            = 0.0009 + 0.042 + 0.03 = 0.0729
    const result = computeCost(anthropicPricing, 9_000, 3_000, 5_000, 2_000)
    expect(result.actualCost).toBeCloseTo(0.0729, 4)
    expect(result.currency).toBe('USD')
  })

  it('zero output → cost is cache + netNew only', () => {
    const result = computeCost(flashPricing, 1_000, 500, 0, 0)
    // netNew = 500, actualCost = 500×0.02 + 500×1 + 0 = 0.01 + 0.5 = 0.51 (per 1M)
    expect(result.actualCost).toBeCloseTo(0.00001 + 0.0005, 5)
  })
})
