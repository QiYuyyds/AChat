/**
 * Agent 头像图标库。图标文件在 `public/agent-icons/`，avatar 字段存的是 token（如 `icon-07`）。
 * token 长度 ≤8，兼容后端 avatar 字段的 max_length=8 约束。
 */

export const AGENT_ICON_COUNT = 21

// ['icon-01', ..., 'icon-21']，token 即文件名（去掉 .png）
export const AGENT_ICON_TOKENS: string[] = Array.from(
  { length: AGENT_ICON_COUNT },
  (_, i) => `icon-${String(i + 1).padStart(2, '0')}`,
)

const ICON_TOKEN_RE = /^icon-\d{2}$/

/** avatar 是否为图标 token（而非 emoji / 首字母） */
export function isAgentIconToken(avatar: string | undefined | null): boolean {
  return !!avatar && ICON_TOKEN_RE.test(avatar)
}

/** token → 静态图标 URL */
export function agentIconUrl(token: string): string {
  return `/agent-icons/${token}.png`
}

/** 随机取一个图标 token，用于创建 Agent 时分配头像 */
export function pickRandomAgentIcon(): string {
  const idx = Math.floor(Math.random() * AGENT_ICON_TOKENS.length)
  return AGENT_ICON_TOKENS[idx]
}
