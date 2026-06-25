/* Shared time formatting. Centralized so every screen renders timestamps identically. */

export function formatTime(ts: number): string {
  return new Date(ts).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

/** Human "x 秒/分钟/小时前" for the sync caption; falls back to absolute time past a day. */
export function formatRelative(ts: number): string {
  const diffMs = Math.max(0, Date.now() - ts)
  const sec = Math.round(diffMs / 1000)
  if (sec < 5) return "刚刚"
  if (sec < 60) return `${sec} 秒前`
  const min = Math.round(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr} 小时前`
  return formatTime(ts)
}
