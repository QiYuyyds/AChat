/**
 * 统一的时间格式化函数。
 * - < 1000ms: "832ms"
 * - < 60000ms: "12.3s"
 * - >= 60000ms: "3m15s"
 */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const min = Math.floor(ms / 60_000)
  const sec = Math.floor((ms % 60_000) / 1000)
  return `${min}m${sec}s`
}
