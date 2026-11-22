/**
 * Per-browser appearance: accent preset and corner radius.
 *
 * Light/dark lives in @nuxtjs/color-mode; this covers the rest. `null` means
 * "use the instance theme" (config/theme.css), so a deployer's branding is
 * what a fresh browser sees.
 */

export const THEME_STORAGE = 'mnemos.theme'

/** Swatches mirror the light `--primary` of each preset in presets.css. */
export const PRESETS = [
  { id: 'blue', label: 'Blue', swatch: 'oklch(0.546 0.245 262.881)' },
  { id: 'violet', label: 'Violet', swatch: 'oklch(0.541 0.281 293.009)' },
  { id: 'green', label: 'Green', swatch: 'oklch(0.627 0.194 149.214)' },
  { id: 'teal', label: 'Teal', swatch: 'oklch(0.6 0.118 184.704)' },
  { id: 'orange', label: 'Orange', swatch: 'oklch(0.646 0.222 41.116)' },
  { id: 'amber', label: 'Amber', swatch: 'oklch(0.769 0.188 70.08)' },
  { id: 'rose', label: 'Rose', swatch: 'oklch(0.586 0.253 17.585)' },
] as const

export const RADII = [0, 0.3, 0.5, 0.625, 0.75, 1] as const

export type PresetId = (typeof PRESETS)[number]['id']

export interface ThemePrefs {
  preset: PresetId | null
  radius: number | null
}

export const DEFAULT_PREFS: ThemePrefs = { preset: null, radius: null }

/** Tolerant on purpose: a stale or hand-edited value must not break the app. */
export function parsePrefs(raw: string | null): ThemePrefs {
  if (!raw) return { ...DEFAULT_PREFS }
  try {
    const data = JSON.parse(raw) as Partial<ThemePrefs>
    const preset = PRESETS.some((p) => p.id === data.preset) ? (data.preset as PresetId) : null
    const radius = typeof data.radius === 'number' && (RADII as readonly number[]).includes(data.radius) ? data.radius : null
    return { preset, radius }
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

/** Apply to <html>: a preset is an attribute, a radius an inline variable. */
export function applyPrefs(root: HTMLElement, prefs: ThemePrefs): void {
  if (prefs.preset) root.dataset.theme = prefs.preset
  else delete root.dataset.theme
  if (prefs.radius !== null) root.style.setProperty('--radius', `${prefs.radius}rem`)
  else root.style.removeProperty('--radius')
}
