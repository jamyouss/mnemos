import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  resolve: {
    // Nuxt resolves `~` to the project root; vitest runs outside Nuxt, so it
    // needs the same alias to load the shared types.
    alias: { '~': fileURLToPath(new URL('./', import.meta.url)) },
  },
  test: { environment: 'node', include: ['utils/**/*.test.ts'] },
})
