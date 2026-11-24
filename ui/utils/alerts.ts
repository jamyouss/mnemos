/** Server alerts the banner shows, minus those dismissed for this browser session. */

export const DISMISSED_ALERTS_STORAGE = 'mnemos.dismissed-alerts'

/**
 * Dismissals of alerts that are still raised. Forgetting the others is what
 * makes an alert come back when its condition clears and then returns.
 */
export function pruneDismissed(dismissed: readonly string[], currentIds: readonly string[]): string[] {
  const current = new Set(currentIds)
  return dismissed.filter((id) => current.has(id))
}

export function visibleAlerts<T extends { id: string }>(alerts: readonly T[], dismissed: readonly string[]): T[] {
  const hidden = new Set(dismissed)
  return alerts.filter((a) => !hidden.has(a.id))
}

/** Stored dismissals; anything unreadable counts as none. */
export function parseDismissed(raw: string | null): string[] {
  try {
    const value = JSON.parse(raw ?? '[]')
    return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
  } catch {
    return []
  }
}
