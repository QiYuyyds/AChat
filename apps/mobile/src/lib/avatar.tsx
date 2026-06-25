/* Shared avatar helpers + badge. Conversation list rows and message rows derive their
   initials and tone color from the same logic so avatars stay consistent. */

export function hashTone(key: string): number {
  let hash = 0
  for (const char of key) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  }
  return hash % 7
}

export function avatarInitials(name: string, fallback = "AG"): string {
  const normalized = name.trim()
  if (!normalized) return fallback

  const asciiWords = normalized.match(/[a-zA-Z0-9]+/g)
  if (asciiWords && asciiWords.length > 0) {
    const first = asciiWords[0]?.[0] ?? ""
    const second = asciiWords.length > 1 ? asciiWords[1]?.[0] : asciiWords[0]?.[1]
    return `${first}${second ?? ""}`.toUpperCase()
  }

  const chars = Array.from(normalized).filter((char) => /\p{Letter}|\p{Number}/u.test(char))
  return chars.slice(0, 2).join("").toUpperCase() || fallback
}

export function conversationAvatarLabel(title: string, mode: "single" | "group"): string {
  return avatarInitials(title, mode === "group" ? "GR" : "DM")
}

export function AvatarBadge({
  label,
  toneKey,
  className,
}: {
  label: string
  toneKey: string
  className?: string
}) {
  return <div className={`${className ?? ""} avatar-tone-${hashTone(toneKey)}`}>{label}</div>
}
