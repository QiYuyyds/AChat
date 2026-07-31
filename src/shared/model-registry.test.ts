import { describe, expect, it } from 'vitest'

import { EFFECTIVE_CONTEXT_CAP, getModelLimits, getModelPricing } from '@/shared/model-registry'

describe('getModelPricing', () => {
  it('returns DeepSeek V4 Flash pricing for deepseek-chat', () => {
    const pricing = getModelPricing(null, 'deepseek-chat')
    expect(pricing).not.toBeNull()
    expect(pricing!.currency).toBe('CNY')
    expect(pricing!.inputCacheHit).toBe(0.02)
    expect(pricing!.inputCacheMiss).toBe(1)
    expect(pricing!.output).toBe(2)
  })

  it('returns DeepSeek V4 Pro pricing for deepseek-reasoner', () => {
    const pricing = getModelPricing(null, 'deepseek-reasoner')
    expect(pricing).not.toBeNull()
    expect(pricing!.currency).toBe('CNY')
    expect(pricing!.inputCacheHit).toBe(0.025)
    expect(pricing!.inputCacheMiss).toBe(3)
    expect(pricing!.output).toBe(6)
  })

  it('returns DeepSeek V4 Flash pricing for deepseek-v4-flash', () => {
    const pricing = getModelPricing(null, 'deepseek-v4-flash')
    expect(pricing).not.toBeNull()
    expect(pricing!.inputCacheHit).toBe(0.02)
  })

  it('returns null for model without pricing data (graceful degradation)', () => {
    expect(getModelPricing(null, 'gpt-4o')).toBeNull()
    expect(getModelPricing(null, 'claude-opus-4-7')).toBeNull()
    expect(getModelPricing(null, 'deepseek-v4')).toBeNull() // has context but no pricing
  })

  it('returns null for unknown model id', () => {
    expect(getModelPricing(null, 'nonexistent-model')).toBeNull()
  })

  it('returns null for null/undefined model id', () => {
    expect(getModelPricing(null, null)).toBeNull()
    expect(getModelPricing(null, undefined)).toBeNull()
  })
})

describe('getModelLimits', () => {
  it('returns physical context window for DeepSeek models', () => {
    const limits = getModelLimits('deepseek', 'deepseek-chat')
    expect(limits.contextWindow).toBe(1_000_000)
  })

  it('caps effective context window to 200K for DeepSeek models', () => {
    const limits = getModelLimits('deepseek', 'deepseek-chat')
    expect(limits.effectiveContextWindow).toBe(EFFECTIVE_CONTEXT_CAP)
    expect(limits.effectiveContextWindow).toBe(200_000)
  })

  it('returns 13_000 outputReserve for all DeepSeek models', () => {
    for (const modelId of [
      'deepseek-chat',
      'deepseek-v4-flash',
      'deepseek-v4',
      'deepseek-v4-pro',
      'deepseek-reasoner',
      'deepseek-r1',
    ]) {
      const limits = getModelLimits('deepseek', modelId)
      expect(limits.outputReserve).toBe(13_000)
    }
  })

  it('effectiveContextWindow equals contextWindow when below cap', () => {
    const limits = getModelLimits('openai', 'gpt-4o')
    expect(limits.contextWindow).toBe(128_000)
    expect(limits.effectiveContextWindow).toBe(128_000)
  })
})
