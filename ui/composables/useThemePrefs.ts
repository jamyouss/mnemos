import { applyPrefs, parsePrefs, THEME_STORAGE, type ThemePrefs } from '~/utils/theme'

export function useThemePrefs() {
  const prefs = useState<ThemePrefs>('theme-prefs', () => parsePrefs(localStorage.getItem(THEME_STORAGE)))

  function set(patch: Partial<ThemePrefs>) {
    prefs.value = { ...prefs.value, ...patch }
    localStorage.setItem(THEME_STORAGE, JSON.stringify(prefs.value))
    applyPrefs(document.documentElement, prefs.value)
  }

  return { prefs, set }
}
