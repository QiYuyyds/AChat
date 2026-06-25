const CODEX_BASE_URL_FORMAT_ERROR =
  'Codex Base URL 必须是完整 URL，例如 https://api.openai.com/v1'

const CODEX_RESPONSES_COMPATIBILITY_ERROR =
  'Codex SDK 需要 Codex/Responses 兼容 endpoint。DeepSeek 等仅 Chat Completions 兼容接口没有 /responses；DeepSeek 请改用 Custom（OpenAI 兼容协议）adapter，或留空使用 Codex SDK 默认 endpoint。'

export function validateCodexBaseUrl(baseUrl: string | null | undefined): string | null {
  const trimmed = baseUrl?.trim()
  if (!trimmed) return null

  let url: URL
  try {
    url = new URL(trimmed)
  } catch {
    return CODEX_BASE_URL_FORMAT_ERROR
  }

  if (isDeepSeekHost(url.hostname)) {
    return CODEX_RESPONSES_COMPATIBILITY_ERROR
  }

  return null
}

export function isCodexResponsesMissingErrorMessage(message: string): boolean {
  const lower = message.toLowerCase()
  return lower.includes('/responses') && (lower.includes('404') || lower.includes('not found'))
}

export function codexResponsesCompatibilityError(rawMessage?: string): string {
  if (!rawMessage) return CODEX_RESPONSES_COMPATIBILITY_ERROR
  return `${CODEX_RESPONSES_COMPATIBILITY_ERROR} 原始错误：${rawMessage}`
}

function isDeepSeekHost(hostname: string): boolean {
  return /(^|\.)deepseek\.com$/i.test(hostname)
}
