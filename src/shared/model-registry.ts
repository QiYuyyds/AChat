/**
 * 模型上下文窗口与输出预留 token 数。
 *
 * Phase D 自动 token 预算用：`agent-runner` 计算 historyBudget = contextWindow - outputReserve
 * - estimate(systemPrompt) - estimate(currentUser) - safetyMargin。UI 的 ContextUsageIndicator
 * 用同一份表展示「上下文 X / Y」。
 *
 * 详见 specs/13-conversation-context.md「Token 预算」节。
 *
 * 维护说明：模型常变（厂商上下文窗常扩），表里都是相对保守的下限。新模型 id 走 provider fallback，
 * 没在表里也跑得起来，只是预算保守一点。要更准就把 id 加进 KNOWN_MODELS。
 */

import type { ModelProvider } from './types'

/** 单位：tokens。整个会话能装进 LLM 一次调用的总 token 上限（input + output）。 */
const PROVIDER_FALLBACK_CONTEXT: Record<ModelProvider, number> = {
  anthropic: 200_000,
  openai: 128_000,
  deepseek: 64_000,
  'volcano-ark': 32_000,
  'openai-compatible': 128_000,
}

/** 默认给输出留的 token。reasoning 模型实际上需要更多（thinking 也吃 token），但 4K 兜底足够。 */
const DEFAULT_OUTPUT_RESERVE = 4096

const KNOWN_MODELS: Record<string, { context: number; outputReserve?: number }> = {
  // DeepSeek
  'deepseek-chat': { context: 64_000 },
  'deepseek-v4-flash': { context: 64_000 },
  'deepseek-v4': { context: 64_000 },
  'deepseek-reasoner': { context: 128_000, outputReserve: 16_384 }, // R1 系列 thinking 吃 token
  'deepseek-r1': { context: 128_000, outputReserve: 16_384 },

  // OpenAI
  'gpt-4o': { context: 128_000 },
  'gpt-4o-mini': { context: 128_000 },
  'gpt-4-turbo': { context: 128_000 },
  'gpt-4': { context: 8192 },
  'gpt-3.5-turbo': { context: 16_385 },
  'o1': { context: 200_000, outputReserve: 32_768 },
  'o1-mini': { context: 128_000, outputReserve: 16_384 },

  // Anthropic
  'claude-opus-4-7': { context: 200_000 },
  'claude-opus-4-7[1m]': { context: 1_000_000 },
  'claude-sonnet-4-6': { context: 200_000 },
  'claude-opus-4-6': { context: 200_000 },
  'claude-opus-4-5': { context: 200_000 },
  'claude-sonnet-4-5': { context: 200_000 },
  'claude-3-5-sonnet-latest': { context: 200_000 },
  'claude-haiku-4-5-20251001': { context: 200_000 },

  // Volcano Ark / 豆包
  'doubao-seed-2-0-lite-260428': { context: 32_000 },
  'doubao-1-5-pro-256k': { context: 256_000 },
  'doubao-pro-128k': { context: 128_000 },
}

export interface ModelLimits {
  /** 总上下文窗口（tokens） */
  contextWindow: number
  /** 给输出预留的 tokens；input + output 不能超 contextWindow */
  outputReserve: number
}

export function getModelLimits(
  provider: ModelProvider | null | undefined,
  modelId: string | null | undefined,
): ModelLimits {
  if (modelId && KNOWN_MODELS[modelId]) {
    const m = KNOWN_MODELS[modelId]
    return {
      contextWindow: m.context,
      outputReserve: m.outputReserve ?? DEFAULT_OUTPUT_RESERVE,
    }
  }
  // Provider fallback
  if (provider && PROVIDER_FALLBACK_CONTEXT[provider]) {
    return {
      contextWindow: PROVIDER_FALLBACK_CONTEXT[provider],
      outputReserve: DEFAULT_OUTPUT_RESERVE,
    }
  }
  // 最终兜底（也是 ClaudeCode adapter 用的，因为它没有 modelProvider 字段）
  return { contextWindow: 200_000, outputReserve: DEFAULT_OUTPUT_RESERVE }
}

/** 粗粒度 token 估算：4 字符 ≈ 1 token。中英混合实测误差 10-20% 量级，对预算决策够用。 */
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4)
}
