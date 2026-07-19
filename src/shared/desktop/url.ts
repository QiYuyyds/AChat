/**
 * Loopback URL helpers for desktop (page host vs engine host).
 *
 * Dev shell often loads UI at http://localhost:3000 while the engine reports
 * http://127.0.0.1:<port>. Those are different browser origins; mismatch breaks
 * startsWith checks for engine token attachment and EventSource assumptions.
 */

/** Align loopback host with the page (`localhost` vs `127.0.0.1`). */
export function alignLoopbackHost(base: string): string {
  if (typeof window === 'undefined') return stripTrailingSlash(base)
  try {
    const u = new URL(base)
    const pageHost = window.location.hostname
    if (
      (u.hostname === '127.0.0.1' && pageHost === 'localhost') ||
      (u.hostname === 'localhost' && pageHost === '127.0.0.1')
    ) {
      u.hostname = pageHost
    }
    return u.origin
  } catch {
    return stripTrailingSlash(base)
  }
}

/** True when host is a loopback name (IPv4 / IPv6 / localhost). */
export function isLoopbackHostname(hostname: string): boolean {
  const h = hostname.toLowerCase()
  return h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '[::1]'
}

/**
 * Compare two absolute origins/bases as the same loopback service.
 * Treats localhost and 127.0.0.1 as equivalent when ports match.
 */
export function sameLoopbackService(a: string, b: string): boolean {
  try {
    const ua = new URL(a.includes('://') ? a : `http://${a}`)
    const ub = new URL(b.includes('://') ? b : `http://${b}`)
    if (ua.port !== ub.port) return false
    if (ua.hostname === ub.hostname) return true
    return isLoopbackHostname(ua.hostname) && isLoopbackHostname(ub.hostname)
  } catch {
    return false
  }
}

/** True when `url` targets the given engine base (loopback-aware). */
export function urlTargetsEngine(url: string, engineBase: string): boolean {
  if (!engineBase) return false
  const base = stripTrailingSlash(engineBase)
  if (url.startsWith(base)) return true
  // Relative engine paths (same-origin packaged UI)
  if (url.startsWith('/api') || url.startsWith('/health')) return true
  try {
    const absolute = new URL(url, base)
    return sameLoopbackService(absolute.origin, base)
  } catch {
    return false
  }
}

function stripTrailingSlash(s: string): string {
  return s.replace(/\/$/, '')
}
