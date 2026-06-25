import type { ModelProvider } from './types'

export const OPENAI_COMPATIBLE_BASE_URL_REQUIRED_ERROR =
  'OpenAI-compatible provider 必须填写 Chat Completions Base URL，例如 https://dashscope.aliyuncs.com/compatible-mode/v1'

export const OPENAI_COMPATIBLE_BASE_URL_FORMAT_ERROR =
  'OpenAI-compatible Base URL 必须是完整 URL，例如 https://dashscope.aliyuncs.com/compatible-mode/v1'

export const OPENAI_COMPATIBLE_API_KEY_REQUIRED_ERROR =
  'OpenAI-compatible provider 必须为该 Agent 单独填写 API Key'

export function validateOpenAICompatibleBaseUrl(
  provider: ModelProvider | null | undefined,
  baseUrl: string | null | undefined,
): string | null {
  if (provider !== 'openai-compatible') return null

  const trimmed = baseUrl?.trim()
  if (!trimmed) return OPENAI_COMPATIBLE_BASE_URL_REQUIRED_ERROR

  try {
    new URL(trimmed)
  } catch {
    return OPENAI_COMPATIBLE_BASE_URL_FORMAT_ERROR
  }

  return null
}

export function validateOpenAICompatibleApiKey(
  provider: ModelProvider | null | undefined,
  apiKey: string | null | undefined,
): string | null {
  if (provider !== 'openai-compatible') return null
  return apiKey?.trim() ? null : OPENAI_COMPATIBLE_API_KEY_REQUIRED_ERROR
}
