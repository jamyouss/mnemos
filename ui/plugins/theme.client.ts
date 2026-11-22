import { applyPrefs, parsePrefs, THEME_STORAGE } from '~/utils/theme'

// Before the first render, so a saved preset does not flash the default one.
export default defineNuxtPlugin(() => {
  applyPrefs(document.documentElement, parsePrefs(localStorage.getItem(THEME_STORAGE)))
})
